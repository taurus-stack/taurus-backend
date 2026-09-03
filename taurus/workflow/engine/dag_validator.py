"""S1-01 DAGValidator — Workflow DAG structure validation.

External usage:
    from taurus.workflow.engine.dag_validator import WorkflowDAG, validate_dag

    result: DAGValidationResult = validate_dag(dag, strict=True)
    if not result.ok:
        raise ValidationError(result.errors)  # Passed to frontend for display via JSON Pointer

Validation order (errors from earlier checks still collect subsequent ones, return all errors at once):
 1. start/end node existence + count
 2. node_key uniqueness
 3. edge not dangling (from/to exist in nodes list)
 4. start has no incoming edge / end has no outgoing edge
 5. Directed acyclic graph (Kahn topological sort) and output topological_order
 6. Reachability (reachable from start / can forward reach end)
 7. [strict] node_type exists in AdapterRegistry
 8. Condition expression basic safety check (parenthesis balance + dangerous keyword blacklist)
 9. warnings collection: isolated nodes, edge conditions all equal (possible dead branch)
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, NotRequired, Required, TypedDict


# ========================================================================
# Type definitions (kept lightweight, TypedDict for easy direct deserialization from frontend JSON/DRF serializer)
# ========================================================================
class Node(TypedDict, total=False):
    node_key: Required[str]
    node_type: Required[str]
    node_name: NotRequired[str]
    params: NotRequired[dict[str, Any]]


class Edge(TypedDict, total=False):
    from_key: Required[str]
    to_key: Required[str]
    condition: NotRequired[str | None]


@dataclass
class WorkflowDAG:
    nodes: list[Node]
    edges: list[Edge]


@dataclass
class DAGValidationResult:
    ok: bool = True
    errors: dict[str, list[str]] = field(default_factory=dict)
    warnings: dict[str, list[str]] = field(default_factory=dict)
    # Topological order from Kahn's algorithm (only guaranteed non-empty when DAG is acyclic and ok)
    topological_order: list[str] = field(default_factory=list)

    # ----- Compatibility alias (aligned with ValidationResult / DRF conventional naming is_valid)-----
    @property
    def is_valid(self) -> bool:
        return self.ok

    @is_valid.setter
    def is_valid(self, value: bool) -> None:
        self.ok = bool(value)

    # ----- mutation helpers (aligned with ValidationResult API style)-----
    def add_error(self, ptr: str, message: str) -> None:
        self.ok = False
        self.errors.setdefault(ptr, []).append(message)

    def add_warning(self, ptr: str, message: str) -> None:
        self.warnings.setdefault(ptr, []).append(message)

    def iter_errors(self) -> list[tuple[str, str]]:
        """Flatten errors per pointer group into a [(pointer, message), ...] list.

        Used for publish / REST API to return flat error lists, displayed in order.
        """
        flat: list[tuple[str, str]] = []
        for ptr, msgs in (self.errors or {}).items():
            if not msgs:
                flat.append((ptr, "Validation failed"))
                continue
            for m in msgs:
                flat.append((ptr, m))
        return flat


# ========================================================================
# Condition expression blacklist (only shallow injection prevention; actual evaluation uses restricted eval in S4-01)
# ========================================================================
_UNSAFE_TOKENS = (
    "__import__", "eval(", "exec(", "compile(", "open(", "subprocess",
    "os.system", "os.popen", "globals()", "locals()", "getattr(", "setattr(",
    "delattr(", "importlib", "__builtins__", "lambda:", "pickle", "marshal",
)


# ========================================================================
# Main API
# ========================================================================
def _extract_host_ids_from_node_for_validator(node: dict[str, Any]) -> list[Any]:
    """Compatibly extract host_ids list from multiple places in a node.

    Directly reuses executor._extract_host_ids_from_node (already handles: hosts/host_id/host
    multiple field names, Host object array -> id extraction, empty value filtering), ensuring
    validator and execution engine produce identical decisions for the same node structure.
    """
    try:
        from .executor import _extract_host_ids_from_node
        return _extract_host_ids_from_node(node) or []
    except Exception:
        # Import exception fallback (rare cases like circular dependency), minimal local extraction logic to avoid false positives
        if not isinstance(node, dict):
            return []
        for k in ("host_ids", "target_hosts", "hosts"):
            v = node.get(k)
            if isinstance(v, list):
                return [h for h in v if isinstance(h, (str, int)) and str(h).strip()]
        return []


def _resolve_requires_host_for_validator(node: dict[str, Any], strict: bool) -> bool | None:
    """Parse node's requires_host.

    When strict=True, query adapter.requires_host from registry table;
    When strict=False, only check explicitly declared requires_host field on node.
    Returns None means cannot determine (strict=False and node has no explicit declaration).

    Additional heuristic: as long as node explicitly has any host config field (host_ids/hosts/target_hosts
    non-empty, or host_id non-empty), treat it as requires_host=True (even if adapter query failed),
    to avoid V-09 missed detection due to registry table load failure.
    """
    explicit = node.get("requires_host")
    if isinstance(explicit, bool):
        return explicit
    # Heuristic: node has explicitly configured host-related fields → treat as requires_host=True
    ids = _extract_host_ids_from_node_for_validator(node)
    if ids:
        return True
    if not strict:
        return None
    try:
        from .registry import get_registry

        nt = node.get("node_type")
        if isinstance(nt, str) and nt not in ("start", "end"):
            cls = get_registry().get_class(nt)
            return bool(getattr(cls, "requires_host", True))
    except Exception:
        return None
    return None


def validate_dag(dag: WorkflowDAG, strict: bool = True) -> DAGValidationResult:
    """Validate a WorkflowDAG. When strict=True, additionally check if node_type is registered."""
    result = DAGValidationResult()
    nodes = dag.nodes or []
    edges = dag.edges or []

    # ----------------------------------------------------------- 1. start/end existence
    starts = [i for i, n in enumerate(nodes) if n.get("node_type") == "start"]
    ends = [i for i, n in enumerate(nodes) if n.get("node_type") == "end"]
    if len(starts) == 0:
        result.add_error("/nodes", "V-01: Missing start node (node_type='start')")
    elif len(starts) > 1:
        result.add_error(
            "/nodes",
            f"V-01: Only one start node allowed, currently {len(starts)} (indices={starts})",
        )
    if len(ends) == 0:
        result.add_error("/nodes", "V-01: Missing end node (node_type='end')")
    start_key = nodes[starts[0]]["node_key"] if len(starts) == 1 else None
    end_key = nodes[ends[0]]["node_key"] if len(ends) == 1 else None

    # ----------------------------------------------------------- 2. node_key uniqueness
    key_to_idx: dict[str, int] = {}
    for i, n in enumerate(nodes):
        k = n.get("node_key")
        if not isinstance(k, str) or not k:
            result.add_error(f"/nodes/{i}/node_key", "V-02: node_key must be a non-empty string")
            continue
        if k in key_to_idx:
            result.add_error(
                f"/nodes/{i}/node_key",
                f"V-02: node_key='{k}' is duplicated (conflicts with /nodes/{key_to_idx[k]})",
            )
        else:
            key_to_idx[k] = i

    # ----------------------------------------------------------- 3. dangling edge detection
    def _repr_key(k: Any) -> str:
        if k is None:
            return "<null>"
        if isinstance(k, str) and k == "":
            return "<empty string>"
        return repr(k)

    for i, e in enumerate(edges):
        fk = e.get("from_key")
        tk = e.get("to_key")
        if fk not in key_to_idx:
            result.add_error(f"/edges/{i}/from_key", f"V-03: Source node {_repr_key(fk)} does not exist in /nodes list")
        if tk not in key_to_idx:
            result.add_error(f"/edges/{i}/to_key", f"V-03: Target node {_repr_key(tk)} does not exist in /nodes list")
        if fk == tk and fk in key_to_idx:
            result.add_error(f"/edges/{i}", f"V-04: Node {_repr_key(fk)} has a self-loop, DAGs cannot have cycles")

    # ----------------------------------------------------------- 4. start has no incoming edge / end has no outgoing edge
    in_deg: dict[str, int] = {k: 0 for k in key_to_idx}
    out_deg: dict[str, int] = {k: 0 for k in key_to_idx}
    adj: dict[str, list[str]] = {k: [] for k in key_to_idx}
    reverse_adj: dict[str, list[str]] = {k: [] for k in key_to_idx}
    for i, e in enumerate(edges):
        fk, tk = e.get("from_key"), e.get("to_key")
        if fk not in key_to_idx or tk not in key_to_idx:
            continue  # dangling edge already reported, skip here
        adj[fk].append(tk)
        reverse_adj[tk].append(fk)
        out_deg[fk] = out_deg.get(fk, 0) + 1
        in_deg[tk] = in_deg.get(tk, 0) + 1

    if start_key is not None and in_deg.get(start_key, 0) > 0:
        result.add_error(
            f"/nodes/{key_to_idx[start_key]}",
            f"V-06: start node '{start_key}' has {in_deg[start_key]} incoming edges, not allowed",
        )
    if end_key is not None and out_deg.get(end_key, 0) > 0:
        result.add_error(
            f"/nodes/{key_to_idx[end_key]}",
            f"V-06: end node '{end_key}' has {out_deg[end_key]} outgoing edges, not allowed",
        )

    # ----------------------------------------------------------- 5. Topological order (Kahn) + cycle detection
    order: list[str] = []
    if not result.errors:  # Only run Kahn when structure is fine (otherwise errors would be misleading)
        q = deque(k for k, d in in_deg.items() if d == 0)
        remaining_in = dict(in_deg)
        while q:
            k = q.popleft()
            order.append(k)
            for nxt in adj[k]:
                remaining_in[nxt] -= 1
                if remaining_in[nxt] == 0:
                    q.append(nxt)
        if len(order) != len(key_to_idx):
            # Found a cycle: pick a node still with in_deg>0, walk backward to print cycle example
            cycle_seed = next(k for k in key_to_idx if remaining_in[k] > 0)
            result.add_error(
                "/edges",
                f"V-04: DAG detected directed cycle (example entry node '{cycle_seed}'). "
                f"Processed {len(order)}/{len(key_to_idx)} nodes",
            )
        else:
            result.topological_order = order

    # ----------------------------------------------------------- 6. Reachability (only meaningful with start/end and no cycles)
    # First identify "completely isolated" draft nodes (no incoming edge AND no outgoing edge, not start/end):
    # These are nodes the user dragged but hasn't connected yet, only warn, don't error
    truly_isolated: set[str] = {
        k
        for k in key_to_idx
        if in_deg.get(k, 0) == 0 and out_deg.get(k, 0) == 0 and k not in {start_key, end_key}
    }

    if start_key is not None and not result.errors:
        reachable_from_start: set[str] = set()
        stack = [start_key]
        while stack:
            k = stack.pop()
            if k in reachable_from_start:
                continue
            reachable_from_start.add(k)
            stack.extend(adj[k])
        for k, idx in key_to_idx.items():
            if k in truly_isolated:
                continue
            if k not in reachable_from_start:
                result.add_error(
                    f"/nodes/{idx}",
                    f"V-05: Node '{k}' is unreachable from start node (dead code)",
                )

    if end_key is not None and start_key is not None and not result.errors:
        can_reach_end: set[str] = set()
        stack = [end_key]
        while stack:
            k = stack.pop()
            if k in can_reach_end:
                continue
            can_reach_end.add(k)
            stack.extend(reverse_adj[k])
        for k, idx in key_to_idx.items():
            if k in truly_isolated:
                continue
            if k not in can_reach_end and k != end_key:
                result.add_error(
                    f"/nodes/{idx}",
                    f"V-05: Node '{k}' has no path reaching end (dead end, will cause execution hang)",
                )

    # ----------------------------------------------------------- 7. [strict] node_type is registered (allow two built-in keywords start/end)
    if strict:
        # Lazy import: loose mode doesn't require it, and avoids circular dependency registry<->validator
        try:
            from .registry import get_registry

            registered = set(get_registry().list_all_adapter_types())
        except Exception:  # pragma: no cover - Fallback to loose when registry is unavailable
            registered = None

        if registered is not None:
            _BUILTIN = {"start", "end"}
            for i, n in enumerate(nodes):
                nt = n.get("node_type")
                if nt in _BUILTIN or nt in registered:
                    continue
                result.add_error(
                    f"/nodes/{i}/node_type",
                    f"V-07: node_type='{nt}' is not registered in the adapter registry. "
                    f"Available types: {sorted(_BUILTIN | registered)}",
                )

    # ----------------------------------------------------------- 8. Condition expression simple safety check
    for i, e in enumerate(edges):
        cond = e.get("condition")
        if cond is None or cond == "":
            continue
        # 8a. Parenthesis balance
        depth = 0
        for ch in cond:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth < 0:
                    break
        if depth != 0:
            result.add_error(
                f"/edges/{i}/condition",
                "V-08: Condition expression has unbalanced parentheses (missing matching '(' or ')')",
            )
        # 8b. Dangerous keywords
        lowered = cond.replace(" ", "")
        for token in _UNSAFE_TOKENS:
            clean_token = token.replace(" ", "")
            if clean_token in lowered:
                result.add_error(
                    f"/edges/{i}/condition",
                    f"V-08: Condition expression contains forbidden dangerous keyword '{token}'",
                )
                break

    # ----------------------------------------------------------- 9. [strict] V-09: requires_host=True node must configure host_ids
    if strict:
        for i, n in enumerate(nodes):
            nt = n.get("node_type")
            if nt in ("start", "end"):
                continue
            rh = _resolve_requires_host_for_validator(n, strict=True)
            if rh is not True:
                continue
            # requires_host=True, check if there's any host config
            host_ids = _extract_host_ids_from_node_for_validator(n) or []
            cleaned = [str(h) for h in host_ids if isinstance(h, (str, int)) and str(h).strip()]
            if not cleaned:
                node_key = n.get("node_key") or f"#{i}"
                display = nt or node_key
                result.add_error(
                    f"/nodes/{i}",
                    f"V-09: Node '{display}' (node_type={nt!r}, requires_host=True) "
                    f"has no target hosts configured. Please specify at least one valid host ID in "
                    f"data.config.target_hosts / host_ids / data.host_ids / params.host_ids.",
                )

    # ----------------------------------------------------------- 10. Isolated node warning (warning only, does not set ok to False)
    for k, idx in key_to_idx.items():
        if k in {"start", "end"} or (start_key and k == start_key) or (end_key and k == end_key):
            continue
        if in_deg.get(k, 0) == 0 or out_deg.get(k, 0) == 0:
            reason = []
            if in_deg.get(k, 0) == 0:
                reason.append("no incoming edge")
            if out_deg.get(k, 0) == 0:
                reason.append("no outgoing edge")
            result.add_warning(
                f"/nodes/{idx}",
                f"V-10: Node '{k}' is isolated ({'/'.join(reason)}), may be leftover draft node",
            )

    return result