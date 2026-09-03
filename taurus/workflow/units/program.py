"""S2-04 Program Management Adapter: install / upgrade / start / stop / restart / remove.

Reuses taurus.models.ProgramCommand DB rows, sends program operation commands to hosts
via Supervisor RPC or TaurusClient. Asynchronous execution, dispatch -> RUNNING, poll checks DB status,
cancel marks as ABORTED.

Error codes:
  E0101 params field type error
  E0201 action not in allowed set
  E0202 program_name is empty
  E0203 upgrade requires target_version
  E0301 specified host does not exist
  E0302 concurrency conflict: same host + action already has running command
  E3003 ProgramCommand persistence failed
  E4001 execution failed (read specific reason from result_message)
"""
from __future__ import annotations

from typing import Any

from django.utils import timezone

from ..engine.base_adapter import ExecutableUnit
from ..engine.context import WorkflowContext
from ..engine.registry import register_unit_adapter
from ..engine.schemas import (
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    STATUS_CANCELLED,
    UnitOutput,
    ValidationResult,
)


_ALLOWED_ACTIONS = {"install", "upgrade", "start", "stop", "restart", "remove"}


def _validate_program_params(params: dict[str, Any], r: ValidationResult) -> None:
    """S2-04 Program Management Adapter static check."""
    # First align frontend timeout fields
    for src in ("timeout", "timeout_sec"):
        if src in params and "timeout_seconds" not in params:
            val = params[src]
            if isinstance(val, bool):
                continue
            if isinstance(val, (int, float)):
                params["timeout_seconds"] = int(val)
            elif isinstance(val, str) and val.strip():
                try:
                    params["timeout_seconds"] = int(val.strip())
                except ValueError:
                    pass

    action = params.get("action")
    if action not in _ALLOWED_ACTIONS:
        r.add_error(
            "/params/action",
            f"E0201: action must be in {sorted(_ALLOWED_ACTIONS)}, got {action!r}",
        )

    program_name = params.get("program_name")
    if not isinstance(program_name, str) or not program_name.strip():
        r.add_error("/params/program_name", "E0202: program_name must be a non-empty string")

    if action == "upgrade":
        target_version = params.get("target_version")
        if not isinstance(target_version, str) or not target_version.strip():
            r.add_error(
                "/params/target_version",
                "E0203: upgrade action must specify target_version",
            )

    timeout_seconds = params.get("timeout_seconds")
    if timeout_seconds is not None:
        if not isinstance(timeout_seconds, int) or timeout_seconds <= 0 or timeout_seconds > 86400:
            r.add_error(
                "/params/timeout_seconds",
                "E0101: timeout_seconds must be an integer in (0, 86400]",
            )

    config = params.get("config")
    if config is not None and not isinstance(config, dict):
        r.add_error("/params/config", "E0101: config must be a dict")


@register_unit_adapter("program")
class ProgramAdapter(ExecutableUnit):
    """Program Management Adapter."""

    node_type = "program"
    display_name = "Program Management"
    category = "program"
    requires_host = True
    is_asynchronous_human = False

    def validate_config(self, params, *, secrets_mask=None):
        r = ValidationResult.success()
        if not isinstance(params, dict):
            r.add_error("/params", "E0101: params must be a dict")
            return r
        _validate_program_params(params, r)
        return r

    def validate_and_render(
        self,
        params: dict[str, Any],
        context: WorkflowContext,
        *,
        secrets_mask: list[str] | None = None,
    ) -> dict[str, Any]:
        return context.render_structure(params)

    # ---- Execution interface ----
    def dispatch(self, cfg) -> UnitOutput:
        """Creates ProgramCommand row and attempts to dispatch. dispatched=True -> RUNNING."""
        from taurus.models import Host, ProgramCommand

        host_id = cfg.host_id
        if host_id is None:
            return UnitOutput(
                status=STATUS_FAILED,
                error_message="E0301: program adapter requires host_id (this node requires_host=True)",
                exit_code=1,
            )

        params = cfg.params
        action = params["action"]
        program_name = params["program_name"]
        target_version = params.get("target_version")
        program_config = params.get("config") or {}

        try:
            host = Host.objects.get(pk=host_id)
        except Host.DoesNotExist:
            return UnitOutput(
                status=STATUS_FAILED,
                error_message=f"E0301: Host id={host_id} does not exist",
                exit_code=1,
            )

        try:
            cmd = ProgramCommand.objects.create(
                host=host,
                program_name=program_name,
                action=action,
                target_version=target_version,
                config=program_config,
                status=0,
                dispatched=False,
                max_retries=params.get("max_retries", 3),
                creator_id=cfg.user_id,
                modifier=str(cfg.user_id),
                dept_belong_id=getattr(cfg, "dept_belong_id", None) or getattr(host, "dept_belong_id", None),
            )
        except Exception as exc:  # noqa: BLE001
            return UnitOutput(
                status=STATUS_FAILED,
                error_message=f"E3003: Failed to create ProgramCommand: {exc}",
                exit_code=1,
            )

        # Trigger dispatch (reuses TaurusClient RPC, if unavailable only marks dispatched=True,
        # actual supervisor polling will pick it up)
        try:
            self._try_dispatch_rpc(cmd, host)
            cmd.dispatched = True
            cmd.dispatched_at = timezone.now()
            cmd.save(update_fields=["dispatched", "dispatched_at"])
        except Exception:
            cmd.dispatched = True
            cmd.dispatched_at = timezone.now()
            cmd.status = 1
            cmd.save(update_fields=["dispatched", "dispatched_at", "status"])

        adapter_state = {"program_command_id": cmd.pk}
        return UnitOutput(
            status=STATUS_RUNNING,
            adapter_state=adapter_state,
            output={
                "program_command_id": cmd.pk,
                "host_id": host_id,
                "action": action,
                "program_name": program_name,
            },
        )

    def poll(self, cfg, adapter_state) -> UnitOutput:
        from taurus.models import ProgramCommand

        if not adapter_state:
            return UnitOutput(
                status=STATUS_FAILED,
                error_message="E4001: adapter_state missing, cannot locate ProgramCommand",
                exit_code=1,
            )
        cmd_id = adapter_state.get("program_command_id")
        if cmd_id is None:
            return UnitOutput(
                status=STATUS_FAILED,
                error_message="E4001: adapter_state missing program_command_id",
                exit_code=1,
            )
        try:
            cmd = ProgramCommand.objects.get(pk=cmd_id)
        except ProgramCommand.DoesNotExist:
            return UnitOutput(
                status=STATUS_FAILED,
                error_message=f"E4001: ProgramCommand id={cmd_id} has been deleted",
                exit_code=1,
            )
        return self._terminal_output_from_cmd(cmd)

    def cancel(self, cfg, adapter_state) -> UnitOutput:
        from taurus.models import ProgramCommand

        if not adapter_state:
            return UnitOutput(
                status=STATUS_SUCCESS,
                output={"cancelled": False, "reason": "adapter_state missing, cannot locate row, ignoring cancel"},
            )
        cmd_id = adapter_state.get("program_command_id")
        if cmd_id is None:
            return UnitOutput(
                status=STATUS_SUCCESS,
                output={"cancelled": False, "reason": "adapter_state missing program_command_id, ignoring cancel"},
            )
        try:
            cmd = ProgramCommand.objects.get(pk=cmd_id)
        except ProgramCommand.DoesNotExist:
            return UnitOutput(
                status=STATUS_SUCCESS,
                output={"cancelled": False, "reason": f"ProgramCommand id={cmd_id} deleted"},
            )

        if cmd.status in (2, 3):
            return UnitOutput(
                status=STATUS_SUCCESS if cmd.status == 2 else STATUS_FAILED,
                output={"cancelled": False, "reason": f"Already terminal, status={cmd.status}"},
                exit_code=None if cmd.status == 2 else 1,
                summary=cmd.result_message,
            )

        # Mark running command as Failed and record ABORTED
        cmd.status = 3
        cmd.result_message = (cmd.result_message or "") + "\n[ABORTED] Workflow cancelled"
        cmd.executed_at = cmd.executed_at or timezone.now()
        cmd.save(update_fields=["status", "result_message", "executed_at"])
        return UnitOutput(
            status=STATUS_CANCELLED,
            output={"program_command_id": cmd.pk, "cancelled": True},
            exit_code=-1,
            summary="Program management command cancelled",
        )

    # ---- internal methods ----
    @staticmethod
    def _try_dispatch_rpc(cmd, host) -> None:
        """Attempt to push ProgramCommand to Host via TaurusClient.

        If client is unavailable (not configured / supervisor offline / modules missing),
        method silently returns; the Supervisor will pick up pending commands via normal polling.
        """
        try:
            from django.conf import settings
            from taurus.integration import TaurusClient

            client = TaurusClient()
            # Supervisor pull mode: push_program_command RPC is optional
            if hasattr(client, "push_program_command"):
                client.push_program_command(
                    host_id=str(host.pk),
                    command_id=str(cmd.pk),
                    action=cmd.action,
                    program_name=cmd.program_name,
                    target_version=cmd.target_version,
                    config=cmd.config,
                )
        except Exception:
            return

    @staticmethod
    def _terminal_output_from_cmd(cmd) -> UnitOutput:
        """ProgramCommand.status -> UnitOutput."""
        # STATUS_CHOICES: 0=PENDING 1=RUNNING 2=SUCCESS 3=FAILED
        if cmd.status == 0:
            return UnitOutput(
                status=STATUS_RUNNING,
                output={
                    "program_command_id": cmd.pk,
                    "phase": "waiting_dispatch",
                },
            )
        if cmd.status == 1:
            return UnitOutput(
                status=STATUS_RUNNING,
                output={
                    "program_command_id": cmd.pk,
                    "phase": "executing",
                    "dispatched_at": cmd.dispatched_at.isoformat() if cmd.dispatched_at else None,
                },
            )
        if cmd.status == 2:
            return UnitOutput(
                status=STATUS_SUCCESS,
                output={
                    "program_command_id": cmd.pk,
                    "executed_at": cmd.executed_at.isoformat() if cmd.executed_at else None,
                    "result_message": cmd.result_message or "",
                },
                exit_code=0,
                summary=cmd.result_message,
            )
        # status == 3 FAILED
        msg = cmd.result_message or "ProgramCommand execution failed (no result_message)"
        return UnitOutput(
            status=STATUS_FAILED,
            error_message=f"E4001: {msg}",
            output={
                "program_command_id": cmd.pk,
                "executed_at": cmd.executed_at.isoformat() if cmd.executed_at else None,
                "result_message": msg,
            },
            exit_code=1,
            summary=msg,
        )