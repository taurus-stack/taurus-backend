"""S4-05 Loop Adapter: Loop control node.

Synchronous node (requires_host=False, is_asynchronous_human=False),
dispatch parses loop config, computes iteration count/list/conditions, records to output.adapter_state.
Actual loop expansion is completed by WorkflowEngine at the orchestration layer.

Three loop modes:
- count:      Fixed count, generates range(count)
- for_each:   Iterates list, items as iterable objects
- while:      Conditional loop, terminates when condition expression is false (max max_iterations)

Error codes:
- E1001: loop_type invalid
- E1002: count not filled or range invalid
- E1003: items not filled or not iterable
- E1004: max_concurrency range invalid
"""
from __future__ import annotations

import logging
from typing import Any

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

_ALLOWED_TYPES = {"count", "for_each", "while"}
_MAX_ITERATIONS = 10000
_MAX_CONCURRENCY = 64


@register_unit_adapter("loop")
class LoopAdapter(ExecutableUnit):
    """Loop control node: provides iteration metadata to the engine."""

    node_type = "loop"
    display_name = "Loop Node"
    requires_host = False
    is_asynchronous_human = False

    def validate_config(self, params: dict, *, secrets_mask=None) -> ValidationResult:
        r = ValidationResult()
        ltype = (params.get("loop_type") or "").strip()
        if not ltype:
            r.add_error("/params/loop_type", "E1001: loop_type is required")
        elif ltype not in _ALLOWED_TYPES:
            r.add_error("/params/loop_type", f"E1001: loop_type must be in {_ALLOWED_TYPES}")

        if ltype == "count":
            count = params.get("count")
            if count is None:
                r.add_error("/params/count", "E1002: count is required under count mode")
            else:
                try:
                    c = int(count)
                    if c < 1 or c > _MAX_ITERATIONS:
                        r.add_error("/params/count", f"E1002: count range [1, {_MAX_ITERATIONS}]")
                except (TypeError, ValueError):
                    r.add_error("/params/count", "E1002: count must be an integer")

        if ltype == "for_each":
            items_source = params.get("items_source") or "manual"
            if items_source == "manual":
                items = params.get("items")
                if items is None or (isinstance(items, (list, tuple)) and len(items) == 0):
                    r.add_error("/params/items", "E1003: items is required and non-empty under for_each manual mode")
            elif items_source == "upstream":
                items_ref = (params.get("items_ref") or "").strip()
                if not items_ref:
                    r.add_error("/params/items_ref", "E1003: items_ref is required under for_each upstream reference mode")

        if ltype in ("count", "for_each"):
            body_nt = (params.get("body_node_type") or "").strip()
            if not body_nt:
                r.add_error("/params/body_node_type", "E1005: body_node_type is required under count/for_each mode")

        if ltype == "while":
            cond = (params.get("condition_expression") or "").strip()
            if not cond:
                r.add_error("/params/condition_expression", "E1003: condition_expression is required under while mode")

        concurrency = params.get("max_concurrency")
        if concurrency is not None:
            try:
                c = int(concurrency)
                if c < 1 or c > _MAX_CONCURRENCY:
                    r.add_error("/params/max_concurrency", f"E1004: max_concurrency range [1, {_MAX_CONCURRENCY}]")
            except (TypeError, ValueError):
                r.add_error("/params/max_concurrency", "E1004: max_concurrency must be an integer")

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
            raise ValueError(f"Loop validation failed: {vr.errors}")
        return context.render_structure(params)

    def dispatch(self, cfg: RenderedNodeConfig) -> UnitOutput:
        params = cfg.params or {}
        ltype = params.get("loop_type", "count")

        try:
            if ltype == "count":
                count = int(params.get("count", 1))
                loop_items = list(range(count))
                loop_count = count

            elif ltype == "for_each":
                items_source = params.get("items_source") or "manual"
                if items_source == "upstream" and params.get("items_ref"):
                    loop_items = []
                    loop_count = 0
                else:
                    items = params.get("items") or []
                    if isinstance(items, str):
                        import json
                        try:
                            items = json.loads(items)
                        except Exception:
                            items = []
                    if not hasattr(items, "__iter__"):
                        items = []
                    loop_items = list(items)
                    loop_count = len(loop_items)

            elif ltype == "while":
                loop_items = []
                loop_count = 0

            else:
                return UnitOutput(
                    status=STATUS_FAILED,
                    error_message=f"E1001: loop_type={ltype} invalid",
                    exit_code=1,
                )

            output_state = {
                "loop_type": ltype,
                "loop_count": loop_count,
                "loop_items": loop_items,
                "max_concurrency": int(params.get("max_concurrency") or 1),
                "break_on_error": bool(params.get("break_on_error", True)),
                "body_node_type": str(params.get("body_node_type") or ""),
                "body_params": params.get("body_params") or {},
                "aggregation": str(params.get("aggregation") or "collect_all"),
                "fail_strategy": str(params.get("fail_strategy") or "fail_fast"),
            }
            if ltype == "while":
                output_state["loop_condition"] = params.get("condition_expression", "")
                output_state["loop_max_iterations"] = int(params.get("max_iterations") or _MAX_ITERATIONS)
            if ltype == "for_each":
                output_state["items_source"] = str(params.get("items_source") or "manual")
                if params.get("items_source") == "upstream" and params.get("items_ref"):
                    output_state["items_ref"] = params["items_ref"]

            return UnitOutput(
                status=STATUS_SUCCESS,
                output=output_state,
                adapter_state=output_state,
                exit_code=0,
                summary=f"Loop node initialized: type={ltype}, count={loop_count}",
            )
        except Exception as exc:
            logger.error("loop dispatch failed: %s", exc)
            return UnitOutput(
                status=STATUS_FAILED,
                error_message=f"E1003: {exc}",
                exit_code=1,
            )

    def poll(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        return UnitOutput(
            status=STATUS_SUCCESS,
            output={"note": "loop is a synchronous node"},
        )

    def cancel(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        return UnitOutput(
            status=STATUS_CANCELLED,
            output={"cancelled": False, "reason": "loop is a synchronous node"},
        )