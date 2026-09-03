"""S2-05 Human Approval Adapter: approval.

Asynchronous node (requires_host=False, is_asynchronous_human=True),
dispatch creates OpsExecutionApproval ApprovalInstance (status pending),
requires human to approve/reject via the approval page, poll writes terminal state back based on ApprovalInstance status,
cancel marks ApprovalInstance as cancelled (withdrawn).

Error code convention:
- E0501: No approver specified (approver_user_id is empty)
- E0502: Approver user does not exist
- E0503: Failed to create ApprovalInstance in DB
- E0504: adapter_state missing approval_id or approval record does not exist
- E5001: Approval rejected
"""
from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.db import transaction
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

_USER_MODEL = settings.AUTH_USER_MODEL


@register_unit_adapter("approval")
class ApprovalAdapter(ExecutableUnit):
    """Human approval adapter: creates approval record -> waits for human processing -> writes back result."""

    node_type = "approval"
    display_name = "Human Approval"
    requires_host = False
    is_asynchronous_human = True

    _ALLOWED_ACTIONS = {"submit", "inform"}

    # -------- Basic interface: validation + rendering --------
    def validate_config(self, params: dict, *, secrets_mask=None) -> ValidationResult:
        r = ValidationResult()
        action = (params.get("action") or "submit").strip() or "submit"
        if action not in self._ALLOWED_ACTIONS:
            r.add_error(
                "/params/action",
                f"E0201: action must be in {self._ALLOWED_ACTIONS}, currently {action!r}",
            )

        # submit must指定Approver;inform(notification)可无Approver(默认Submitter自己过)
        if action == "submit":
            approver_user_id = params.get("approver_user_id")
            if approver_user_id in (None, "", 0):
                r.add_error(
                    "/params/approver_user_id",
                    "E0501: approver_user_id is required when using submit mode",
                )

        # Title & Description(用于Frontend展示)
        title = (params.get("title") or "").strip()
        if len(title) > 200:
            r.add_error("/params/title", "E0204: title must not exceed 200 chars")

        desc = params.get("description") or ""
        if isinstance(desc, str) and len(desc) > 5000:
            r.add_error("/params/description", "E0204: description must not exceed 5000 chars")

        timeout = params.get("timeout_seconds")
        if timeout is not None:
            try:
                t = int(timeout)
                if t < 60 or t > 7 * 24 * 3600:
                    r.add_error(
                        "/params/timeout_seconds",
                        "E0204: timeout_seconds range [60, 604800] (1min ~ 7days)",
                    )
            except (TypeError, ValueError):
                r.add_error("/params/timeout_seconds", "E0201: timeout_seconds must be integer")

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
            raise ValueError(f"approval validation failed: {vr.errors}")
        raw = context.render_structure(params)
        return raw

    # -------- dispatch:createApprovalInstance --------
    def dispatch(self, cfg: RenderedNodeConfig) -> UnitOutput:
        params: dict = cfg.params or {}
        action = (params.get("action") or "submit").strip() or "submit"
        title = (params.get("title") or f"Workflow approval {cfg.node_key}").strip()
        description = params.get("description") or ""

        submitter_id = cfg.user_id

        approver_id = params.get("approver_user_id")
        if action == "submit":
            if approver_id in (None, "", 0):
                return UnitOutput(
                    status=STATUS_FAILED,
                    error_message="E0501: Approver not specified (approver_user_id), cannot create approval",
                    exit_code=1,
                )
            # ValidationApprover存在
            try:
                from django.apps import apps
                UserCls = apps.get_model(_USER_MODEL)
                with transaction.atomic():
                    if not UserCls.objects.filter(pk=approver_id).exists():
                        return UnitOutput(
                            status=STATUS_FAILED,
                            error_message=f"E0502: Approver user id={approver_id} not found",
                            exit_code=1,
                        )
            except Exception as exc:  # noqa: BLE001
                return UnitOutput(
                    status=STATUS_FAILED,
                    error_message=f"E0502: Approver validation failed: {exc}",
                    exit_code=1,
                )

        # require一  host associate(OpsExecutionApproval Model host Required)
        # Approval节.不Execution任何Hostoperation, 但Model要求, so取一 存在的Host, 
        # 或在无Host时用特殊逻辑:这里allowApproval单直接Skip host 要求.
        from taurus.workflow.engine.schemas import NO_HOST_SENTINEL
        host_id = None
        host_raw = cfg.host_id
        if host_raw is not None and str(host_raw).strip() != "" and str(host_raw) != NO_HOST_SENTINEL:
            host_id = host_raw

        try:
            from django.apps import apps
            from taurus.models import OpsExecutionApproval, Host

            with transaction.atomic():
                if host_id is None:
                    # 兜底:取任意一hosts(Only used for满足Modelforeign key, 不实际Execution)
                    h = Host.objects.filter(status=1).order_by("pk").first()
                    if h is not None:
                        host_id = h.pk
                    else:
                        return UnitOutput(
                            status=STATUS_FAILED,
                            error_message="E0503: No available hosts, cannot create approval",
                            exit_code=1,
                        )

                # Idempotency:同一 dispatch_id 只create一entriesApproval单
                existing = OpsExecutionApproval.objects.filter(
                    batch_id=cfg.dispatch_id,
                ).first()
                if existing is not None:
                    return UnitOutput(
                        status=STATUS_RUNNING,
                        adapter_state={"approval_id": existing.pk},
                        summary=f"Reusing existing approval id={existing.pk}",
                    )

                approver_name = None
                if approver_id not in (None, "", 0):
                    try:
                        UserCls = apps.get_model(_USER_MODEL)
                        u = UserCls.objects.filter(pk=approver_id).first()
                        if u is not None:
                            approver_name = getattr(u, "username", None) or getattr(u, "name", None) or str(u)
                    except Exception:  # noqa: BLE001
                        pass

                submitter_name = None
                if submitter_id not in (None, "", 0):
                    try:
                        UserCls = apps.get_model(_USER_MODEL)
                        su = UserCls.objects.filter(pk=submitter_id).first()
                        if su is not None:
                            submitter_name = getattr(su, "username", None) or getattr(su, "name", None) or str(su)
                    except Exception:  # noqa: BLE001
                        pass

                # Validation submitter_id User存在(若provide), No则置EmptyAvoid FK Failed
                if submitter_id not in (None, 0, ""):
                    try:
                        UserCls = apps.get_model(_USER_MODEL)
                        if not UserCls.objects.filter(pk=submitter_id).exists():
                            submitter_id = None
                            submitter_name = None
                    except Exception:  # noqa: BLE001
                        submitter_id = None
                        submitter_name = None

                # Merge description + Workflow元Message
                submit_desc_parts = [str(description)] if description else []
                submit_desc_parts.append(
                    f"Workflow node: {cfg.node_name} (key={cfg.node_key}, execution_id={cfg.execution_id})"
                )
                submit_desc = "\n\n".join(submit_desc_parts)

                # 构建候选Approverlist
                candidate_approvers = []
                if approver_id not in (None, "", 0):
                    try:
                        UserCls = apps.get_model(_USER_MODEL)
                        u = UserCls.objects.filter(pk=approver_id).first()
                        if u is not None:
                            candidate_approvers.append({
                                'user_id': u.id,
                                'username': u.username,
                                'name': getattr(u, 'name', '') or '',
                            })
                    except Exception:  # noqa: BLE001
                        pass

                # Approval模式map:Frontend 'or'/'and' → Model 'any'/'all'
                raw_mode = params.get("mode", "or")
                approval_mode = 'all' if raw_mode in ('and', 'all') else 'any'

                # QueryWorkflowName和Description
                workflow_name = title[:200] if title else ''
                workflow_desc = description or ''
                try:
                    from taurus.workflow.models import WorkflowNodeExecution
                    ne = WorkflowNodeExecution.objects.filter(dispatch_id=cfg.dispatch_id).select_related(
                        'execution__workflow'
                    ).first()
                    if ne and ne.execution and ne.execution.workflow:
                        wf = ne.execution.workflow
                        workflow_name = (getattr(wf, 'name', '') or workflow_name)[:200]
                        wf_desc = getattr(wf, 'description', '') or ''
                        if wf_desc:
                            workflow_desc = f"{wf_desc}\n\n{workflow_desc}".strip()
                except Exception:
                    pass

                approval = OpsExecutionApproval.objects.create(
                    batch_id=cfg.dispatch_id,
                    status="pending",
                    source_type="workflow",
                    related_name=workflow_name,
                    related_desc=workflow_desc[:2000] if workflow_desc else '',
                    submitter_id=submitter_id if submitter_id not in (None, 0, "") else None,
                    submitter_name=submitter_name,
                    submit_desc=submit_desc,
                    approver_id=approver_id if approver_id not in (None, "", 0) else None,
                    approver_name=approver_name,
                    approval_mode=approval_mode,
                    candidate_approvers=candidate_approvers,
                    host_id=host_id,
                    script_type="sh",  # Placeholder, Approval不ExecutionScript
                    script_content="# Approval node placeholder\n",
                    timeout_seconds=min(
                        int(params.get("timeout_seconds") or 3 * 24 * 3600),
                        7 * 24 * 3600,
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("create OpsExecutionApproval failed: dispatch_id=%s", cfg.dispatch_id)
            return UnitOutput(
                status=STATUS_FAILED,
                error_message=f"E0503: Create approval instance failed: {exc}",
                exit_code=1,
            )

        return UnitOutput(
            status=STATUS_RUNNING,
            adapter_state={"approval_id": approval.pk},
            output={
                "approval_id": approval.pk,
                "status": approval.status,
                "title": title,
                "submitter": submitter_name,
                "approver": approver_name,
                "created_at": approval.create_datetime.isoformat() if getattr(approval, "create_datetime", None) else timezone.now().isoformat(),
            },
            summary=f"Approval id={approval.pk} created, awaiting approver",
        )

    # -------- poll:回查Approval单Status --------
    def poll(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        if not adapter_state:
            return UnitOutput(
                status=STATUS_FAILED,
                error_message="E0504: adapter_state missing, cannot locate approval",
                exit_code=1,
            )
        approval_id = adapter_state.get("approval_id")
        if approval_id is None:
            return UnitOutput(
                status=STATUS_FAILED,
                error_message="E0504: adapter_state missing approval_id",
                exit_code=1,
            )
        try:
            from taurus.models import OpsExecutionApproval
            approval = OpsExecutionApproval.objects.get(pk=approval_id)
        except OpsExecutionApproval.DoesNotExist:
            return UnitOutput(
                status=STATUS_FAILED,
                error_message=f"E0504: Approval id={approval_id} deleted",
                exit_code=1,
            )

        status = approval.status
        base_output = {
            "approval_id": approval.pk,
            "status": status,
            "approver_name": approval.approver_name,
            "approve_reason": approval.approve_reason,
            "approve_time": approval.approve_time.isoformat() if approval.approve_time else None,
        }

        if status == "approved":
            return UnitOutput(
                status=STATUS_SUCCESS,
                output={**base_output, "approved": True},
                exit_code=0,
                summary=f"Approval id={approval.pk} approved",
            )
        if status == "rejected":
            reason = approval.approve_reason or "Approval rejected"
            return UnitOutput(
                status=STATUS_FAILED,
                error_message=f"E5001: Approval rejected: {reason}",
                output={**base_output, "rejected": True},
                exit_code=1,
                summary=f"Approval id={approval.pk} rejected: {reason}",
            )
        if status == "cancelled":
            return UnitOutput(
                status=STATUS_CANCELLED,
                output={**base_output, "cancelled": True},
                summary=f"Approval id={approval.pk} withdrawn",
            )
        if status in {"executing", "done", "failed"}:
            # Approval → Execution链路的后续phase(本Adapter只关心Approval passed/驳回)
            if status == "failed":
                return UnitOutput(
                    status=STATUS_FAILED,
                    error_message=f"E5001: 审批后执行阶段失败：{approval.approve_reason or ''}",
                    output=base_output,
                    exit_code=1,
                )
            return UnitOutput(
                status=STATUS_SUCCESS,
                output={**base_output, "passed_through_execution": True},
                exit_code=0,
                summary=f"Post-approval phase={status}",
            )

        # pending → 仍在等待
        return UnitOutput(
            status=STATUS_RUNNING,
            output={**base_output, "waiting": True},
            summary="Waiting for approver",
        )

    # -------- cancel:撤回Approval单 --------
    def cancel(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        if not adapter_state:
            return UnitOutput(
                status=STATUS_SUCCESS,
                output={"cancelled": False, "reason": "No adapter_state, no approval to withdraw"},
            )
        approval_id = adapter_state.get("approval_id")
        if approval_id is None:
            return UnitOutput(
                status=STATUS_SUCCESS,
                output={"cancelled": False, "reason": "Missing approval_id, cannot withdraw"},
            )
        try:
            from taurus.models import OpsExecutionApproval
            approval = OpsExecutionApproval.objects.get(pk=approval_id)
        except OpsExecutionApproval.DoesNotExist:
            return UnitOutput(
                status=STATUS_SUCCESS,
                output={"cancelled": False, "reason": f"Approval id={approval_id} not found"},
            )

        # Terminal stateApproval单:直接Per原Statusreturn
        if approval.status in {"approved", "rejected", "cancelled", "done"}:
            out_status = STATUS_SUCCESS if approval.status in {"approved", "done"} else (
                STATUS_FAILED if approval.status == "rejected" else STATUS_CANCELLED
            )
            return UnitOutput(
                status=out_status,
                output={"cancelled": False, "reason": f"Approval is terminal status={approval.status}, no need to withdraw"},
            )

        # running/executing 一律标记为 cancelled 撤回
        approval.status = "cancelled"
        approval.finish_time = timezone.now()
        if not approval.approve_reason:
            approval.approve_reason = "ABORTED: workflow cancelled, withdraw approval"
        approval.save(update_fields=["status", "finish_time", "approve_reason", "modifier", "update_datetime"])

        return UnitOutput(
            status=STATUS_CANCELLED,
            output={
                "cancelled": True,
                "approval_id": approval.pk,
                "reason": "Approval withdrawn (triggered by workflow cancellation)",
            },
            summary=f"Approval id={approval.pk} withdrawn",
        )