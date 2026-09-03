"""S4-01 HTTP Callback Adapter: Asynchronous node driven by external system HTTP callback.

Asynchronous node (requires_host=False, is_asynchronous_human=True),
dispatch sends POST request to specified URL (carrying token callback address),
external system processes and calls back /api/taurus/workflow/callback/{token}/ to write results back,
poll checks whether adapter_state.callback_status is success/failed.

Error codes:
- E0601: url not filled or protocol invalid
- E0602: payload_template render failed
- E0603: callback request send failed
- E0604: callback result timeout
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import timedelta
from typing import Any

import requests
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

_ALLOWED_METHODS = {"POST", "PUT", "PATCH"}
_MAX_URL_LEN = 2048


@register_unit_adapter("http_callback")
class HttpCallbackAdapter(ExecutableUnit):
    """external HTTP Callback驱动的异步节.."""

    node_type = "http_callback"
    display_name = "HTTP Callback"
    requires_host = False
    is_asynchronous_human = True

    def validate_config(self, params: dict, *, secrets_mask=None) -> ValidationResult:
        r = ValidationResult()
        url = (params.get("url") or "").strip()
        if not url:
            r.add_error("/params/url", "E0601: Callback URL is required")
        elif not url.startswith(("http://", "https://")):
            r.add_error("/params/url", "E0601: URL must start with http:// or https://")
        elif len(url) > _MAX_URL_LEN:
            r.add_error("/params/url", f"E0601: URL must not exceed {_MAX_URL_LEN} chars")

        method = (params.get("method") or "POST").upper()
        if method not in _ALLOWED_METHODS:
            r.add_error("/params/method", f"E0601: method must be in {_ALLOWED_METHODS}")

        timeout = params.get("timeout_seconds")
        if timeout is not None:
            try:
                t = int(timeout)
                if t < 30 or t > 604800:
                    r.add_error("/params/timeout_seconds", "E0601: timeout range [30, 604800] seconds")
            except (TypeError, ValueError):
                r.add_error("/params/timeout_seconds", "E0601: timeout must be integer")

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
            raise ValueError(f"http_callback validation failed: {vr.errors}")
        return context.render_structure(params)

    def dispatch(self, cfg: RenderedNodeConfig) -> UnitOutput:
        params = cfg.params or {}
        url = params.get("url", "")
        method = (params.get("method") or "POST").upper()
        headers = params.get("headers") or {}
        payload_template = params.get("payload_template") or {}
        timeout = int(params.get("timeout_seconds") or 3600)

        token = secrets.token_urlsafe(32)

        payload: dict[str, Any] = {
            "workflow_execution_id": cfg.execution_id,
            "dispatch_id": cfg.dispatch_id,
            "node_key": cfg.node_key,
            "node_type": self.node_type,
            "token": token,
        }

        if isinstance(payload_template, dict):
            payload.update(payload_template)
        elif isinstance(payload_template, str) and payload_template.strip():
            try:
                extra = json.loads(payload_template)
                if isinstance(extra, dict):
                    payload.update(extra)
            except Exception:
                pass

        headers = dict(headers)
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("X-Workflow-Token", token)

        try:
            resp = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=payload,
                timeout=(10, min(30, timeout)),
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.error("E0603: Callback request failed: %s", exc)
            return UnitOutput(
                status=STATUS_FAILED,
                error_message=f"E0603: {exc}",
                exit_code=1,
            )

        return UnitOutput(
            status=STATUS_RUNNING,
            adapter_state={
                "callback_status": "pending",
                "token": token,
                "deadline": (timezone.now() + timedelta(seconds=timeout)).isoformat(),
                "remote_status_code": resp.status_code,
            },
            output={
                "token": token,
                "remote_status_code": resp.status_code,
            },
            summary=f"Callback sent to {url}, awaiting response",
        )

    def poll(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        if not adapter_state:
            return UnitOutput(
                status=STATUS_FAILED,
                error_message="E0604: adapter_state missing, cannot poll callback",
                exit_code=1,
            )

        status = adapter_state.get("callback_status", "pending")
        if status == "success":
            return UnitOutput(
                status=STATUS_SUCCESS,
                output=adapter_state.get("callback_payload") or {},
                summary="External callback succeeded",
            )
        if status == "failed":
            return UnitOutput(
                status=STATUS_FAILED,
                error_message=adapter_state.get("error") or "External callback failed",
                exit_code=1,
            )
        if status == "cancelled":
            return UnitOutput(
                status=STATUS_CANCELLED,
                output={"cancelled": True},
            )

        deadline = adapter_state.get("deadline")
        if deadline:
            try:
                from django.utils.dateparse import parse_datetime
                dl = parse_datetime(deadline)
                if dl and timezone.now() > dl:
                    return UnitOutput(
                        status=STATUS_FAILED,
                        error_message="E0604: External callback timeout",
                        exit_code=1,
                    )
            except Exception:
                pass

        return UnitOutput(
            status=STATUS_RUNNING,
            output={"waiting": True},
        )

    def cancel(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        if not adapter_state:
            return UnitOutput(
                status=STATUS_CANCELLED,
                output={"cancelled": True, "reason": "No adapter_state"},
            )

        state = dict(adapter_state)
        state["callback_status"] = "cancelled"
        return UnitOutput(
            status=STATUS_CANCELLED,
            adapter_state=state,
            output={"cancelled": True, "reason": "Triggered by workflow cancellation"},
        )

    def inject_external_event(
        self,
        *,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
        event: dict[str, Any],
    ) -> UnitOutput:
        state = dict(adapter_state or {})
        callback_status = event.get("status", "success")
        state["callback_status"] = callback_status
        state["callback_payload"] = event.get("data") or {}

        if callback_status == "failed":
            state["error"] = event.get("error") or "External callback failed"

        output = state.get("callback_payload") or {}
        if callback_status == "success":
            return UnitOutput(
                status=STATUS_SUCCESS,
                adapter_state=state,
                output=output,
                summary="External callback succeeded",
            )
        elif callback_status == "failed":
            return UnitOutput(
                status=STATUS_FAILED,
                adapter_state=state,
                error_message=state.get("error", "External callback failed"),
                exit_code=1,
            )
        else:
            return UnitOutput(
                status=STATUS_RUNNING,
                adapter_state=state,
                output=output,
            )