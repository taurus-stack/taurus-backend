"""S4-07 Email Notification Adapter: SMTP email notification node.

Synchronous node (requires_host=False, is_asynchronous_human=False),
dispatch uses Django's built-in email mechanism (django.core.mail) to send emails.
Requires Django settings.EMAIL_HOST and other SMTP config to be ready.

Error codes:
- E1201: recipients not filled
- E1202: subject/body not filled
- E1203: SMTP send exception
"""
from __future__ import annotations

import logging
from typing import Any

from django.core.mail import send_mail

from taurus.workflow.engine.base_adapter import ExecutableUnit
from taurus.workflow.engine.context import WorkflowContext
from taurus.workflow.engine.registry import register_unit_adapter
from taurus.workflow.engine.schemas import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_SUCCESS,
    RenderedNodeConfig,
    UnitOutput,
    ValidationResult,
)

logger = logging.getLogger(__name__)


@register_unit_adapter("email_notification")
class EmailNotificationAdapter(ExecutableUnit):
    """SMTP Email notification节.."""

    node_type = "email_notification"
    display_name = "Email Notification"
    requires_host = False
    is_asynchronous_human = False

    def validate_config(self, params: dict, *, secrets_mask=None) -> ValidationResult:
        r = ValidationResult()
        recipients = params.get("recipients")
        if not recipients:
            r.add_error("/params/recipients", "E1201: recipients is required")

        subject = (params.get("subject_template") or "").strip()
        if not subject:
            r.add_error("/params/subject_template", "E1202: subject_template is required")

        body = (params.get("body_template") or "").strip()
        if not body:
            r.add_error("/params/body_template", "E1202: body_template is required")

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
            raise ValueError(f"email_notification validation failed: {vr.errors}")
        return context.render_structure(params)

    def dispatch(self, cfg: RenderedNodeConfig) -> UnitOutput:
        params = cfg.params or {}

        recipients = params.get("recipients") or []
        if isinstance(recipients, str):
            recipients = [recipients]
        recipients = [r for r in recipients if r]

        subject = params.get("subject_template", "")
        body = params.get("body_template", "")
        from_email = params.get("from_email")

        sent_count = 0
        try:
            send_mail(
                subject=subject,
                message=body,
                from_email=from_email,
                recipient_list=recipients,
                fail_silently=False,
            )
            sent_count = len(recipients)
            return UnitOutput(
                status=STATUS_SUCCESS,
                output={"sent_count": sent_count, "recipients": recipients},
                exit_code=0,
                summary=f"Email sent: {sent_count}",
            )
        except Exception as exc:
            logger.error("E1203: Email send failed: %s", exc)
            return UnitOutput(
                status=STATUS_FAILED,
                error_message=f"E1203: {exc}",
                exit_code=1,
                output={"sent_count": 0},
            )

    def poll(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        return UnitOutput(
            status=STATUS_SUCCESS,
            output={"note": "email_notification is a sync node"},
        )

    def cancel(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        return UnitOutput(
            status=STATUS_CANCELLED,
            output={"cancelled": False, "reason": "email_notification is a sync node"},
        )