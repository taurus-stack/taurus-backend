"""Testing-only adapter: always succeeds with a deterministic output.
仅供frameworkIntegration testuse, Production environment应从 INSTALLED_UNIT_ADAPTERS 注释掉.
"""
from __future__ import annotations

from typing import Any

from ..engine.base_adapter import ExecutableUnit
from ..engine.context import WorkflowContext
from ..engine.registry import register_unit_adapter
from ..engine.schemas import (
    STATUS_SUCCESS,
    RenderedNodeConfig,
    UnitOutput,
    ValidationResult,
)


@register_unit_adapter("testing_noop")
class TestingNoopAdapter(ExecutableUnit):
    node_type = "testing_noop"
    display_name = "Test-success placeholder"
    category = "control"
    requires_host = False
    is_asynchronous_human = False

    def validate_config(self, params, *, secrets_mask=None):
        r = ValidationResult.success()
        if not isinstance(params.get("value", ""), str | int | float | list | dict):
            r.add_error("/params/value", "E0101: value must be JSON serializable")
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
        v = cfg.params.get("copy")
        if v is None:
            v = cfg.params.get("value", "noop")
        return UnitOutput(
            status=STATUS_SUCCESS,
            output={"echo": v, "dispatch_id": cfg.dispatch_id},
            exit_code=0,
            summary="noop execution succeeded",
        )

    def poll(self, cfg, adapter_state):
        return UnitOutput(
            status=STATUS_SUCCESS,
            output={"dispatch_id": cfg.dispatch_id},
            exit_code=0,
        )

    def cancel(self, cfg, adapter_state):
        return UnitOutput(
            status=STATUS_SUCCESS,
            output={"cancelled": False, "reason": "noop already completed, no need to cancel"},
        )