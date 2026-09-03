"""S2-06 Child Workflow Adapter: sub_workflow.

Asynchronous node (requires_host=False, is_asynchronous_human=False),
dispatch triggers a child WorkflowExecution (recursively calls WorkflowRunner.trigger_workflow),
poll queries child execution status, cancel cancels child execution.

Error code convention:
- E0601: sub_workflow_id not specified (child workflow ID)
- E0602: child workflow does not exist or DAG version not released
- E0603: trigger child workflow failed (runner threw exception)
- E0604: adapter_state missing or child execution record does not exist
- E0410: child workflow nesting depth exceeds limit (max 3 levels)
- E6001: child workflow execution failed
- E6002: child workflow execution cancelled

Output fields (when poll reaches terminal state):
- sub_execution_id: child workflow execution ID
- sub_status: child workflow execution status code
- sub_outputs: child workflow per-node output snapshot ({node_key: {status, output, exit_code}})
- start_time / end_time: child workflow start and end time
"""
from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from taurus.workflow.engine.base_adapter import ExecutableUnit
from taurus.workflow.engine.context import WorkflowContext
from taurus.workflow.engine.registry import register_unit_adapter
from taurus.workflow.engine.schemas import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    RenderedNodeConfig,
    UnitOutput,
    ValidationResult,
)

logger = logging.getLogger(__name__)

MAX_NESTING_DEPTH = 3


@register_unit_adapter("sub_workflow")
class SubWorkflowAdapter(ExecutableUnit):
    """Child workflow adapter: triggers a child workflow and waits for its completion."""

    node_type = "sub_workflow"
    display_name = "Sub Workflow"
    requires_host = False
    is_asynchronous_human = False

    # -------- 基础interface:Check + Render --------
    def validate_config(self, params: dict, *, secrets_mask=None) -> ValidationResult:
        r = ValidationResult()
        sub_id = params.get("sub_workflow_id")
        if sub_id in (None, "", 0):
            r.add_error(
                "/params/sub_workflow_id",
                "E0601: sub_workflow_id is required",
            )

        # trigger_params Optional, 但mustYes dict
        tp = params.get("trigger_params")
        if tp is not None and not isinstance(tp, dict):
            r.add_error(
                "/params/trigger_params",
                "E0201: trigger_params must be dict",
            )

        # fail_strategy Optional
        fs = params.get("fail_strategy")
        if fs is not None and fs not in {"fail_fast", "continue"}:
            r.add_error(
                "/params/fail_strategy",
                "E0201: fail_strategy must be in {fail_fast, continue}",
            )

        return r

    def validate_and_render(
        self,
        params: dict[str, Any],
        context: WorkflowContext,
        *,
        secrets_mask: list[str] | None = None,
    ) -> dict[str, Any]:
        vr = self.validate_config(params, secrets_mask=secrets_mask)
        if not vr.ok:
            raise ValueError(f"sub_workflow validation failed: {vr.errors}")
        return context.render_structure(params)

    # -------- dispatch:TriggerchildWorkflow --------
    def dispatch(self, cfg: RenderedNodeConfig) -> UnitOutput:
        params: dict = cfg.params or {}
        sub_id = params.get("sub_workflow_id")
        if sub_id in (None, "", 0):
            return UnitOutput(
                status=STATUS_FAILED,
                error_message="E0601: sub_workflow_id not specified",
                exit_code=1,
            )

        try:
            from taurus.models import Workflow
            sub_wf = Workflow.objects.get(pk=sub_id)
        except Workflow.DoesNotExist:
            return UnitOutput(
                status=STATUS_FAILED,
                error_message=f"E0602: Sub-workflow id={sub_id} not found",
                exit_code=1,
            )

        if sub_wf.dag_published_version_id is None:
            return UnitOutput(
                status=STATUS_FAILED,
                error_message=f"E0602: Sub-workflow id={sub_id} not published",
                exit_code=1,
            )

        # Idempotency:同一 dispatch_id 已有childExecution则复用
        existing_sub_exec_id = self._find_existing_sub_execution(cfg.dispatch_id)
        if existing_sub_exec_id is not None:
            return UnitOutput(
                status=STATUS_RUNNING,
                adapter_state={"sub_execution_id": existing_sub_exec_id},
                output={"sub_workflow_id": sub_id, "sub_execution_id": existing_sub_exec_id},
                summary=f"Reusing existing sub-workflow exec id={existing_sub_exec_id}",
            )

        # 嵌套深度detect:从parentExecution的 trigger_params readcurrent深度
        current_depth = self._compute_nesting_depth(cfg.execution_id)
        if current_depth > MAX_NESTING_DEPTH:
            return UnitOutput(
                status=STATUS_FAILED,
                error_message=(
                    f"E0410: Sub-workflow nesting depth limit exceeded"
                    f"(max {MAX_NESTING_DEPTH}, current {current_depth})"
                ),
                exit_code=1,
            )

        # TriggerchildWorkflow
        try:
            from taurus.workflow.engine.runner import WorkflowRunner
            runner = WorkflowRunner()
            trigger_params = params.get("trigger_params") or {}
            # 注入parentExecutionMessage, For easy追溯
            trigger_params.setdefault("__parent_execution_id__", cfg.execution_id)
            trigger_params.setdefault("__parent_node_key__", cfg.node_key)
            trigger_params.setdefault("__parent_dispatch_id__", cfg.dispatch_id)
            trigger_params["__nesting_depth__"] = current_depth

            fail_strategy = params.get("fail_strategy") or None
            trigger_type = "sub_workflow"

            result = runner.trigger_workflow(
                sub_wf,
                trigger_params=trigger_params,
                trigger_type=trigger_type,
                user_id=cfg.user_id,
                fail_strategy=fail_strategy,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("trigger sub_workflow failed: sub_id=%s", sub_id)
            return UnitOutput(
                status=STATUS_FAILED,
                error_message=f"E0603: Trigger sub-workflow failed: {exc}",
                exit_code=1,
            )

        sub_exec_id = result.execution_id
        return UnitOutput(
            status=STATUS_RUNNING,
            adapter_state={"sub_execution_id": sub_exec_id},
            output={
                "sub_workflow_id": sub_id,
                "sub_execution_id": sub_exec_id,
                "initial_runnables": result.initial_runnables,
                "triggered_at": timezone.now().isoformat(),
            },
            summary=f"Sub-workflow triggered, id={sub_exec_id}",
        )

    # -------- poll:QuerychildExecution status --------
    def poll(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        if not adapter_state:
            return UnitOutput(
                status=STATUS_FAILED,
                error_message="E0604: adapter_state missing, cannot locate sub-execution",
                exit_code=1,
            )
        sub_exec_id = adapter_state.get("sub_execution_id")
        if sub_exec_id is None:
            return UnitOutput(
                status=STATUS_FAILED,
                error_message="E0604: adapter_state missing sub_execution_id",
                exit_code=1,
            )
        try:
            from taurus.models import WorkflowExecution
            sub_exec = WorkflowExecution.objects.get(pk=sub_exec_id)
        except WorkflowExecution.DoesNotExist:
            return UnitOutput(
                status=STATUS_FAILED,
                error_message=f"E0604: Sub-execution id={sub_exec_id} not found",
                exit_code=1,
            )

        # childWorkflowYes本Adaptercreate的child任务, 其生命周期由本Adapter驱动:
        # 每次 poll 时先AdvancechildWorkflow一步, 确保childExecution statusYes最new.
        # (externalCircular只 advance parentWorkflow, childWorkflowrequire在此处被Advance)
        if sub_exec.status in {0, 1}:  # 仅非Terminal state时Advance
            try:
                from taurus.workflow.engine.runner import WorkflowRunner
                WorkflowRunner().advance_workflow(sub_exec_id)
                sub_exec.refresh_from_db()
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "advance sub_workflow in poll failed: sub_exec_id=%s err=%s",
                    sub_exec_id, exc,
                )

        # WorkflowExecution.status: 0=Pending execution 1=Running 2=Completed 3=Failed 4=Cancelled
        status = sub_exec.status
        base_output = {
            "sub_execution_id": sub_exec.pk,
            "sub_status": status,
            "start_time": sub_exec.start_time.isoformat() if sub_exec.start_time else None,
            "end_time": sub_exec.end_time.isoformat() if sub_exec.end_time else None,
        }

        if status == 2:
            sub_outputs = self._collect_sub_outputs(sub_exec.pk)
            return UnitOutput(
                status=STATUS_SUCCESS,
                output={**base_output, "completed": True, "sub_outputs": sub_outputs},
                exit_code=0,
                summary=f"Sub-workflow exec id={sub_exec.pk} completed",
            )
        if status == 3:
            err = sub_exec.error_message or "Sub-workflow execution failed"
            return UnitOutput(
                status=STATUS_FAILED,
                error_message=f"E6001: Sub-workflow execution failed: {err}",
                output={**base_output, "failed": True, "error_message": err},
                exit_code=1,
                summary=f"Sub-workflow exec id={sub_exec.pk} failed: {err}",
            )
        if status == 4:
            return UnitOutput(
                status=STATUS_CANCELLED,
                output={**base_output, "cancelled": True},
                summary=f"Sub-workflow exec id={sub_exec.pk} cancelled",
            )

        # 0=Pending execution 1=Running → 仍在run
        return UnitOutput(
            status=STATUS_RUNNING,
            output={**base_output, "running": True},
            summary="Sub-workflow executing",
        )

    # -------- cancel:CancelchildExecution --------
    def cancel(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        if not adapter_state:
            return UnitOutput(
                status=STATUS_SUCCESS,
                output={"cancelled": False, "reason": "No adapter_state, no sub-execution to cancel"},
            )
        sub_exec_id = adapter_state.get("sub_execution_id")
        if sub_exec_id is None:
            return UnitOutput(
                status=STATUS_SUCCESS,
                output={"cancelled": False, "reason": "Missing sub_execution_id"},
            )
        try:
            from taurus.models import WorkflowExecution
            sub_exec = WorkflowExecution.objects.get(pk=sub_exec_id)
        except WorkflowExecution.DoesNotExist:
            return UnitOutput(
                status=STATUS_SUCCESS,
                output={"cancelled": False, "reason": f"Sub-execution id={sub_exec_id} not found"},
            )

        # Terminal state:直接Per原Statusreturn
        if sub_exec.status in {2, 3, 4}:
            out_status = STATUS_SUCCESS if sub_exec.status == 2 else (
                STATUS_FAILED if sub_exec.status == 3 else STATUS_CANCELLED
            )
            return UnitOutput(
                status=out_status,
                output={"cancelled": False, "reason": f"Sub-execution is terminal status={sub_exec.status}"},
            )

        # 调用 runner CancelchildExecution
        try:
            from taurus.workflow.engine.runner import WorkflowRunner
            runner = WorkflowRunner()
            runner.cancel_workflow(sub_exec.pk)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cancel sub_workflow failed: sub_exec_id=%s err=%s", sub_exec_id, exc)
            return UnitOutput(
                status=STATUS_CANCELLED,
                output={"cancelled": True, "sub_execution_id": sub_exec.pk, "warning": str(exc)},
                summary=f"Sub-execution id={sub_exec.pk} cancel error: {exc}",
            )

        return UnitOutput(
            status=STATUS_CANCELLED,
            output={"cancelled": True, "sub_execution_id": sub_exec.pk},
            summary=f"Sub-execution id={sub_exec.pk} cancelled",
        )

    # -------- 辅助 --------
    def _find_existing_sub_execution(self, parent_dispatch_id: str) -> int | None:
        """via context.__parent_dispatch_id__ find已Trigger的childExecution(Idempotency)."""
        try:
            from taurus.models import WorkflowExecution
            # via trigger_params JSON Fieldfind
            # MySQL JSON Query兼容写法
            qs = WorkflowExecution.objects.filter(
                trigger_params__contains={"__parent_dispatch_id__": parent_dispatch_id},
            )
            obj = qs.order_by("-pk").first()
            return obj.pk if obj is not None else None
        except Exception:  # noqa: BLE001
            return None

    def _compute_nesting_depth(self, parent_execution_id: str | int) -> int:
        """从parentExecution的 trigger_params read嵌套深度, return current_depth = parent_depth + 1.

        顶层Workflowno __nesting_depth__, 视为 depth=0, childWorkflow depth=1.
        """
        try:
            from taurus.models import WorkflowExecution
            parent = WorkflowExecution.objects.filter(pk=parent_execution_id).first()
            if parent is None:
                return 1
            parent_depth = int((parent.trigger_params or {}).get("__nesting_depth__", 0))
            return parent_depth + 1
        except Exception:  # noqa: BLE001
            return 1

    def _collect_sub_outputs(self, sub_execution_id: int) -> dict[str, Any]:
        """收集childWorkflow各节.的OutputSnapshot, 供parentWorkflow下游节.Reference.

        returnFormat:{node_key: {"status": int, "output": dict, "exit_code": int}}
        """
        try:
            from taurus.workflow.models import WorkflowNodeExecution
            rows = WorkflowNodeExecution.objects.filter(execution_id=sub_execution_id)
            result: dict[str, Any] = {}
            for row in rows:
                result[str(row.node_key)] = {
                    "status": int(row.status),
                    "output": dict(row.output or {}),
                    "exit_code": int(row.exit_code) if row.exit_code is not None else None,
                }
            return result
        except Exception:  # noqa: BLE001
            return {}