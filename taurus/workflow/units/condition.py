"""S4-02 Condition Adapter: Conditional judgment node.

Synchronous node (requires_host=False, is_asynchronous_human=False),
dispatch directly evaluates expression, returns true / false / error.
WorkflowEngine determines which output edge to follow based on the returned branch marker (branch_true / branch_false).

Supported expression syntax:
- simple:  "value1 == value2" / "value1 != value2" / "value > 10"
- python:  Python expression (sandbox environment, allowlist builtins only)

Error codes:
- E0701: expression not filled
- E0702: expression syntax error / execution exception
- E0703: comparison operator invalid (simple mode)
"""
from __future__ import annotations

import ast
import logging
import operator
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

_ALLOWED_OPS = {
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
}

_SAFE_BUILTINS = {
    "abs": abs, "len": len, "min": min, "max": max,
    "int": int, "float": float, "str": str, "bool": bool,
    "list": list, "dict": dict, "tuple": tuple, "set": set,
    "round": round, "sum": sum, "any": any, "all": all,
    "True": True, "False": False, "None": None,
}


@register_unit_adapter("condition")
class ConditionAdapter(ExecutableUnit):
    """entries件判断:according toExpression求值分流Workflow."""

    node_type = "condition"
    display_name = "Condition"
    requires_host = False
    is_asynchronous_human = False

    def validate_config(self, params: dict, *, secrets_mask=None) -> ValidationResult:
        r = ValidationResult()
        expr = (params.get("expression") or "").strip()
        if not expr:
            r.add_error("/params/expression", "E0701: Expression is required")

        expr_type = (params.get("expression_type") or "simple").strip()
        if expr_type not in {"simple", "python"}:
            r.add_error(
                "/params/expression_type",
                f"E0701: expression_type must be in {{simple, python}}, got {expr_type!r}",
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
            raise ValueError(f"condition validation failed: {vr.errors}")
        return context.render_structure(params)

    def dispatch(self, cfg: RenderedNodeConfig) -> UnitOutput:
        params = cfg.params or {}
        expr = params.get("expression", "")
        expr_type = params.get("expression_type", "simple")

        try:
            if expr_type == "simple":
                result = self._eval_simple(expr, params)
            elif expr_type == "python":
                result = self._eval_python(expr, params)
            else:
                result = self._eval_python(expr, params)

            branch = "true" if result else "false"
            return UnitOutput(
                status=STATUS_SUCCESS,
                output={
                    "condition_result": bool(result),
                    "branch": branch,
                },
                exit_code=0,
                summary=f"Condition result={result}, taking {branch} branch",
            )
        except Exception as exc:
            logger.error("E0702: Condition evaluation failed: %s", exc)
            return UnitOutput(
                status=STATUS_FAILED,
                error_message=f"E0702: {exc}",
                exit_code=1,
                output={
                    "condition_result": False,
                    "branch": "error",
                },
            )

    def poll(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        return UnitOutput(
            status=STATUS_SUCCESS,
            output={"note": "condition is a sync node, poll should not be called"},
        )

    def cancel(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        return UnitOutput(
            status=STATUS_CANCELLED,
            output={"cancelled": False, "reason": "condition is a sync node, no need to cancel"},
        )

    @staticmethod
    def _eval_simple(expr: str, variables: dict[str, Any]) -> bool:
        for op_name, op_fn in _ALLOWED_OPS.items():
            if op_name in expr:
                parts = expr.split(op_name, 1)
                if len(parts) == 2:
                    left = ConditionAdapter._resolve_value(parts[0].strip(), variables)
                    right = ConditionAdapter._resolve_value(parts[1].strip(), variables)
                    return bool(op_fn(left, right))
        raise ValueError(f"E0703: No valid comparison operator found: {expr}")

    @staticmethod
    def _eval_python(expr: str, variables: dict[str, Any]) -> bool:
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as exc:
            raise ValueError(f"E0702: Python expression syntax error: {exc}") from exc

        _validate_ast_safe(tree)

        safe_globals = {"__builtins__": _SAFE_BUILTINS}
        safe_globals.update(variables)
        result = eval(compile(tree, "<condition>", "eval"), safe_globals)
        return bool(result)

    @staticmethod
    def _resolve_value(token: str, variables: dict[str, Any]) -> Any:
        if token.startswith('"') and token.endswith('"'):
            return token[1:-1]
        if token.startswith("'") and token.endswith("'"):
            return token[1:-1]
        try:
            return int(token)
        except ValueError:
            pass
        try:
            return float(token)
        except ValueError:
            pass
        return variables.get(token, token)


def _validate_ast_safe(tree: ast.AST) -> None:
    forbidden = (
        ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef,
        ast.ClassDef, ast.Global, ast.Nonlocal, ast.Delete, ast.With,
        ast.AsyncWith, ast.Raise, ast.Try, ast.While, ast.For, ast.AsyncFor,
        ast.GeneratorExp, ast.ListComp, ast.DictComp, ast.SetComp,
    )
    for node in ast.walk(tree):
        if isinstance(node, forbidden):
            raise ValueError(f"E0702: Expression contains disallowed syntax: {type(node).__name__}")