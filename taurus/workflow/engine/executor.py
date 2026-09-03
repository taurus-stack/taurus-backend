"""WorkflowEngine Executor pure function core (S1-03).

This module deliberately avoids importing Django ORM for fast unit testing without a database:
- expand_hosts()              # Expand hosts based on node's host_group
- render_node_config()        # Use WorkflowContext to interpolate node params, collect secrets_mask
- evaluate_edge_condition()   # Boolean edge conditions + special "else" keyword
- compute_next_runnables()    # Based on incoming edge status machine, pick next batch of executable nodes
- transition_status()         # Check status machine migration validity (PENDING -> RUNNING -> terminal state)

Actual DB persistence / adapter invocation is handled by the upper layer WorkflowRunner, see engine/runner.py.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from taurus.workflow.engine.context import InterpolationError, WorkflowContext

# Normalize "node config render phase exceptions", convenient for upper layer to catch (ValueError from validity check,
# InterpolationError from WorkflowContext interpolation)
RenderError = (ValueError, InterpolationError)  # type: ignore[assignment]
__all__rendered_docstring__ = True  # Marker: only used for type: ignore suppression

from taurus.workflow.engine.schemas import (
    NO_HOST_SENTINEL,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
)

# ====== Constants: no hardcoding ======
# Valid status transitions: from -> set[to]
VALID_TRANSITIONS: dict[int, set[int]] = {
    STATUS_PENDING: {STATUS_RUNNING, STATUS_SKIPPED, STATUS_CANCELLED},
    STATUS_RUNNING: {STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELLED, STATUS_RUNNING},  # Allow RUNNING->RUNNING for poll to refresh adapter_state
    STATUS_SUCCESS: set(),  # Terminal state
    STATUS_FAILED: set(),
    STATUS_SKIPPED: set(),
    STATUS_CANCELLED: set(),
}
TERMINAL_STATUSES = frozenset((STATUS_SUCCESS, STATUS_FAILED, STATUS_SKIPPED, STATUS_CANCELLED))

# Condition expression syntax (minimal, no expression engine introduced)
# Supports:
#   true / false                     constants
#   success(node_key)                upstream node is SUCCESS
#   failed(node_key)                 upstream node is FAILED
#   status(node_key, 'SUCCESS')      equals a specific status
#   node_key.output.path == value    comparison (== != < > <= >=)
#   NOT (...) / AND(..., ...) / OR(..., ...)
#   __else__                         special constant: means "when all other edges entering this node are false"
_CMP_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_.]*)\s*"
    r"(==|!=|<=|>=|<|>)\s*"
    r"('[^']*'|\"[^\"]*\"|-?\d+(?:\.\d+)?|true|false|null)\s*$"
)


# ---------------------------------------------------------------------- Host expansion
# Compatible with various host field names that frontend may pass (by priority, high→low); singular host_id / host auto-wraps into array
_HOST_LIST_KEYS: tuple[tuple[str, ...], ...] = (
    ("host_ids",),                      # Engine top-level normalization
    ("target_hosts",),                  # Frontend host-selecter standard
    ("hosts",),                         # Common frontend naming (user reports)
    ("host_id",),                       # Singular: single host config
    ("host",),                          # Singular
)
# data.config also iterates the same keys (but singular keys are also allowed)
_NESTED_LIST_KEYS: tuple[str, ...] = ("target_hosts", "host_ids", "hosts", "host_id", "host")
_SINGULAR_HOST_KEYS = frozenset(("host_id", "host"))
# Primary key fields in objects (need to expand .id when frontend passes back host object array)
_HOST_OBJECT_ID_KEYS: tuple[str, ...] = (
    "id", "host_id", "pk", "uuid", "key", "value", "code",
)


def _coerce_host_item(item: Any) -> Any:
    """Normalize a "host list element" into a usable host_id scalar.

    Frontend components may pass back a dict (full host object) rather than a scalar id:
      - {id: 5, name: 'db-01', ...}  →  return '5'
      - {host_id: 'host-abc', ...}    →  return 'host-abc'
      - {value: 123} / {code: 'X'}   →  fallback extraction
      - Already str/int → return as-is
      - Unrecognized non-empty object → as-is (don't discard, let upper layer decide)
    """
    if item is None:
        return None
    if isinstance(item, (str, int)):
        return item
    if isinstance(item, dict):
        for k in _HOST_OBJECT_ID_KEYS:
            v = item.get(k)
            if v is not None and v != "":
                return v
    return None if isinstance(item, (list, tuple)) else item


def _coerce_host_list(raw: Iterable[Any]) -> list[Any]:
    """Normalize a host list into a scalar id list, filter None/empty strings.

    Simultaneously process:
      - Object elements (see _coerce_host_item)
      - Nested arrays (flatten one level, compatible with some frontend components passing [[a,b]])
    """
    if not raw:
        return []
    result: list[Any] = []
    for item in raw:
        if isinstance(item, (list, tuple)) and not isinstance(item, (str, bytes)):
            # Flatten one level
            for sub in item:
                c = _coerce_host_item(sub)
                if c is not None and not (isinstance(c, str) and not c.strip()):
                    result.append(c)
            continue
        c = _coerce_host_item(item)
        if c is None:
            continue
        if isinstance(c, str) and not c.strip():
            continue
        result.append(c)
    return result


def _extract_host_ids_from_node(node: dict[str, Any]) -> list[Any] | None:
    """Extract host_ids list from a node (possibly frontend vue-flow raw structure) with multiple fallback paths.

    Compatible search paths (by priority, return first non-empty list):
      - node.host_ids / node.target_hosts / node.hosts / node.host_id / node.host
      - node["data"]["config"][...] with the same keys
      - node["data"][host_ids / hosts / host_id / host]
      - node["params"][host_ids / hosts / host_id / host]
    Returns None if not found or not a list (caller falls back to __NO_HOST__).
    Return value is already normalized through _coerce_host_list (object→id, flattened, empty filtered).
    """
    if not isinstance(node, dict):
        return None

    # 1) Top-level (singular keys auto-wrap into array)
    for keys in _HOST_LIST_KEYS:
        k = keys[0]
        v = node.get(k)
        if k in _SINGULAR_HOST_KEYS:
            if v is None or v == "":
                continue
            coerced = _coerce_host_list([v])
            if coerced:
                return coerced
        if isinstance(v, list):
            coerced = _coerce_host_list(v)
            if coerced:
                return coerced

    # 2) Under data.config
    data = node.get("data")
    if isinstance(data, dict):
        cfg = data.get("config")
        if isinstance(cfg, dict):
            for k in _NESTED_LIST_KEYS:
                v = cfg.get(k)
                if k in _SINGULAR_HOST_KEYS:
                    if v is None or v == "":
                        continue
                    coerced = _coerce_host_list([v])
                    if coerced:
                        return coerced
                if isinstance(v, list):
                    coerced = _coerce_host_list(v)
                    if coerced:
                        return coerced
        # 3) Directly under data
        for k in _NESTED_LIST_KEYS:
            v = data.get(k)
            if k in _SINGULAR_HOST_KEYS:
                if v is None or v == "":
                    continue
                coerced = _coerce_host_list([v])
                if coerced:
                    return coerced
            if isinstance(v, list):
                coerced = _coerce_host_list(v)
                if coerced:
                    return coerced

    # 4) Under params
    p = node.get("params")
    if isinstance(p, dict):
        for k in _NESTED_LIST_KEYS:
            v = p.get(k)
            if k in _SINGULAR_HOST_KEYS:
                if v is None or v == "":
                    continue
                coerced = _coerce_host_list([v])
                if coerced:
                    return coerced
            if isinstance(v, list):
                coerced = _coerce_host_list(v)
                if coerced:
                    return coerced
    return None


def expand_hosts(
    node: dict[str, Any],
    workflow_hosts: Iterable[str] | None = None,
) -> list[str]:
    """Determine which hosts a node runs on based on node's host_group / host_ids.

    Rules (by priority, return on first match):
      1. Node config explicitly `requires_host: false` → return [NO_HOST_SENTINEL] (no host, e.g. Approval/HTTP Callback)
      2. Node's explicit host list (compatible with various frontend vue-flow nested structures) → return expanded list
      3. Workflow global workflow_hosts (Workflow.hosts id list) non-empty → return global
      4. requires_host=True but no host config → return [] (no valid host, upper layer should error)
      5. requires_host=False (implicit, e.g. unregistered node) → return [NO_HOST_SENTINEL]
    """
    if node.get("requires_host") is False:
        return [NO_HOST_SENTINEL]
    explicit = _extract_host_ids_from_node(node) or []
    if isinstance(explicit, list) and explicit:
        cleaned = [str(h) for h in explicit if str(h).strip()]
        if cleaned:
            return cleaned
    if workflow_hosts:
        cleaned = [str(h) for h in workflow_hosts if str(h).strip()]
        if cleaned:
            return cleaned
    if node.get("requires_host") is True:
        return []
    return [NO_HOST_SENTINEL]


# ---------------------------------------------------------------------- Node vue-flow → Engine normalization helpers
def extract_node_params_from_vueflow(node: dict[str, Any]) -> dict[str, Any]:
    """Extract the actual params body required by adapter from node dict, compatible with multiple structures.

    Engine layer expects `node.params` to be a dict (main parameters body for render/check), but frontend vue-flow nodes
    may place business parameters in nested layers; this function takes the **first non-empty dict found** in order:

      1. node.params             # Already normalized engine standard format
      2. node.data.params        # Old frontend format
      3. node.data.config        # New frontend manifest-driven (AutoNodeForm writes fields under config)
      4. node                    # Fallback: entire node as params (need to strip top-level dirty keys below)
    """
    if not isinstance(node, dict):
        return {}
    p = node.get("params")
    if isinstance(p, dict):
        return p
    data = node.get("data")
    if isinstance(data, dict):
        p = data.get("params")
        if isinstance(p, dict):
            return p
        cfg = data.get("config")
        if isinstance(cfg, dict):
            return cfg
    return {k: v for k, v in node.items() if isinstance(k, str)}


def extract_node_timeout_sec(node: dict[str, Any]) -> int:
    """Extract timeout_sec integer from node (compatible with vue-flow nesting)."""
    if not isinstance(node, dict):
        return 0
    candidates: list[Any] = [
        node.get("timeout_sec"),
        node.get("timeout_seconds"),
        node.get("timeout"),
    ]
    data = node.get("data")
    if isinstance(data, dict):
        cfg = data.get("config")
        if isinstance(cfg, dict):
            candidates.extend([
                cfg.get("timeout_sec"),
                cfg.get("timeout_seconds"),
                cfg.get("timeout"),
            ])
        candidates.extend([
            data.get("timeout_sec"),
            data.get("timeout_seconds"),
            data.get("timeout"),
        ])
    for raw in candidates:
        if raw is None or raw == "":
            continue
        try:
            v = int(raw)
            if v < 0:
                return 0
            return v
        except (TypeError, ValueError):
            continue
    return 0


# ---------------------------------------------------------------------- Node config rendering
class RenderedNodeParams(dict):
    """Rendered node params dict subclass; maintains dict semantics while allowing attached metadata.

    This way callers `dict(x)` only copies dict key-values (existing usage in runner is not affected),
    and can attach extra metadata via `.secrets_mask` / `.timeout_sec` etc.
    """

    pass


def render_node_config(
    adapter: Any,
    node: dict[str, Any],
    workflow_ctx: WorkflowContext,
    host_id: str = NO_HOST_SENTINEL,
    attempt_no: int = 1,
    fallback_fail_strategy: str | None = None,
) -> RenderedNodeParams:
    """S1-03 spec: interpolate node.params via WorkflowContext ${} once,
    then call adapter.validate_and_render(config, context) for adapter's own strict check and secondary render.

    Args:
        adapter: ExecutableUnit subclass instance (provides validate_and_render)
        node: Node raw dict (containing node_key, node_type, params, timeout, etc.)
        workflow_ctx: WorkflowContext, responsible for ${} interpolation
        host_id: Host id; for no-host nodes pass NO_HOST_SENTINEL
        attempt_no: Attempt number (starting from 1)

    Returns:
        RenderedNodeParams (dict subclass, containing rendered params; extended attributes:
        .secrets_mask / .timeout_sec / .max_retries / .retry_delay_ms /
        .fail_strategy / .condition)

    Raises:
        InterpolationError: ${} interpolation failed
        ValueError: adapter validate_and_render returned invalid value
    """
    raw_params = extract_node_params_from_vueflow(node)
    secrets_mask: list[str] = []

    # Defense: if params accidentally contains top-level structural fields due to history/compatibility
    # (nodeType/node_type/host_ids/timeout_sec/fail_strategy/timeout_seconds/target_hosts
    #  etc.), must strip them, otherwise they'll pollute adapter validation checks
    #  (e.g. wait ignores unknown nodeType as normal field which is fine, but may affect
    #  Dependency's explicit "only recognize delay_seconds/wake_at" logic).
    if isinstance(raw_params, dict):
        for _dirty_key in (
            "nodeType", "node_type", "node_key", "node_name",
            "host_ids", "target_hosts", "timeout_sec", "timeout", "timeout_seconds",
            "fail_strategy",
            "max_retries", "retry_delay_ms", "condition",
            "node_label", "label", "id", "position", "data",
        ):
            if _dirty_key in raw_params:
                raw_params = {k: v for k, v in raw_params.items() if k != _dirty_key}

    # 1. First let WorkflowContext recursively interpolate; any return containing secret paths will accumulate mask_paths in ctx
    rendered_params = workflow_ctx.render_structure(raw_params)
    # Merge masked JSON Pointers from current node's parameters
    secrets_mask.extend(getattr(workflow_ctx, "last_masked_paths", []) or [])

    # 2. Call adapter's layer strict check + secondary render
    final_params = adapter.validate_and_render(rendered_params, workflow_ctx)
    if final_params is None:
        raise ValueError(
            f"E1000: adapter.validate_and_render returned None "
            f"(node_type={getattr(adapter, 'node_type', '?')})"
        )
    if not isinstance(final_params, dict):
        raise ValueError(
            f"E1002: adapter.validate_and_render must return dict, "
            f"actual returned {type(final_params).__name__} "
            f"(node_type={getattr(adapter, 'node_type', '?')})"
        )

    # 3. Then supplement adapter's own declared secret pointers (via masked_paths extended attribute)
    extra = getattr(final_params, "masked_paths", None) or []
    for p in extra:
        if isinstance(p, str) and p.startswith("/") and p not in secrets_mask:
            secrets_mask.append(p)

    timeout_sec = extract_node_timeout_sec(node)

    out = RenderedNodeParams(final_params)
    out.secrets_mask = secrets_mask  # type: ignore[attr-defined]
    out.timeout_sec = timeout_sec  # type: ignore[attr-defined]
    out.max_retries = int(node.get("max_retries", 0) or 0)  # type: ignore[attr-defined]
    out.retry_delay_ms = int(node.get("retry_delay_ms", 0) or 0)  # type: ignore[attr-defined]
    # Backward compat: old DAG versions had fail_strategy='fail_fast' on every node
    # as a default. Treat it as "not set" when fallback is different.
    node_fs = node.get("fail_strategy")
    if node_fs == "fail_fast" and fallback_fail_strategy and fallback_fail_strategy != "fail_fast":
        node_fs = None
    out.fail_strategy = str(node_fs or fallback_fail_strategy or "fail_fast")  # type: ignore[attr-defined]
    out.condition = str(node.get("condition") or "")  # type: ignore[attr-defined]
    out.node_key = str(node.get("node_key"))  # type: ignore[attr-defined]
    out.node_type = str(node.get("node_type"))  # type: ignore[attr-defined]
    out.node_name = str(node.get("node_name") or node.get("node_key"))  # type: ignore[attr-defined]
    out.host_id = host_id  # type: ignore[attr-defined]
    out.attempt_no = int(attempt_no)  # type: ignore[attr-defined]
    return out


# ---------------------------------------------------------------------- Edge condition evaluation
class _ElseToken:
    """Placeholder token: when edge has condition='__else__', return this token for upper layer to judge."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "__else__"


_ELSE = _ElseToken()

# Simple tokenizer: split by , () and whitespace, but keep string literals
_TOK_RE = re.compile(r"('[^']*'|\"[^\"]*\"|\(|\)|,|[A-Za-z_][A-Za-z0-9_.]*|==|!=|<=|>=|<|>|-?\d+(?:\.\d+)?)\s*")


def _tokenize(expr: str) -> list[str]:
    # First process <= >= == !=, already included in regex with priority long match
    tokens: list[str] = []
    pos = 0
    while pos < len(expr):
        if expr[pos].isspace():
            pos += 1
            continue
        m = _TOK_RE.match(expr, pos)
        if not m:
            raise ValueError(f"E1100: unrecognized character in condition expression @{pos}: {expr[pos:pos + 20]!r}")
        tokens.append(m.group(1))
        pos = m.end()
    return tokens


def _parse_literal(tok: str) -> Any:
    if tok in ("true", "True"):
        return True
    if tok in ("false", "False"):
        return False
    if tok in ("null", "None"):
        return None
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ("'", '"'):
        return tok[1:-1]
    try:
        if "." in tok:
            return float(tok)
        return int(tok)
    except ValueError:
        raise ValueError(f"E1101: not a valid literal {tok!r}")


def _eval_atom(tokens: list[str], idx: int, ctx: dict[str, Any]) -> tuple[Any, int]:
    """
    Parse minimal unit:
      - '(' expr ')'
      - constants true/false/null/number/string
      - success(key) / failed(key) / skipped(key) / cancelled(key) / status(key, 'X')
      - NOT ( expr )
      - AND ( expr, expr, ... ) / OR ( expr, expr, ... )
      - __else__ placeholder
      - <path> <cmp> <literal>
    """
    if idx >= len(tokens):
        raise ValueError("E1102: expression unexpectedly ended")
    t = tokens[idx]

    if t == "(":
        v, j = _eval_expr(tokens, idx + 1, ctx)
        if j >= len(tokens) or tokens[j] != ")":
            raise ValueError("E1103: missing matching ')'")
        return v, j + 1

    # Function / identifier form
    if t in ("success", "failed", "skipped", "cancelled", "status") and idx + 1 < len(tokens) and tokens[idx + 1] == "(":
        fn = t
        j = idx + 2
        args: list[Any] = []
        is_first_arg = True
        while j < len(tokens) and tokens[j] != ")":
            if args and tokens[j] != ",":
                raise ValueError(f"E1104: {fn}() missing ',' between arguments")
            if tokens[j] == ",":
                j += 1
                if j >= len(tokens) or tokens[j] == ")":
                    raise ValueError(f"E1105: {fn}() missing argument at end")
                is_first_arg = False
            if is_first_arg:
                # First argument allows writing node_key directly (identifier without quotes)
                tok_first = tokens[j]
                if (
                    re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tok_first)
                    and tok_first not in ("true", "false", "null", "True", "False", "None")
                ):
                    args.append(tok_first)
                    j += 1
                    is_first_arg = False
                    continue
            a, j = _eval_atom(tokens, j, ctx)
            args.append(a)
            is_first_arg = False
        if j >= len(tokens):
            raise ValueError(f"E1106: {fn}() missing ')'")
        j += 1  # Skip )
        if not args or not isinstance(args[0], str):
            raise ValueError(f"E1107: {fn}() first argument must be node_key string")
        key = args[0]
        statuses = ctx.get("node_statuses", {}) or {}
        st = statuses.get(key)
        status_map = {
            "success": STATUS_SUCCESS,
            "failed": STATUS_FAILED,
            "skipped": STATUS_SKIPPED,
            "cancelled": STATUS_CANCELLED,
        }
        if fn in status_map:
            if len(args) != 1:
                raise ValueError(f"E1108: {fn}() must have exactly 1 argument")
            return st == status_map[fn], j
        if fn == "status":
            if len(args) != 2 or not isinstance(args[1], str):
                raise ValueError("E1109: status(k, 'SUCCESS') requires 2 arguments with the second being a string status name")
            expected_map = {
                "PENDING": STATUS_PENDING,
                "RUNNING": STATUS_RUNNING,
                "SUCCESS": STATUS_SUCCESS,
                "FAILED": STATUS_FAILED,
                "SKIPPED": STATUS_SKIPPED,
                "CANCELLED": STATUS_CANCELLED,
            }
            if args[1] not in expected_map:
                raise ValueError(f"E1110: status() unknown status name {args[1]!r}")
            return st == expected_map[args[1]], j
        # Unreachable
        raise ValueError(f"E1111: unknown function {fn}")

    if t in ("AND", "OR", "NOT") and idx + 1 < len(tokens) and tokens[idx + 1] == "(":
        fn = t
        j = idx + 2
        args: list[Any] = []
        while j < len(tokens) and tokens[j] != ")":
            if args and tokens[j] != ",":
                raise ValueError(f"E1112: {fn}() missing ',' between arguments")
            if tokens[j] == ",":
                j += 1
            a, j = _eval_expr(tokens, j, ctx)
            args.append(a)
        if j >= len(tokens):
            raise ValueError(f"E1113: {fn}() missing ')'")
        j += 1
        if fn == "NOT":
            if len(args) != 1:
                raise ValueError("E1114: NOT() must have exactly 1 argument")
            v = args[0]
            if isinstance(v, _ElseToken):
                raise ValueError("E1115: NOT() cannot operate on __else__")
            return (not bool(v)), j
        if fn == "AND":
            # AND() with zero arguments = True (but __else__ as any arg will bubble up)
            r: Any = True
            for a in args:
                if isinstance(a, _ElseToken):
                    r = _ELSE  # Still bubbles to outer layer; upper layer compute_runnables will process
                    continue
                if not bool(a):
                    return False, j
            return r, j
        # OR
        r = False
        for a in args:
            if isinstance(a, _ElseToken):
                r = _ELSE
                continue
            if bool(a):
                return True, j
        return r, j

    if t == "__else__":
        return _ELSE, idx + 1

    # Constant literal
    try:
        v = _parse_literal(t)
        return v, idx + 1
    except ValueError:
        pass

    # Otherwise try <path> <cmp> <literal>
    # We greedily read up to 3 tokens to attempt matching
    if idx + 2 < len(tokens) and tokens[idx + 1] in ("==", "!=", "<", ">", "<=", ">="):
        path = t
        op = tokens[idx + 1]
        try:
            rhs = _parse_literal(tokens[idx + 2])
        except ValueError:
            raise ValueError(f"E1116: invalid right side of comparison {tokens[idx + 2]!r}")
        # Parse lhs: path supports x.output.y / x.status / workflow.env.X
        lhs = _resolve_value_path(path, ctx)
        if isinstance(lhs, _ElseToken):
            raise ValueError("E1117: __else__ placeholder appeared in comparison expression")
        try:
            if op == "==":
                result = lhs == rhs
            elif op == "!=":
                result = lhs != rhs
            elif op == "<":
                result = lhs < rhs
            elif op == ">":
                result = lhs > rhs
            elif op == "<=":
                result = lhs <= rhs
            else:  # >=
                result = lhs >= rhs
        except TypeError as exc:
            raise ValueError(f"E1118: incompatible types in comparison operation: {exc}")
        return bool(result), idx + 3

    raise ValueError(f"E1119: unrecognized token {t!r}")


def _resolve_value_path(path: str, ctx: dict[str, Any]) -> Any:
    """Parse out the value path in the comparison expression (consistent with WorkflowContext's ${...} rules).
    Supports:
      workflow.env.xxx
      trigger.xxx
      <node_key>.status      → int
      <node_key>.output.xxx  → arbitrary JSON value
    """
    if not path:
        raise ValueError("E1120: empty path")
    steps = path.split(".")
    if steps[0] == "workflow":
        d = ctx.get("workflow") or {}
        for s in steps[1:]:
            if not isinstance(d, dict):
                raise ValueError(f"E1121: path {path!r} is not a dict at step {s!r}")
            if s not in d:
                return None
            d = d[s]
        return d
    if steps[0] == "trigger":
        d = ctx.get("trigger") or {}
        for s in steps[1:]:
            if not isinstance(d, dict):
                raise ValueError(f"E1122: path {path!r} is not a dict at step {s!r}")
            if s not in d:
                return None
            d = d[s]
        return d
    # Other prefixes are treated as node_key (allow node_key to have '.'? forbid here to avoid ambiguity)
    if "." in steps[0]:
        raise ValueError(f"E1123: path prefix must be workflow/trigger/<node_key>, must not contain '.'：{steps[0]!r}")
    node_key = steps[0]
    if len(steps) < 2 or steps[1] not in ("status", "output"):
        raise ValueError(
            f"E1124: node value reference must be in form <node>.status or <node>.output.xxx, actual {path!r}"
        )
    outputs = ctx.get("node_outputs") or {}
    statuses = ctx.get("node_statuses") or {}
    if steps[1] == "status":
        if len(steps) != 2:
            raise ValueError(f"E1125: <node>.status does not allow further drilling down：{path!r}")
        return statuses.get(node_key)
    # Output drilling
    d = outputs.get(node_key)
    if d is None:
        return None
    for s in steps[2:]:
        if isinstance(d, dict):
            d = d.get(s)
        elif isinstance(d, list):
            try:
                d = d[int(s)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return d


def _eval_expr(tokens: list[str], idx: int, ctx: dict[str, Any]) -> tuple[Any, int]:
    """Top-level expression: currently we write AND/OR/NOT all in function form (xxx(...)), to avoid operator priority hell.
    Top level = one atom."""
    return _eval_atom(tokens, idx, ctx)


def evaluate_edge_condition(
    condition_str: str,
    ctx: dict[str, Any],
) -> bool | _ElseToken:
    """Evaluate the condition on an edge.

    Args:
        condition_str: The condition written on the edge. Empty string = true (proceed as long as upstream succeeds),
            but this function does not do "whether upstream succeeded" judgement; that's the responsibility of compute_next_runnables.
            This function only evaluates the string itself.
            Special: `__else__` (case-sensitive) → returns _ElseToken singleton, meaning "True only when all other incoming edges are False".
        ctx: Evaluation context dictionary, containing {workflow:{env:{}}, trigger:{}, node_statuses:{k:int}, node_outputs:{k:dict}}

    Returns:
        True / False / _ELSE (_ELSE is a placeholder token, only recognized by compute_runnables layer)

    Raises:
        ValueError (E11xx): Syntax error
    """
    if condition_str is None or not str(condition_str).strip():
        return True
    s = condition_str.strip()
    if s == "__else__":
        return _ELSE
    tokens = _tokenize(s)
    if not tokens:
        return True
    result, pos = _eval_expr(tokens, 0, ctx)
    if pos != len(tokens):
        raise ValueError(
            f"E1126: expression has extra content at token {pos} (possibly mismatched parentheses): {tokens[pos:]!r}"
        )
    return result


# ---------------------------------------------------------------------- Next batch of executable nodes
class NodeStatusView:
    """Minimal data view required by compute_next_runnables — decouples from database rows."""

    __slots__ = ("node_key", "status", "is_expanded", "fail_strategy")

    def __init__(self, node_key: str, status: int, is_expanded: bool, fail_strategy: str = "fail_fast"):
        """
        Args:
            status: int (0~5)
            is_expanded: True means this node has already done expand_hosts and created host-granularity execution rows.
                Even if all host rows are in terminal state, if is_expanded=False, we still consider this node "not yet executing".
            fail_strategy: Node-level failure strategy (fail_fast / continue), used to determine whether empty-condition edges allow
                FAILED source nodes to resume triggering downstream.
        """
        self.node_key = node_key
        self.status = int(status)
        self.is_expanded = bool(is_expanded)
        self.fail_strategy = str(fail_strategy)


def _host_view_status(per_host_rows: Iterable[dict[str, Any]] | None) -> tuple[int, bool, str]:
    """Collapse execution rows of the same node across multiple hosts into a "node-level view" (status, is_expanded, fail_strategy).

    Collapse rules (conservative semantics):
      - No host rows at all → is_expanded=False, status=PENDING
      - Any row RUNNING → node view RUNNING
      - Otherwise any row FAILED + fail_strategy=fail_fast → node view FAILED
      - Otherwise all reached terminal state: if all SUCCESS → SUCCESS; if all SKIPPED → SKIPPED; otherwise if at least one row FAILED → FAILED; otherwise mixed → SUCCESS
      - Any CANCELLED → CANCELLED takes priority
    """
    rows = list(per_host_rows or [])
    if not rows:
        return STATUS_PENDING, False, "fail_fast"
    statuses = [int(r.get("status", STATUS_PENDING)) for r in rows]
    strategies = [str(r.get("fail_strategy", "fail_fast")) for r in rows]

    if any(s == STATUS_CANCELLED for s in statuses):
        return STATUS_CANCELLED, True, strategies[0]
    if any(s == STATUS_RUNNING for s in statuses):
        return STATUS_RUNNING, True, strategies[0]
    # All terminal state
    all_terminal = all(s in TERMINAL_STATUSES for s in statuses)
    if not all_terminal:
        # PENDING appears but has rows → waiting for dispatch, conservatively return RUNNING
        return STATUS_RUNNING, True, strategies[0]
    # fail_fast FAILED directly marks node as FAILED
    for s, strat in zip(statuses, strategies):
        if s == STATUS_FAILED and strat == "fail_fast":
            return STATUS_FAILED, True, strategies[0]
    if all(s == STATUS_SUCCESS for s in statuses):
        return STATUS_SUCCESS, True, strategies[0]
    if all(s == STATUS_SKIPPED for s in statuses):
        return STATUS_SKIPPED, True, strategies[0]
    if any(s == STATUS_FAILED for s in statuses):
        return STATUS_FAILED, True, strategies[0]
    return STATUS_SUCCESS, True, strategies[0]


def normalize_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Standardize edges to {from, to, condition} format, compatible with aliases like {from_key, to_key} / {src, dst}."""
    if not edges:
        return []
    normalized: list[dict[str, Any]] = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        src = (
            e.get("from")
            or e.get("from_key")
            or e.get("src")
            or e.get("source")
        )
        dst = (
            e.get("to")
            or e.get("to_key")
            or e.get("dst")
            or e.get("target")
        )
        normalized.append({
            "from": src,
            "to": dst,
            "condition": e.get("condition") or e.get("cond") or "",
        })
    return normalized


def compute_next_runnables(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    node_execution_rows: dict[str, list[dict[str, Any]]],
    edge_eval_ctx: dict[str, Any],
    *,
    execution_fail_strategy: str = "fail_fast",
) -> list[str]:
    """Give the next batch of node_keys that "can be expanded/dispatched" (approximate topological order, dependencies first).

    Args:
        nodes: DAG nodes list
        edges: DAG edges list (compatible with {from,to} / {from_key,to_key} / {src,dst})
        node_execution_rows: {node_key: [ {status, fail_strategy, ...} ... ]} host-granularity DB row view
        edge_eval_ctx: Context for evaluate_edge_condition (containing node_statuses / node_outputs / workflow / trigger)
        execution_fail_strategy: Execution-level failure strategy, for backward compatibility with old DAG data
    """
    edges = normalize_edges(edges)   # ← First normalize aliases
    key_to_node = {str(n.get("node_key")): n for n in nodes}
    exec_fs = str(execution_fail_strategy or "fail_fast")
    # Build node-level views
    node_views: dict[str, NodeStatusView] = {}
    for k, node in key_to_node.items():
        nt = node.get("node_type")
        rows = node_execution_rows.get(k) or []
        if nt == "start":
            # start/end are special control nodes, don't expand, don't create host rows, but need to "be treated as SUCCESS reached" so downstream can resume
            node_views[k] = NodeStatusView(k, STATUS_SUCCESS, is_expanded=True)
            continue
        if nt == "end":
            node_views[k] = NodeStatusView(k, STATUS_PENDING, is_expanded=False)
            continue
        st, expanded, f_strat = _host_view_status(rows)
        # Backward compat: in old DAG data, node's fail_strategy='fail_fast' was the default value,
        # when execution-level strategy is not 'fail_fast', ignore node-level value and use execution-level strategy.
        resolved_fs = str(f_strat)
        if resolved_fs == "fail_fast" and exec_fs != "fail_fast":
            resolved_fs = exec_fs
        node_views[k] = NodeStatusView(k, st, expanded, fail_strategy=resolved_fs)

    # Sync node views into node_statuses in edge_eval_ctx (if caller didn't provide their own)
    if "node_statuses" not in edge_eval_ctx or edge_eval_ctx["node_statuses"] is None:
        edge_eval_ctx["node_statuses"] = {k: v.status for k, v in node_views.items()}
    else:
        # Merge: for those not externally specified, fill them in
        for k, v in node_views.items():
            edge_eval_ctx["node_statuses"].setdefault(k, v.status)

    reverse_edges: dict[str, list[dict[str, Any]]] = {k: [] for k in key_to_node}
    for e in edges:
        target = str(e.get("to", ""))
        if target in reverse_edges:
            reverse_edges[target].append(e)

    # === Global edges preprocessing: first get each edge's judgement, then normalize __else__ for "same src" ===
    edge_raw: list[Any] = [False] * len(edges)
    for i, e in enumerate(edges):
        src = str(e.get("from", ""))
        src_view = node_views.get(src)
        if src_view is None or src_view.status not in TERMINAL_STATUSES:
            continue
        cond = str(e.get("condition") or "")
        # __from__ is syntactic sugar: success(__from__) / status(__from__, ...) when writing edge condition doesn't need to repeat source node name
        if src and "__from__" in cond:
            # Simple string replace: __from__ itself is not a valid token character (identifiers can't have mixed underscores?
            # actually our tokenizer allows identifiers starting with '_'; so to be safe use string replace + edge boundary check.
            cond_escaped = src.replace("'", "\\'")
            cond = cond.replace("__from__", "'" + cond_escaped + "'")
        # Empty condition: by default only considered True when source node SUCCESS.
        # If source node fail_strategy=continue, even FAILED allows downstream nodes to continue execution
        # (CANCELLED/SKIPPED still block, because they don't have actual failure termination semantics)
        if not cond.strip():
            if src_view.status == STATUS_SUCCESS:
                edge_raw[i] = True
            elif src_view.status == STATUS_FAILED and src_view.fail_strategy == "continue":
                edge_raw[i] = True
            else:
                edge_raw[i] = False
        else:
            try:
                edge_raw[i] = evaluate_edge_condition(cond, edge_eval_ctx)
            except ValueError:
                edge_raw[i] = False

    # Normalize outgoing edges from the same src: if non-else has True → all __else__ for that src are treated as False
    src_out: dict[str, list[int]] = {}
    for i, e in enumerate(edges):
        src_out.setdefault(str(e.get("from", "")), []).append(i)

    edge_bool: list[bool] = [False] * len(edges)
    for src, idxs in src_out.items():
        has_non_else_true = False
        for i in idxs:
            r = edge_raw[i]
            if isinstance(r, _ElseToken):
                continue
            if r:
                has_non_else_true = True
                break
        src_view = node_views.get(src)
        src_cancel = src_view is not None and src_view.status == STATUS_CANCELLED
        for i in idxs:
            r = edge_raw[i]
            if isinstance(r, _ElseToken):
                edge_bool[i] = bool((not has_non_else_true) and (not src_cancel))
            else:
                edge_bool[i] = bool(r)

    edge_idx_map: dict[int, int] = {id(e): i for i, e in enumerate(edges)}

    runnables: list[str] = []
    for k, view in node_views.items():
        if view.status != STATUS_PENDING:
            continue
        if view.is_expanded:
            continue
        node = key_to_node[k]
        if node.get("node_type") == "start":
            continue
        incoming = reverse_edges.get(k) or []
        if not incoming:
            runnables.append(k)
            continue

        all_term = True
        any_hit = False
        for e in incoming:
            src = str(e.get("from", ""))
            sv = node_views.get(src)
            if sv is None or sv.status not in TERMINAL_STATUSES:
                all_term = False
                break
            if edge_bool[edge_idx_map[id(e)]]:
                any_hit = True
        if not all_term:
            continue
        if any_hit:
            runnables.append(k)
            continue
    return runnables


# ---------------------------------------------------------------------- Status transition validity
def transition_status(current: int, target: int) -> int:
    """
    Check and "return target" (for upper layer to directly save to DB). Raises ValueError if not valid.
    """
    if not isinstance(current, int) or not isinstance(target, int):
        raise ValueError(f"E1200: status must be int, actual current={type(current).__name__} target={type(target).__name__}")
    allowed = VALID_TRANSITIONS.get(current)
    if allowed is None:
        raise ValueError(f"E1201: unknown current status value {current}")
    if target not in allowed:
        raise ValueError(
            f"E1202: invalid status transition: {current} -> {target} "
            f"(allowed: {sorted(allowed) if allowed else '(terminal state, no transitions allowed)'})"
        )
    return target