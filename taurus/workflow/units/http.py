"""S4-04 HTTP Adapter: Synchronous HTTP request node.

Synchronous node (requires_host=False, is_asynchronous_human=False),
dispatch directly sends HTTP request and parses response.
Supports GET/POST/PUT/DELETE + Bearer/API Key/Basic authentication.

Error codes:
- E0901: url not filled or protocol invalid
- E0902: method/auth_type invalid
- E0903: request send failed (network/DNS exception)
"""
from __future__ import annotations

import json
import logging
from typing import Any

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

_ALLOWED_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}
_ALLOWED_AUTH = {"none", "bearer", "api_key", "basic"}
_MAX_URL_LEN = 2048


@register_unit_adapter("http")
class HttpAdapter(ExecutableUnit):
    """同步 HTTP request节.."""

    node_type = "http"
    display_name = "HTTP Request"
    requires_host = False
    is_asynchronous_human = False

    def validate_config(self, params: dict, *, secrets_mask=None) -> ValidationResult:
        r = ValidationResult()
        url = (params.get("url") or "").strip()
        if not url:
            r.add_error("/params/url", "E0901: URL is required")
        elif not url.startswith(("http://", "https://")):
            r.add_error("/params/url", "E0901: URL must start with http:// or https://")
        elif len(url) > _MAX_URL_LEN:
            r.add_error("/params/url", f"E0901: URL must not exceed {_MAX_URL_LEN} chars")

        method = (params.get("method") or "GET").upper()
        if method not in _ALLOWED_METHODS:
            r.add_error("/params/method", f"E0902: method must be in {_ALLOWED_METHODS}")

        auth_type = (params.get("auth_type") or "none").strip()
        if auth_type not in _ALLOWED_AUTH:
            r.add_error("/params/auth_type", f"E0902: auth_type must be in {_ALLOWED_AUTH}")

        timeout = params.get("timeout")
        if timeout is not None:
            try:
                t = int(timeout)
                if t < 1 or t > 600:
                    r.add_error("/params/timeout", "E0902: timeout range [1, 600] seconds")
            except (TypeError, ValueError):
                r.add_error("/params/timeout", "E0902: timeout must be integer")

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
            raise ValueError(f"http validation failed: {vr.errors}")
        return context.render_structure(params)

    def dispatch(self, cfg: RenderedNodeConfig) -> UnitOutput:
        params = cfg.params or {}
        url = params.get("url", "")
        method = (params.get("method") or "GET").upper()
        headers = params.get("headers") or {}
        body = params.get("body") or {}
        timeout = int(params.get("timeout") or 30)
        auth_type = (params.get("auth_type") or "none").strip()
        auth_config = params.get("auth_config") or {}

        try:
            headers = self._build_auth(headers, auth_type, auth_config)

            kwargs: dict[str, Any] = {"headers": headers, "timeout": (5, timeout)}
            if method not in {"GET", "DELETE"} and body:
                if isinstance(body, (dict, list)):
                    kwargs["json"] = body
                else:
                    kwargs["data"] = body

            resp = requests.request(method=method, url=url, **kwargs)

            try:
                resp_body = resp.json()
            except (json.JSONDecodeError, ValueError):
                resp_body = resp.text

            output: dict[str, Any] = {
                "status_code": resp.status_code,
                "response_body": resp_body,
                "elapsed": resp.elapsed.total_seconds(),
            }

            if resp.status_code >= 400:
                return UnitOutput(
                    status=STATUS_FAILED,
                    error_message=f"HTTP {resp.status_code}: {resp_body}",
                    exit_code=1,
                    output=output,
                )

            return UnitOutput(
                status=STATUS_SUCCESS,
                output=output,
                exit_code=0,
                summary=f"HTTP {method} {url} → {resp.status_code}",
            )
        except requests.exceptions.Timeout as exc:
            logger.error("HTTP request timeout: %s", exc)
            return UnitOutput(
                status=STATUS_FAILED,
                error_message=f"E0903: Request timeout: {exc}",
                exit_code=1,
            )
        except requests.exceptions.RequestException as exc:
            logger.error("HTTP request failed: %s", exc)
            return UnitOutput(
                status=STATUS_FAILED,
                error_message=f"E0903: {exc}",
                exit_code=1,
            )

    def poll(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        return UnitOutput(
            status=STATUS_SUCCESS,
            output={"note": "http is a sync node"},
        )

    def cancel(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        return UnitOutput(
            status=STATUS_CANCELLED,
            output={"cancelled": False, "reason": "http is a sync node"},
        )

    @staticmethod
    def _build_auth(
        headers: dict[str, str],
        auth_type: str,
        auth_config: dict[str, Any],
    ) -> dict[str, str]:
        if auth_type == "bearer":
            token = auth_config.get("token") or auth_config.get("access_token") or ""
            if token:
                headers["Authorization"] = f"Bearer {token}"
        elif auth_type == "api_key":
            key = auth_config.get("key") or ""
            header_name = auth_config.get("header_name", "X-API-Key")
            if key:
                headers[header_name] = key
        elif auth_type == "basic":
            username = auth_config.get("username", "")
            password = auth_config.get("password", "")
            if username and password:
                import base64
                token = base64.b64encode(f"{username}:{password}".encode()).decode()
                headers["Authorization"] = f"Basic {token}"
        return headers