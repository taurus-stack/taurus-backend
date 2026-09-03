"""S4-06 Webhook Notification Adapter: Webhook notification node.

Synchronous node (requires_host=False, is_asynchronous_human=False),
dispatch sends HTTP request per config, supports failed retry (exponential backoff).

Supports three request body formats:
- json:  Parses payload_template as JSON object and sends as application/json
- form:  Parses payload_template as key=value&... format and sends as application/x-www-form-urlencoded
- raw:   Sends payload_template as plain text directly

Error codes:
- E1101: url not filled
- E1102: retry count out of range
- E1103: all retries failed
- E1104: request body format parse failed
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import parse_qs

import requests

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


@register_unit_adapter("webhook_notification")
class WebhookNotificationAdapter(ExecutableUnit):
    """Webhook notification:send带retry的 HTTP request."""

    node_type = "webhook_notification"
    display_name = "Webhook Notification"
    requires_host = False
    is_asynchronous_human = False

    def validate_config(self, params: dict, *, secrets_mask=None) -> ValidationResult:
        r = ValidationResult()
        url = (params.get("url") or "").strip()
        if not url:
            r.add_error("/params/url", "E1101: Webhook URL is required")
        elif not url.startswith(("http://", "https://")):
            r.add_error("/params/url", "E1101: URL must start with http:// or https://")

        retry = params.get("retry_count")
        if retry is not None:
            try:
                ri = int(retry)
                if ri < 0 or ri > 10:
                    r.add_error("/params/retry_count", "E1102: retry_count range [0, 10]")
            except (TypeError, ValueError):
                r.add_error("/params/retry_count", "E1102: retry_count must be integer")

        body_format = (params.get("body_format") or "json").strip()
        if body_format not in ("json", "form", "raw"):
            r.add_error("/params/body_format", "E1104: body_format must be json/form/raw")

        payload = params.get("payload_template") or ""
        if body_format == "json" and payload:
            try:
                json.loads(payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                r.add_error("/params/payload_template", "E1104: Invalid JSON")

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
            raise ValueError(f"webhook_notification validation failed: {vr.errors}")
        return context.render_structure(params)

    @staticmethod
    def _prepare_payload(body_format: str, payload: Any) -> tuple[dict[str, Any], dict[str, str]]:
        """according to body_format 准备Request body和默认 Content-Type.

        Returns:
            (kwargs_for_request, extra_headers)
        """
        extra_headers: dict[str, str] = {}
        kwargs: dict[str, Any] = {}

        if body_format == "json":
            text = payload or ""
            if isinstance(text, (dict, list)):
                kwargs["json"] = text
            else:
                try:
                    parsed = json.loads(text) if text else {}
                    kwargs["json"] = parsed
                except (TypeError, ValueError, json.JSONDecodeError):
                    kwargs["data"] = str(text)
            extra_headers.setdefault("Content-Type", "application/json")

        elif body_format == "form":
            text = payload or ""
            if isinstance(text, str) and text.strip():
                parsed = parse_qs(text, keep_blank_values=True)
                flat = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
                kwargs["data"] = flat
            else:
                kwargs["data"] = {}
            extra_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

        elif body_format == "raw":
            text = payload or ""
            kwargs["data"] = str(text) if text else ""
        else:
            if isinstance(payload, (dict, list)):
                kwargs["json"] = payload
            else:
                kwargs["data"] = payload

        return kwargs, extra_headers

    def dispatch(self, cfg: RenderedNodeConfig) -> UnitOutput:
        params = cfg.params or {}
        url = params.get("url", "")
        method = (params.get("method") or "POST").upper()
        headers = dict(params.get("headers") or {})
        payload = params.get("payload_template") or ""
        body_format = (params.get("body_format") or "json").strip()
        timeout = int(params.get("timeout") or 30)
        retry_count = int(params.get("retry_count") or 2)

        last_error = None
        for attempt in range(retry_count + 1):
            try:
                kwargs: dict[str, Any] = {"headers": headers, "timeout": (5, timeout)}

                if method != "GET":
                    payload_kwargs, extra_headers = self._prepare_payload(body_format, payload)
                    kwargs.update(payload_kwargs)
                    for k, v in extra_headers.items():
                        if k not in headers:
                            headers[k] = v
                    kwargs["headers"] = headers

                resp = requests.request(method=method, url=url, **kwargs)

                try:
                    body = resp.json()
                except (json.JSONDecodeError, ValueError):
                    body = resp.text

                if resp.status_code < 400:
                    return UnitOutput(
                        status=STATUS_SUCCESS,
                        output={
                            "response_status": resp.status_code,
                            "response_body": body,
                            "attempts": attempt + 1,
                        },
                        exit_code=0,
                        summary=f"Webhook sent successfully (attempt {attempt + 1})",
                    )

                last_error = f"HTTP {resp.status_code}"
                if attempt < retry_count:
                    time.sleep(min(2 ** attempt, 30))
            except Exception as exc:
                last_error = str(exc)
                logger.warning("Webhook attempt %d failed: %s", attempt + 1, exc)
                if attempt < retry_count:
                    time.sleep(min(2 ** attempt, 30))

        return UnitOutput(
            status=STATUS_FAILED,
            error_message=f"E1103: {last_error}",
            exit_code=1,
            output={"response_status": None, "attempts": retry_count + 1},
        )

    def poll(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        return UnitOutput(
            status=STATUS_SUCCESS,
            output={"note": "webhook_notification is a sync node"},
        )

    def cancel(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        return UnitOutput(
            status=STATUS_CANCELLED,
            output={"cancelled": False, "reason": "webhook_notification is a sync node"},
        )