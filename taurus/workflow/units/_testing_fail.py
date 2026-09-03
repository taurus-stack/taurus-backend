"""Testing-only adapter: always fails with deterministic error codes.
用于test:Failed策略, Error码回显, retry计数server.
"""
from __future__ import annotations

from typing import Any

from ..engine.base_adapter import ExecutableUnit
from ..engine.context import WorkflowContext
from ..engine.registry import register_unit_adapter
from ..engine.schemas import (
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    RenderedNodeConfig,
    UnitOutput,
    ValidationResult,
)


@register_unit_adapter("testing_fail")
class TestingFailAdapter(ExecutableUnit):
    node_type = "testing_fail"
    display_name = "Test-fail placeholder"
    category = "control"
    requires_host = False
    is_asynchronous_human = False

    # allow的Error码:E0499 ConfigError / E2401 远端joinFailed(可retry)/ E3499 BusinessFailed
    _ALLOWED = {"E0499", "E2401", "E3499"}

    def validate_config(self, params, *, secrets_mask=None):
        r = ValidationResult.success()
        code = params.get("error_code", "E3499")
        if code not in self._ALLOWED:
            r.add_error(
                "/params/error_code",
                f"E0499: error_code must be one of {sorted(self._ALLOWED)}",
            )
        return r

    def validate_and_render(
        self,
        params: dict[str, Any],
        context: WorkflowContext,
        *,
        secrets_mask: list[str] | None = None,
    ) -> dict[str, Any]:
        return context.render_structure(params)

    def dispatch(self, cfg: RenderedNodeConfig) -> UnitOutput:
        code = cfg.params.get("error_code", "E3499")
        msg = cfg.params.get("message", "(failure injected by testing_fail)")
        # spec §2.4: Error码Prefixmust在 error_message 首Field, For easy正则提取
        return UnitOutput(
            status=STATUS_FAILED,
            output={"injected": True, "expected_code": code},
            exit_code=1,
            error_message=f"{code}: {msg}",
        )

    def poll(self, cfg, adapter_state):
        # 该Adapter同步, so不存在 RUNNING
        return self.dispatch(cfg)

    def cancel(self, cfg, adapter_state):
        # 该AdapterYes同步Failed, Cancel无意义, returnSuccess(Cancel已Failed节.视为 no-op)
        return UnitOutput(status=STATUS_SUCCESS, output={"noop_cancel": True})