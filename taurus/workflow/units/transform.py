"""S4-03 Transform Adapter: Data format conversion node.

Synchronous node (requires_host=False, is_asynchronous_human=False),
dispatch selects different engines based on transform_type to convert upstream data:
- jsonpath:  Applies JSONPath expression to input data
- python:    Python expression evaluation (restricted sandbox)
- regex:     Regex match extraction
- script:    Reserved

Error codes:
- E0801: transform_type invalid
- E0802: expression/pattern not filled
- E0803: JSONPath parse failed
- E0804: regex expression invalid
- E0805: Python expression execution exception
"""
from __future__ import annotations

import ast
import json
import logging
import re
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

_ALLOWED_TYPES = {"jsonpath", "python", "regex", "script"}
_ALLOWED_OUTPUT_TYPES = {"string", "number", "boolean", "object"}


@register_unit_adapter("transform")
class TransformAdapter(ExecutableUnit):
    """Data transform节.:对上游Output做Format/值convert."""

    node_type = "transform"
    display_name = "Data Transform"
    requires_host = False
    is_asynchronous_human = False

    def validate_config(self, params: dict, *, secrets_mask=None) -> ValidationResult:
        r = ValidationResult()
        ttype = (params.get("transform_type") or "").strip()
        if not ttype:
            r.add_error("/params/transform_type", "E0801: transform_type is required")
        elif ttype not in _ALLOWED_TYPES:
            r.add_error(
                "/params/transform_type",
                f"E0801: transform_type must be in {_ALLOWED_TYPES}, got {ttype!r}",
            )

        expr = (params.get("expression") or "").strip()
        if not expr:
            r.add_error("/params/expression", "E0802: expression is required")

        if ttype == "regex" and expr:
            try:
                re.compile(expr)
            except re.error as exc:
                r.add_error("/params/expression", f"E0804: Invalid regex: {exc}")

        output_type = (params.get("output_type") or "object").strip()
        if output_type and output_type not in _ALLOWED_OUTPUT_TYPES:
            r.add_error(
                "/params/output_type",
                f"E0801: output_type must be in {_ALLOWED_OUTPUT_TYPES}",
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
            raise ValueError(f"transform validation failed: {vr.errors}")
        return context.render_structure(params)

    def dispatch(self, cfg: RenderedNodeConfig) -> UnitOutput:
        params = cfg.params or {}
        ttype = params.get("transform_type", "python")
        expr = params.get("expression", "")
        output_type = params.get("output_type", "object")
        fallback = params.get("fallback_value")

        try:
            if ttype == "jsonpath":
                raw = self._eval_jsonpath(expr, params)
            elif ttype == "regex":
                raw = self._eval_regex(expr, params)
            elif ttype == "python":
                raw = self._eval_python(expr, params)
            elif ttype == "script":
                raw = self._eval_python(expr, params)
            else:
                raw = fallback

            result = self._coerce_output(raw, output_type)
            return UnitOutput(
                status=STATUS_SUCCESS,
                output={"result": result, "raw_result": raw},
                exit_code=0,
                summary="Data transform succeeded",
            )
        except Exception as exc:
            logger.error("transform execution failed: %s", exc)
            return UnitOutput(
                status=STATUS_FAILED,
                error_message=f"E0805: {exc}",
                exit_code=1,
                output={"result": fallback},
            )

    def poll(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        return UnitOutput(
            status=STATUS_SUCCESS,
            output={"note": "transform is a sync node"},
        )

    def cancel(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        return UnitOutput(
            status=STATUS_CANCELLED,
            output={"cancelled": False, "reason": "transform is a sync node"},
        )

    @staticmethod
    def _eval_jsonpath(expr: str, data: dict[str, Any]) -> Any:
        data_json = json.dumps(data, ensure_ascii=False)
        try:
            import jsonpath_ng.ext
            matches = jsonpath_ng.ext.parse(expr).find(json.loads(data_json))
            return [m.value for m in matches] if matches else None
        except ImportError:
            pass
        except Exception:
            pass
        return TransformAdapter._simple_jsonpath(expr, data)

    @staticmethod
    def _simple_jsonpath(expr: str, data: dict[str, Any]) -> Any:
        if expr.startswith("$."):
            parts = expr[2:].split(".")
            current: Any = data
            for part in parts:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                elif isinstance(current, list) and part.isdigit():
                    idx = int(part)
                    if idx < len(current):
                        current = current[idx]
                    else:
                        return None
                else:
                    return None
            return current
        return None

    @staticmethod
    def _eval_regex(expr: str, data: dict[str, Any]) -> str:
        target = json.dumps(data, ensure_ascii=False)
        match = re.search(expr, target)
        if match:
            return match.group(0)
        return target

    @staticmethod
    def _eval_python(expr: str, variables: dict[str, Any]) -> Any:
        tree = ast.parse(expr, mode="eval")
        forbidden = (
            ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef,
            ast.ClassDef, ast.Global, ast.Nonlocal, ast.Delete, ast.With,
            ast.AsyncWith, ast.Raise, ast.Try, ast.While, ast.For, ast.AsyncFor,
        )
        for node in ast.walk(tree):
            if isinstance(node, forbidden):
                raise ValueError(f"E0805: Expression contains disallowed syntax: {type(node).__name__}")
        safe_builtins = {
            "abs": abs, "len": len, "min": min, "max": max,
            "int": int, "float": float, "str": str, "bool": bool,
            "list": list, "dict": dict, "tuple": tuple, "set": set,
            "round": round, "sum": sum, "any": any, "all": all,
            "True": True, "False": False, "None": None,
        }
        safe_globals = {"__builtins__": safe_builtins}
        safe_globals.update(variables)
        return eval(compile(tree, "<transform>", "eval"), safe_globals)

    @staticmethod
    def _coerce_output(raw: Any, output_type: str) -> Any:
        try:
            if output_type == "string":
                if isinstance(raw, (dict, list)):
                    return json.dumps(raw, ensure_ascii=False)
                return str(raw)
            if output_type == "number":
                return float(raw) if raw is not None else 0
            if output_type == "boolean":
                return bool(raw)
            return raw
        except Exception:
            return raw