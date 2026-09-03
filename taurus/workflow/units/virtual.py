"""S2-07 Virtual Node Adapter: start / end / noop.

These three nodes do not execute any actual operation, dispatch returns SUCCESS instantly.
Usage:
- start: Workflow start, for easy DAG topology unification (all real nodes have upstream)
- end:   Workflow end, aggregates multiple branch results
- noop:  No-op placeholder, used for debug/workflow orchestration placeholder

Error codes: None (virtual nodes never fail)
Output fields:
- start: {started_at: ISO8601}
- end:   {finished_at: ISO8601}
- noop:  {echo: params.value or "noop"}
"""
from __future__ import annotations

from typing import Any

from django.utils import timezone

from ..engine.base_adapter import ExecutableUnit
from ..engine.context import WorkflowContext
from ..engine.registry import register_unit_adapter, get_registry
from ..engine.schemas import (
    STATUS_SUCCESS,
    RenderedNodeConfig,
    UnitOutput,
    ValidationResult,
)


class _VirtualBase(ExecutableUnit):
    """Virtual node public base class: no host required, non-asynchronous, instant success."""

    requires_host = False
    is_asynchronous_human = False

    def validate_config(self, params, *, secrets_mask=None):
        return ValidationResult.success()

    def validate_and_render(
        self,
        params: dict[str, Any],
        context: WorkflowContext,
        *,
        secrets_mask: list[str] | None = None,
    ) -> dict[str, Any]:
        return context.render_structure(params)

    def poll(self, cfg: RenderedNodeConfig, adapter_state: dict[str, Any] | None) -> UnitOutput:
        return self.dispatch(cfg)

    def cancel(self, cfg: RenderedNodeConfig, adapter_state: dict[str, Any] | None) -> UnitOutput:
        return UnitOutput(
            status=STATUS_SUCCESS,
            output={"cancelled": False, "reason": "Virtual node is terminal, no need to cancel"},
        )


@register_unit_adapter("start")
class StartAdapter(_VirtualBase):
    """Workflow起.节..dispatch 瞬间return SUCCESS, Output started_at."""

    node_type = "start"
    display_name = "Start"
    category = "control"

    def dispatch(self, cfg: RenderedNodeConfig) -> UnitOutput:
        return UnitOutput(
            status=STATUS_SUCCESS,
            output={"started_at": timezone.now().isoformat()},
            exit_code=0,
            summary="Workflow started",
        )


@register_unit_adapter("end")
class EndAdapter(_VirtualBase):
    """Workflow终.节..dispatch 瞬间return SUCCESS, Output finished_at."""

    node_type = "end"
    display_name = "End"
    category = "control"

    def dispatch(self, cfg: RenderedNodeConfig) -> UnitOutput:
        return UnitOutput(
            status=STATUS_SUCCESS,
            output={"finished_at": timezone.now().isoformat()},
            exit_code=0,
            summary="Workflow finished",
        )


@register_unit_adapter("noop")
class NoopAdapter(_VirtualBase):
    """No-op节..可用于debug, Placeholder, 或作为Branch汇聚..

    params.value 会被原样回显到 output.echo, Convenient for下游节.Reference.
    """

    node_type = "noop"
    display_name = "No-op"
    category = "control"

    def validate_config(self, params, *, secrets_mask=None):
        r = ValidationResult.success()
        if "value" in params:
            v = params["value"]
            if not isinstance(v, (str, int, float, list, dict, bool)):
                r.add_error("/params/value", "E0101: value must be JSON serializable")
        return r

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


# ============================================================ 兼容别名Registry
# 早期Frontendstatic节.Definition的 node_type 带 virtual_ Prefix;
# 现Unified推荐去掉Prefixuse start / end / noop(noop 本来就一致), 
# 这里给历史遗留的 virtual_start / virtual_end Registry别名, 保证
# 已Save的WorkflowDraft, 已ReleaseVersion的 graph_definition 仍能正常Check, Execution.
get_registry().register_alias("virtual_start", "start")
get_registry().register_alias("virtual_end", "end")