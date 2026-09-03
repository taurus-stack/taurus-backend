"""WorkflowRunner Orchestration layer (bridge between Django ORM and executor pure functions).

Responsibilities:
- trigger_workflow: create WorkflowExecution, initialize NodeExecution rows and call Adapter dispatch
- advance_workflow: poll RUNNING rows -> UpdateStatus -> push next batch of runnables -> dispatch -> finalization
- cancel_workflow: call adapter.cancel and mark status as CANCELLED

All DB write operations are wrapped in @transaction.atomic; external via celery beat / management commands
periodically execute advance_workflow.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any

from django.db import transaction
from django.utils import timezone

from taurus.workflow.engine.context import WorkflowContext
from taurus.workflow.engine.executor import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    TERMINAL_STATUSES,
    RenderError,
    _ElseToken,
    compute_next_runnables,
    evaluate_edge_condition,
    expand_hosts,
    extract_node_timeout_sec,
    normalize_edges,
    render_node_config,
    transition_status,
)
from taurus.workflow.engine.registry import get_registry
from taurus.workflow.engine.schemas import (
    NO_HOST_SENTINEL,
    RenderedNodeConfig,
    UnitOutput,
)
from taurus.workflow.exceptions import WorkflowRunnerError  # noqa: F401  Re-export for caller convenience
from taurus.workflow.engine import metrics as wf_metrics

logger = logging.getLogger(__name__)

__all__ = [
    "AdvanceTick",
    "TriggerResult",
    "WorkflowRunner",
    "WorkflowRunnerError",
    "resolve_fail_strategy",
]


def resolve_fail_strategy(workflow: Any, explicit: str | None = None) -> str:
    """Map Workflow.fail_strategy (stop/continue) to Engine value (fail_fast/continue).

    Workflow Model uses stop/continue semantics, Engine uses fail_fast/continue, needs unified mapping.
    Priority: use explicit parameter (caller-specified), otherwise read workflow.fail_strategy,
    fallback to 'fail_fast'.
    """
    if explicit is not None:
        if explicit in ("fail_fast", "continue"):
            return explicit
        if explicit == "stop":
            return "fail_fast"
    wf_value = getattr(workflow, "fail_strategy", None) or ""
    if wf_value == "continue":
        return "continue"
    return "fail_fast"

# Node types eligible for timeout (global timeout only applies to these types)
# Control/logic node types (start/end/noop/wait/condition/loop/transform/virtual) are not affected by global timeout
_TIMEOUT_ELIGIBLE_NODE_TYPES: frozenset[str] = frozenset({
    "command", "script", "file_op", "program",
    "http", "http_callback", "sub_workflow", "approval",
    "email_notification", "webhook_notification",
})

# --------------------------------------------------------------------- return structure
@dataclasses.dataclass
class TriggerResult:
    execution_id: int
    dag_version_id: int
    initial_runnables: list[str]


@dataclasses.dataclass
class AdvanceTick:
    execution_id: int
    polled: int              # Number of RUNNING rows polled this tick
    newly_completed: int     # Number of rows that reached SUCCESS/FAILED/SKIPPED/CANCELLED this tick
    newly_dispatched: int    # Number of rows newly dispatched this tick (RUNNING or directly terminal state)
    newly_skipped: int       # Number of rows marked SKIPPED this tick due to edge condition not matching
    finished: bool           # Workflow reached terminal state (all nodes terminal and execution marked finished)


def _build_rendered_cfg(
    row: Any,
    *,
    params: dict[str, Any],
) -> RenderedNodeConfig:
    """Build standard RenderedNodeConfig from WorkflowNodeExecution row + rendered params."""
    started_ts = (
        row.started_at.isoformat()
        if row.started_at is not None
        else timezone.now().isoformat()
    )
    return RenderedNodeConfig(
        execution_id=str(row.execution_id),
        node_key=str(row.node_key),
        node_name=str(getattr(row, "node_name", "") or row.node_key),
        host_id=str(row.host_id or NO_HOST_SENTINEL),
        dispatch_id=str(row.dispatch_id),
        attempt_no=int(getattr(row, "attempt_no", 1) or 1),
        user_id=int(getattr(row, "user_id", 0) or 0),
        global_timeout_sec=int(getattr(row, "timeout_sec", 0) or 0),
        secrets_mask=list(getattr(row, "secrets_mask", None) or []),
        params=dict(params or {}),
        triggered_at=started_ts,
    )


def _apply_unit_output(row: Any, uo: UnitOutput, *, initial: bool) -> list[str]:
    """Flush UnitOutput into WorkflowNodeExecution row. Returns save update_fields list.

    initial=True means the first UnitOutput returned from dispatch (started_at may have just been set).
    """
    fields: list[str] = []

    # Update status if valid
    try:
        new_status = transition_status(row.status, int(uo.status))
        if new_status != row.status:
            row.status = new_status
            fields.append("status")
    except ValueError:
        pass  # Invalid transition, keep original status

    if uo.adapter_state is not None:
        row.adapter_state = dict(uo.adapter_state)
        fields.append("adapter_state")
    if uo.output:
        row.output = dict(uo.output)
        fields.append("output")
    if uo.output_refs:
        row.output_refs = list(uo.output_refs)
        fields.append("output_refs")
    if uo.exit_code is not None:
        row.exit_code = int(uo.exit_code)
        fields.append("exit_code")
    # Only overwrite row's error_description when adapter explicitly returns error_message;
    # summary like normal output digest (e.g. "child workflow triggered") should not write error_message
    if uo.error_message is not None:
        row.error_message = uo.error_message
        fields.append("error_message")
    if row.status in TERMINAL_STATUSES and row.finished_at is None:
        row.finished_at = timezone.now()
        fields.append("finished_at")
        if row.started_at and row.finished_at:
            row.duration_ms = max(0, int((row.finished_at - row.started_at).total_seconds() * 1000))
            fields.append("duration_ms")
    if not initial:
        row.poll_count = int(getattr(row, "poll_count", 0) or 0) + 1
        fields.append("poll_count")
    fields.append("update_datetime")
    return fields


# -------------------------------------------------------------------- Utility functions
def _find_node(nodes: list[dict[str, Any]], node_key: str) -> dict[str, Any] | None:
    for n in nodes:
        if str(n.get("node_key", "")) == node_key:
            return n
    return None


def _make_dispatch_id(execution_id: int, node_key: str, host_id: str, attempt_no: int = 1) -> str:
    """Idempotency key: ensures the same node + same execution on the same host for the same attempt is dispatched only once.

    attempt_no participates in hash, so failed retries (incremented attempt_no) can generate new dispatch_ids,
    satisfying Adapter dispatch_id idempotency semantics.
    """
    from hashlib import sha1

    raw = f"wfex-{execution_id}:{node_key}:{host_id}:{attempt_no}"
    return f"wfex-{execution_id}-{node_key[:32]}-{sha1(raw.encode()).hexdigest()[:10]}"


def _collect_workflow_host_ids(_workflow: Any) -> list[str]:
    """Expand host_id list from workflow-bound assets/tags (placeholder implementation).

    Real implementation could expand via workflow.hosts, workflow.host_groups etc.;
    currently returns empty, actual host_ids mainly come from node's own host_ids / host_group_refs.
    """
    return []


def _build_edge_ctx(execution: Any, dag_ver: Any) -> dict[str, Any]:
    """Build edge condition/interpolation ctx (consumed by evaluate_edge_condition / compute_next_runnables)."""
    wf = execution.workflow
    ctx: dict[str, Any] = {
        "workflow": {
            "id": wf.pk,
            "name": getattr(wf, "name", None),
            "env": dict(getattr(dag_ver, "global_envs", None) or getattr(dag_ver, "env_vars", None) or {}),
            "secrets": dict(getattr(dag_ver, "global_secrets", None) or {}),
        },
        "trigger": dict(getattr(execution, "trigger_params", None) or {}),
        "execution": {
            "id": execution.pk,
            "trigger_type": getattr(execution, "trigger_type", "manual"),
        },
        # node_statuses / node_outputs dynamically maintained by runner
        "node_statuses": {},
        "node_outputs": {},
    }
    return ctx


def _build_workflow_context(ctx: dict[str, Any]) -> WorkflowContext:
    """Build WorkflowContext object from `_build_edge_ctx` generated dict (consumed by render_node_config).

    WorkflowContext path rules differ slightly from ctx dict:
      - WorkflowContext.node_outputs keys require `node_<key>` instead of bare `<key>`
      - Values use `{"output": {...}, "status": <int>}` structure (so `${node_A.output.rc}` / `${node_A.status}` can parse)
    """
    wf_block = ctx.get("workflow") or {}
    node_outputs_src: dict[str, Any] = ctx.get("node_outputs") or {}
    node_statuses_src: dict[str, Any] = ctx.get("node_statuses") or {}
    wf_node_outputs: dict[str, dict[str, Any]] = {}
    for k, out in node_outputs_src.items():
        clean_out = {kk: vv for kk, vv in out.items() if kk != "__by_host__"}
        entry: dict[str, Any] = {"output": clean_out}
        if k in node_statuses_src:
            entry["status"] = node_statuses_src[k]
        wf_node_outputs[f"node_{k}"] = entry
    # Some node_statuses have status but node_outputs has no output, also populate status (ensure status interpolation works)
    for k, st in node_statuses_src.items():
        key = f"node_{k}"
        if key not in wf_node_outputs:
            wf_node_outputs[key] = {"output": {}, "status": st}
        elif "status" not in wf_node_outputs[key]:
            wf_node_outputs[key]["status"] = st
    return WorkflowContext(
        workflow_env=dict(wf_block.get("env") or {}),
        secrets=dict(wf_block.get("secrets") or {}),
        trigger_params=dict(ctx.get("trigger") or {}),
        node_outputs=wf_node_outputs,
    )


def _host_render_ctx(base_ctx: dict[str, Any], *, host_id: str) -> dict[str, Any]:
    """Inject host-level variables for single-host rendering."""
    ctx = dict(base_ctx)
    ctx["host"] = {"id": host_id}
    return ctx


_CANONICAL_TYPE_NAMES: dict[str, set[str]] = {
    "start": {"Start node", "start", "Start", "Start"},
    "end": {"End node", "end", "End", "End"},
    "wait": {"Delayed wait", "Wait", "wait", "Wait", "Delayed"},
    "noop": {"No-op", "noop", "Noop", "No-op node"},
    "condition": {"Condition branch", "Condition", "condition", "Condition", "Branch"},
    "loop": {"Loop", "loop", "Loop", "Loop node"},
    "approval": {"Approval", "Approval node", "approval", "Approval"},
    "sub_workflow": {"Sub-workflow", "Sub-process", "sub_workflow", "SubWorkflow"},
}

_CANONICAL_TYPE_DISPLAY: dict[str, str] = {
    "start": "Start node",
    "end": "End node",
    "wait": "Delayed wait",
    "noop": "No-op",
    "condition": "Condition branch",
    "loop": "Loop",
    "approval": "Approval",
    "sub_workflow": "Sub-workflow",
}


def _sanitize_canonical_node_name(node_type: str, current_name: str) -> str:
    """For canonical types like start/end/wait: if name is polluted to another canonical type's display name, auto-correct.

    This resolves historical dirty data issues, e.g. end was named "Delayed wait" (wait's name),
    users seeing two "Delayed wait" rows would mistakenly think there are two wait nodes.
    """
    if not node_type:
        return current_name
    nt = str(node_type)
    if nt not in _CANONICAL_TYPE_NAMES:
        return current_name
    name = str(current_name or "").strip()
    if not name:
        return _CANONICAL_TYPE_DISPLAY.get(nt, name)
    # Name is correct -> return directly
    if name in _CANONICAL_TYPE_NAMES[nt]:
        return name
    # Does the name happen to match another canonical type's display name? -> Typical pollution scenario
    for other_nt, other_names in _CANONICAL_TYPE_NAMES.items():
        if other_nt == nt:
            continue
        if name in other_names:
            return _CANONICAL_TYPE_DISPLAY[nt]
    return name


def _ensure_node_engine_fields(node: dict[str, Any]) -> dict[str, Any]:
    """Normalize frontend vue-flow raw node to engine-standard top-level fields (modify in-place and return node).

    This is the core normalization entry point - all raw nodes extracted from `dag_ver.definition["nodes"]`
    in the runner must call this function before being passed to expand_hosts / render_node_config,
    to ensure:
      - node_key / node_type / node_name have values
      - requires_host is injected according to registry (key decision basis for expand_hosts)
      - host_ids / target_hosts -> top-level host_ids
      - fail_strategy / max_retries / retry_delay_ms / timeout etc. are lifted from data.config

    Designed to be idempotent: multiple calls produce no side effects.
    """
    if not isinstance(node, dict):
        return node

    # 1. node_key: compatible with vue-flow's id
    if "node_key" not in node or not node.get("node_key"):
        node["node_key"] = str(node.get("node_key") or node.get("id") or f"node_{id(node)}")
    node_key = str(node["node_key"])

    # 2. node_type: compatible with vue-flow's type, data.nodeType
    if "node_type" not in node or not node.get("node_type"):
        data = node.get("data")
        cfg = (data or {}).get("config") if isinstance(data, dict) else None
        node["node_type"] = str(
            node.get("node_type")
            or node.get("type")
            or (cfg.get("nodeType") if isinstance(cfg, dict) else None)
            or (data.get("nodeType") if isinstance(data, dict) else None)
            or "noop"
        )
    node_type = str(node["node_type"])

    # 3. requires_host: injected from adapter class attribute (only when not explicitly specified)
    if "requires_host" not in node:
        try:
            adapter_cls = get_registry().get_class(node_type)
            node["requires_host"] = bool(getattr(adapter_cls, "requires_host", True))
        except Exception:
            node["requires_host"] = True

    # 4. data.config -> top-level common fields (only fill when missing at top level, keep idempotent)
    data = node.get("data") if isinstance(node.get("data"), dict) else None
    cfg = data.get("config") if data and isinstance(data.get("config"), dict) else None
    params_from_cfg = node.get("params") if isinstance(node.get("params"), dict) else None

    # 4.1 node_name: compatible with node.name / node.title / node.label / data.label / data.config.node_name
    if "node_name" not in node or not node.get("node_name"):
        raw_name = None
        if cfg and isinstance(cfg.get("node_name"), str) and cfg.get("node_name"):
            raw_name = cfg["node_name"]
        if not raw_name and data and isinstance(data.get("label"), str) and data.get("label"):
            raw_name = data["label"]
        for k in ("node_name", "title", "name", "label"):
            if isinstance(node.get(k), str) and node.get(k):
                raw_name = node.get(k)
                break
        node["node_name"] = str(raw_name or node_key)

    # 4.2 host_ids: compatible with data.config.target_hosts / hosts / host_id etc. various frontend field names
    #    Also process frontend-passed Host object arrays ({id,name,...}) -> extract ids
    existing = node.get("host_ids")
    existing_coerced: list[Any] | None = None
    if isinstance(existing, list) and existing:
        from .executor import _coerce_host_list
        existing_coerced = _coerce_host_list(existing)
        if existing_coerced:
            node["host_ids"] = existing_coerced

    if not existing_coerced:
        from .executor import _coerce_host_list, _SINGULAR_HOST_KEYS

        merged: list[Any] = []

        # Top-level: target_hosts / hosts / host_ids (array)
        for k in ("target_hosts", "hosts", "host_ids"):
            v = node.get(k)
            if isinstance(v, list) and v:
                merged.extend(v)
                break

        # Top-level singular host_id / host
        for k in ("host_id", "host"):
            v = node.get(k)
            if v is not None and v != "":
                merged.append(v)

        # Under data.config (array + singular)
        if cfg:
            for k in ("target_hosts", "hosts", "host_ids"):
                if isinstance(cfg.get(k), list) and cfg[k]:
                    merged.extend(cfg[k])
                    break
            for k in ("host_id", "host"):
                v = cfg.get(k)
                if v is not None and v != "":
                    merged.append(v)
        # Directly under data
        if data:
            for k in ("target_hosts", "hosts", "host_ids"):
                if isinstance(data.get(k), list) and data[k]:
                    merged.extend(data[k])
                    break
            for k in ("host_id", "host"):
                v = data.get(k)
                if v is not None and v != "":
                    merged.append(v)
        # Under params
        if params_from_cfg:
            for k in ("target_hosts", "hosts", "host_ids"):
                if isinstance(params_from_cfg.get(k), list) and params_from_cfg[k]:
                    merged.extend(params_from_cfg[k])
                    break
            for k in ("host_id", "host"):
                v = params_from_cfg.get(k)
                if v is not None and v != "":
                    merged.append(v)

        if merged:
            node["host_ids"] = _coerce_host_list(merged)

    explicit_hosts = node.get("host_ids")
    if isinstance(explicit_hosts, list) and explicit_hosts:
        node["requires_host"] = True

    # 4.3 Control fields: fail_strategy / max_retries / retry_delay_ms
    def _promote_cfg_to_top(*keys: str, coerce=None) -> None:
        for k in keys:
            if node.get(k) is None or node.get(k) == "" or (coerce is int and isinstance(node.get(k), str) and node.get(k).isdigit() is False and node.get(k) != ""):
                found = None
                if cfg and k in cfg:
                    found = cfg.get(k)
                if (found is None or found == "") and data and k in data:
                    found = data.get(k)
                if (found is None or found == "") and params_from_cfg and k in params_from_cfg:
                    found = params_from_cfg.get(k)
                if found is None or found == "":
                    continue
                if coerce is not None:
                    try:
                        node[k] = coerce(found)
                        return
                    except (TypeError, ValueError):
                        continue
                node[k] = found
                return

    _promote_cfg_to_top("fail_strategy")
    _promote_cfg_to_top("max_retries", coerce=int)
    _promote_cfg_to_top("retry_delay_ms", coerce=int)

    # 5. params: if no params at top level but data.config / data.params exist, keep in place
    #    (extract_node_params_from_vueflow inside render_node_config will process them)
    return node


def _prep_node_rows(
    execution: Any,
    dag_ver: Any,
    node: dict[str, Any],
    user_id: int | None,
    attempt_no: int = 1,
) -> list[Any]:
    """Generate WorkflowNodeExecution rows for a node x each host (not saved yet).

    During row generation:
      - First normalize frontend vue-flow node (with id/type/data.config.target_hosts etc.) to
        engine-recognized top-level fields (requires_host / node_type / node_key / node_name)
      - expand_hosts to get host_ids:
          * requires_host=False or implicitly no host -> ["__NO_HOST__"]
          * requires_host=True but no valid host -> [], this function generates a FAILED placeholder row
          * Otherwise -> one PENDING row per host
      - dispatch_id generated per (execution_id, node_key, host_id)
    """
    from taurus.workflow.models import WorkflowNodeExecution

    # Normalize vue-flow raw node to engine-standard fields (modify node dict in-place)
    _ensure_node_engine_fields(node)

    node_key = str(node["node_key"])
    node_type = str(node.get("node_type", "noop") or "noop")
    raw_name = str(node.get("node_name") or node_key)
    node_name = _sanitize_canonical_node_name(node_type, raw_name)
    timeout_sec = extract_node_timeout_sec(node)

    # Global timeout injection: when node is not individually configured with timeout (timeout_sec=0) and node type supports timeout,
    # use workflow-level global timeout as default
    if timeout_sec == 0 and node_type in _TIMEOUT_ELIGIBLE_NODE_TYPES:
        ctx_data = execution.context or {}
        global_timeout = int(ctx_data.get("global_timeout_sec", 0) or 0)
        if global_timeout > 0:
            timeout_sec = global_timeout
    now = timezone.now()

    host_ids = expand_hosts(node)
    requires_host = bool(node.get("requires_host", False))

    rows: list[WorkflowNodeExecution] = []

    # Backward compat: old DAG versions had fail_strategy='fail_fast' on every node
    # as a default. Treat it as "not set" when execution-level is different,
    # so that the execution-level strategy can take effect.
    node_fs = node.get("fail_strategy")
    exec_fs = str(execution.fail_strategy or "fail_fast")
    if node_fs == "fail_fast" and exec_fs != "fail_fast":
        node_fs = None
    resolved_fail_strategy = str(node_fs or exec_fs or "fail_fast")

    if not host_ids and requires_host:
        # requires_host=True but no valid hosts after expansion -> generate a FAILED placeholder row
        # Use __NO_HOST__ placeholder but mark error, convenient for frontend display and user diagnosis
        hid = "__NO_HOST__"
        err_row = WorkflowNodeExecution(
            execution=execution,
            dag_version=dag_ver,
            node_key=node_key,
            node_type=node_type,
            node_name=node_name,
            host_id=hid,
            dispatch_id=_make_dispatch_id(execution.pk, node_key, hid, attempt_no),
            status=STATUS_FAILED,
            attempt_no=attempt_no,
            fail_strategy=resolved_fail_strategy,
            timeout_sec=timeout_sec,
            queued_at=now,
            started_at=now,
            finished_at=now,
            error_message=(
                "E0301: Node requires_host=True but no valid hosts configured. "
                "Please select target hosts (host_ids / target_hosts) in node properties, "
                "or bind assets at workflow level."
            ),
            user_id=user_id,
            creator_id=user_id,
            modifier=str(user_id) if user_id is not None else None,
            dept_belong_id=None,
            create_datetime=now,
            update_datetime=now,
        )
        rows.append(err_row)
        return rows

    # Normal path: __NO_HOST__ when there are host_ids or requires_host=False
    if not host_ids:
        host_ids = ["__NO_HOST__"]

    for hid in host_ids:
        rows.append(
            WorkflowNodeExecution(
                execution=execution,
                dag_version=dag_ver,
                node_key=node_key,
                node_type=node_type,
                node_name=node_name,
                host_id=hid,
                dispatch_id=_make_dispatch_id(execution.pk, node_key, hid, attempt_no),
                status=STATUS_PENDING,
                attempt_no=attempt_no,
                fail_strategy=resolved_fail_strategy,
                timeout_sec=timeout_sec,
                queued_at=now,
                user_id=user_id,
                creator_id=user_id,
                modifier=str(user_id) if user_id is not None else None,
                dept_belong_id=None,
                create_datetime=now,
                update_datetime=now,
            ),
        )
    return rows



def _refresh_ctx_from_db(
    execution: Any,
    ctx: dict[str, Any],
) -> None:
    """Sync latest NodeExecution row status/output from DB into ctx."""
    from taurus.workflow.models import WorkflowNodeExecution

    rows = WorkflowNodeExecution.objects.filter(execution_id=execution.pk).only(
        "node_key", "status", "output", "host_id",
    )
    node_statuses: dict[str, int] = {}
    node_outputs: dict[str, dict[str, Any]] = {}
    for r in rows:
        prev = node_statuses.get(r.node_key)
        if prev is None:
            node_statuses[r.node_key] = r.status
        else:
            # Node-level view: any host FAILED -> node treated as FAILED, any CANCELLED -> treated as CANCELLED,
            # otherwise check if all SUCCESS
            order = [STATUS_SUCCESS, STATUS_SKIPPED, STATUS_PENDING, STATUS_RUNNING, STATUS_CANCELLED, STATUS_FAILED]
            try:
                node_statuses[r.node_key] = max(prev, r.status, key=lambda s: order.index(s))
            except ValueError:
                node_statuses[r.node_key] = max(prev, r.status)
        # Node-level output: aggregate each host's output (defaults to first non-empty)
        out = r.output or {}
        if out:
            agg = node_outputs.setdefault(r.node_key, {})
            if "__by_host__" not in agg:
                agg["__by_host__"] = {}
            agg["__by_host__"][r.host_id] = out
            # Single host case: expand directly to top level, convenient for interpolation
            if "rc" not in agg:
                agg.update({k: v for k, v in out.items() if k != "__by_host__"})
    ctx["node_statuses"] = node_statuses
    ctx["node_outputs"] = node_outputs


def _mark_ineligible_nodes_skipped(
    execution: Any,
    dag_ver: Any,
    ctx: dict[str, Any],
    *,
    user_id: int | None,
) -> int:
    """For nodes where all upstream are terminal but no incoming edge matched, create SKIPPED rows.

    This ensures every node in the DAG eventually has a row (even if not run), convenient for frontend/audit rendering.
    """
    from taurus.workflow.models import WorkflowNodeExecution

    existing_rows = list(
        WorkflowNodeExecution.objects.filter(execution_id=execution.pk)
        .only("node_key", "fail_strategy")
    )
    already_keys: set[str] = set()
    node_fail_strategies: dict[str, str] = {}
    exec_fs = str(execution.fail_strategy or "fail_fast")
    for r in existing_rows:
        already_keys.add(r.node_key)
        node_fs = r.fail_strategy or "fail_fast"
        # Backward compat: old DAG data had fail_strategy='fail_fast' as default,
        # when execution-level strategy is not 'fail_fast', ignore node-level value.
        if node_fs == "fail_fast" and exec_fs != "fail_fast":
            node_fs = exec_fs
        node_fail_strategies[r.node_key] = node_fs

    nodes: list[dict[str, Any]] = list(dag_ver.definition.get("nodes") or [])
    edges: list[dict[str, Any]] = normalize_edges(list(dag_ver.definition.get("edges") or []))
    key_to_node = {str(n["node_key"]): n for n in nodes}
    forward: dict[str, list[str]] = {k: [] for k in key_to_node}
    reverse: dict[str, list[dict[str, Any]]] = {k: [] for k in key_to_node}
    indeg: dict[str, int] = {k: 0 for k in key_to_node}
    for e in edges:
        to = str(e.get("to", ""))
        src = str(e.get("from", ""))
        reverse.setdefault(to, []).append(e)
        if src in forward:
            forward[src].append(to)
        if to in indeg:
            indeg[to] += 1
    # Topological order (BFS): ensure dependency order is correct, B is always evaluated before C
    order: list[str] = []
    q: list[str] = [k for k, d in indeg.items() if d == 0]
    _visited = set(q)
    while q:
        k = q.pop(0)
        order.append(k)
        for nb in forward.get(k, []):
            indeg[nb] -= 1
            if indeg[nb] == 0 and nb not in _visited:
                _visited.add(nb)
                q.append(nb)
    # Put those not in order (with cycles) at the end, ensure all are covered
    for k in key_to_node:
        if k not in _visited:
            order.append(k)
    # Use temporary status dict, created SKIPPED rows will sync in, for downstream node judgment
    local_statuses: dict[str, int] = dict(ctx.get("node_statuses") or {})

    # === Same as compute_next_runnables: normalize global edges first, ensure __else__ semantics per source are accurate ===
    edge_raw: list[Any] = [False] * len(edges)
    for i, e in enumerate(edges):
        src = str(e.get("from", ""))
        src_st = local_statuses.get(src)
        if src_st is None or src_st not in TERMINAL_STATUSES:
            continue
        cond = str(e.get("condition") or "")
        if src and "__from__" in cond:
            cond = cond.replace("__from__", "'" + src.replace("'", "\\'") + "'")
        # Empty condition: source node SUCCESS -> directly True; FAILED + fail_strategy=continue also allows downstream to resume
        if not cond.strip():
            if src_st == STATUS_SUCCESS:
                edge_raw[i] = True
            elif src_st == STATUS_FAILED and node_fail_strategies.get(src) == "continue":
                edge_raw[i] = True
        else:
            try:
                edge_raw[i] = evaluate_edge_condition(cond, ctx)
            except ValueError:
                edge_raw[i] = False

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
        src_st = local_statuses.get(src)
        src_cancel = src_st is not None and src_st == STATUS_CANCELLED
        for i in idxs:
            r = edge_raw[i]
            if isinstance(r, _ElseToken):
                edge_bool[i] = bool((not has_non_else_true) and (not src_cancel))
            else:
                edge_bool[i] = bool(r)
    edge_idx_map: dict[int, int] = {id(e): i for i, e in enumerate(edges)}
    # === Global normalization end ===

    inserts: list[WorkflowNodeExecution] = []
    for k in order:
        node = key_to_node[k]
        if k in already_keys:
            continue
        if node.get("node_type") in ("start", "end"):
            continue
        incoming = reverse.get(k) or []
        if not incoming:
            continue
        all_term = True
        any_hit = False
        for e in incoming:
            src = str(e.get("from", ""))
            st = local_statuses.get(src)
            if st is None or st not in TERMINAL_STATUSES:
                all_term = False
                break
            if edge_bool[edge_idx_map[id(e)]]:
                any_hit = True
        if not all_term:
            continue
        if any_hit:
            # Edge matched but no row exists, possibly an expand error, skip and leave for later diagnosis
            continue
        # All upstream terminal + no edge matched -> mark SKIPPED
        inserts.extend(_prep_node_rows(execution, dag_ver, node, user_id))
        now = timezone.now()
        count = max(1, len(expand_hosts(node)) or 1)
        for r in inserts[-count:]:
            r.status = STATUS_SKIPPED
            r.finished_at = now
            # Model has no skipped_reason field, store in adapter_state for diagnosis
            r.adapter_state = {"skipped_reason": "upstream_conditions_not_met"}
        local_statuses[k] = STATUS_SKIPPED
    if inserts:
        WorkflowNodeExecution.objects.bulk_create(inserts, batch_size=500)
    return len(inserts)


def _finalize_workflow_if_done(execution: Any) -> bool:
    """If all non start/end nodes have reached terminal state, mark execution as finished based on aggregate results.

    Returns True indicating the workflow has completed (whether Success, Failed, or Cancelled).

    Note: must check whether DAG definition still has nodes that haven't created rows yet —
    when upstream nodes complete synchronously but downstream haven't been pushed by compute_next_runnables,
    judging workflow completion based only on existing rows would be incorrect.
    """
    from taurus.workflow.models import WorkflowDAGVersion, WorkflowNodeExecution

    rows = list(
        WorkflowNodeExecution.objects.filter(execution_id=execution.pk)
        .exclude(node_type__in=("start", "end"))
        .only("status", "node_key")
    )

    # Check DAG definition for non start/end nodes that haven't created rows yet
    dag_ver: WorkflowDAGVersion | None = execution.dag_version
    # Intermediate business node key set in DAG (non start/end)
    dag_non_control_keys: set[str] = set()
    if dag_ver is not None:
        dag_def = dag_ver.definition or {}
        existing_keys = {r.node_key for r in rows}
        for node in dag_def.get("nodes") or []:
            nk = str(node.get("node_key", ""))
            nt = str(node.get("node_type", ""))
            if nt in ("start", "end"):
                continue
            dag_non_control_keys.add(nk)
            if nk and nk not in existing_keys:
                return False  # Node in DAG hasn't created rows yet

    # When there are no intermediate business nodes (pure start→end empty DAG),
    # still need to determine execution completion
    # (otherwise status will remain stuck at RUNNING)
    if not rows and not dag_non_control_keys:
        # Empty DAG treated as successful completion
        end_time = timezone.now()
        execution.status = 2
        execution.error_message = None
        execution.end_time = end_time
        execution.save(update_fields=["status", "end_time", "error_message", "update_datetime"])
        return True

    if not rows:
        return False  # No node execution yet, cannot determine completion
    non_term = [r for r in rows if r.status not in TERMINAL_STATUSES]
    if non_term:
        return False

    has_failed = any(r.status == STATUS_FAILED for r in rows)
    has_cancelled = any(r.status == STATUS_CANCELLED for r in rows)
    end_time = timezone.now()
    if has_cancelled:
        execution.status = 4  # Legacy enum: cancelled
        execution.error_message = "cancelled"
    elif has_failed:
        execution.status = 3  # Legacy enum: failed
        execution.error_message = "has failed nodes"
    else:
        execution.status = 2  # Legacy enum: success
        execution.error_message = None
    execution.end_time = end_time
    execution.save(update_fields=["status", "end_time", "error_message", "update_datetime"])
    return True


# ----------------------------------------------------------------- WorkflowRunner
class WorkflowRunner:
    """Orchestration layer that glues executor pure functions and Django ORM together."""

    # ---------------------------------------------------------------- trigger
    @transaction.atomic
    def trigger_workflow(
        self,
        workflow: Any,
        *,
        dag_version: Any | None = None,
        trigger_params: dict[str, Any] | None = None,
        trigger_type: str = "manual",
        user_id: int | None = None,
        fail_strategy: str | None = None,
    ) -> TriggerResult:
        """Release a new WorkflowExecution and dispatch initial runnable nodes."""
        from taurus.models import WorkflowExecution
        from taurus.workflow.models import WorkflowDAGVersion, WorkflowNodeExecution

        resolved_fail_strategy = resolve_fail_strategy(workflow, fail_strategy)

        dag_ver = dag_version or getattr(workflow, "dag_published_version", None)
        if dag_ver is None:
            raise WorkflowRunnerError("E3001: workflow has no published DAG version")
        if not isinstance(dag_ver, WorkflowDAGVersion):
            raise WorkflowRunnerError("E3002: dag_version is not a WorkflowDAGVersion instance")

        dag_def = dag_ver.definition or {}
        nodes: list[dict[str, Any]] = list(dag_def.get("nodes") or [])
        edges: list[dict[str, Any]] = normalize_edges(list(dag_def.get("edges") or []))
        if not nodes:
            raise WorkflowRunnerError("E3003: dag_version is missing nodes definition")
        # Edges can be empty: single-node workflow (e.g., only triggers a child workflow) has no edges

        global_timeout_sec = int(getattr(workflow, "global_timeout_sec", 0) or 0)
        execution = WorkflowExecution.objects.create(
            workflow=workflow,
            status=1,  # Legacy WorkflowExecution: 0 pending / 1 running
            start_time=timezone.now(),
            end_time=None,
            context={"mode": "dag", "dag_version": dag_ver.version, "global_timeout_sec": global_timeout_sec},
            error_message=None,
            current_step=0,
            dag_version=dag_ver,
            trigger_params=dict(trigger_params or {}),
            current_node_key=None,
            fail_strategy=resolved_fail_strategy,
            trigger_type=trigger_type,
            creator_id=user_id,
            modifier=str(user_id) if user_id is not None else None,
        )
        ctx = _build_edge_ctx(execution, dag_ver)
        runnables = compute_next_runnables(nodes, edges, {}, ctx, execution_fail_strategy=execution.fail_strategy)
        inserts: list[WorkflowNodeExecution] = []
        # Start node requires immediately creating a SUCCESS-status record (control nodes treated as immediate success)
        # This ensures frontend and backend node statuses correspond one-to-one, avoiding frontend defaulting to pending
        now = timezone.now()
        for node in nodes:
            if str(node.get("node_type", "")) == "start":
                start_rows = _prep_node_rows(execution, dag_ver, node, user_id)
                for sr in start_rows:
                    sr.status = STATUS_SUCCESS
                    sr.queued_at = now
                    sr.started_at = now
                    sr.finished_at = now
                inserts.extend(start_rows)
        for nk in runnables:
            node = _find_node(nodes, nk)
            if node is None:
                continue
            rows = _prep_node_rows(execution, dag_ver, node, user_id)
            if str(node.get("node_type", "")) == "end":
                for row in rows:
                    row.status = STATUS_SUCCESS
                    row.queued_at = now
                    row.started_at = now
                    row.finished_at = now
                    row.duration_ms = 0
            inserts.extend(rows)
        if inserts:
            WorkflowNodeExecution.objects.bulk_create(inserts, batch_size=500)

        # Immediately dispatch first wave PENDING → RUNNING (no long blocking in transaction here,
        # real deployment can switch to sending messages to queue, dispatched asynchronously by worker)
        self._dispatch_pending(execution, dag_ver, inserts, ctx, user_id=user_id)

        # Synchronous follow-up: after dispatch, if there are synchronously completed nodes (e.g., testing_noop),
        # downstream end nodes will become runnable. Need to compute runnables once more,
        # create rows for end nodes and mark them SUCCESS directly, otherwise they'll stay in "not created" status forever.
        all_rows = list(
            WorkflowNodeExecution.objects.filter(execution_id=execution.pk)
            .only("node_key", "status", "fail_strategy")
        )
        node_rows_map2: dict[str, list[dict[str, Any]]] = {}
        for r in all_rows:
            node_rows_map2.setdefault(r.node_key, []).append({
                "status": r.status, "fail_strategy": r.fail_strategy,
            })
        ctx2 = _build_edge_ctx(execution, dag_ver)
        _refresh_ctx_from_db(execution, ctx2)
        sync_runnables = compute_next_runnables(
            nodes, edges, node_rows_map2, ctx2,
            execution_fail_strategy=execution.fail_strategy,
        )
        for nk in sync_runnables:
            node = _find_node(nodes, nk)
            if node is None:
                continue
            if str(node.get("node_type", "")) != "end":
                continue
            rows = _prep_node_rows(execution, dag_ver, node, user_id)
            if rows:
                sync_now = timezone.now()
                for row in rows:
                    row.status = STATUS_SUCCESS
                    row.queued_at = sync_now
                    row.started_at = sync_now
                    row.finished_at = sync_now
                    row.duration_ms = 0
                WorkflowNodeExecution.objects.bulk_create(rows, batch_size=500)

        # Synchronously completed workflows (e.g., start→end empty DAG, synchronous control nodes) require immediate terminal state determination,
        # otherwise execution.status will remain stuck at RUNNING until the next advance_workflow is dispatched.
        _finalize_workflow_if_done(execution)

        # Dry run not counted in execution count; other trigger types update exec_count and last_exec_time
        if trigger_type != "dryrun":
            workflow.exec_count = (workflow.exec_count or 0) + 1
            workflow.last_exec_time = timezone.now()
            workflow.save(update_fields=["exec_count", "last_exec_time", "update_datetime"])

        return TriggerResult(
            execution_id=execution.pk,
            dag_version_id=dag_ver.pk,
            initial_runnables=runnables,
        )

    # ------------------------------------------------------------------ cancel
    @transaction.atomic
    def cancel_workflow(
        self, execution_id: int, *, reason: str | None = None,
    ) -> None:
        from taurus.models import WorkflowExecution
        from taurus.workflow.models import WorkflowNodeExecution

        execution = WorkflowExecution.objects.filter(pk=execution_id).first()
        if execution is None:
            raise WorkflowRunnerError(f"E3040: WorkflowExecution#{execution_id} does not exist")
        rows = list(WorkflowNodeExecution.objects.filter(
            execution_id=execution.pk,
            status__in=(STATUS_PENDING, STATUS_RUNNING),
        ))
        for row in rows:
            if row.status == STATUS_RUNNING:
                try:
                    adapter_cls = get_registry().get_class(row.node_type)
                    adapter = adapter_cls()
                    cfg = _build_rendered_cfg(row, params=dict(row.rendered_params or {}))
                    uo = adapter.cancel(cfg, dict(row.adapter_state or {}))
                    _apply_unit_output(row, uo, initial=False)
                except Exception:  # noqa: BLE001  cancel best-effort
                    logger.exception(
                        "E3041: cancel adapter failed dispatch_id=%s", row.dispatch_id
                    )
                    row.status = STATUS_CANCELLED
                    row.finished_at = timezone.now()
                    row.adapter_state = dict(row.adapter_state or {})
                    row.adapter_state.setdefault("__runner__", {})["cancel_error"] = True
            # Force terminal state (cancel success returning SUCCESS doesn't mean it wasn't cancelled,
            # unified to CANCELLED here for easy distinction from actually successful tasks)
            if row.status != STATUS_CANCELLED:
                row.status = STATUS_CANCELLED
                row.finished_at = row.finished_at or timezone.now()
            if not row.error_message:
                row.error_message = reason or "workflow cancelled"
            row.save(update_fields=[
                "status", "finished_at", "error_message",
                "adapter_state", "output", "output_refs", "exit_code",
                "duration_ms", "update_datetime",
            ])
        execution.status = 4  # Legacy enum: cancelled
        execution.end_time = timezone.now()
        execution.save(update_fields=["status", "end_time", "update_datetime"])

    # ----------------------------------------------------------------- advance
    def advance_workflow(self, execution_id: int, *, user_id: int | None = None) -> AdvanceTick:
        """Advance workflow one step: poll → status update → push next batch of runnables → dispatch → finalization.

        This method is the entry point called by the "external loop" every tick. Idempotency: calling multiple times
        for the same execution won't cause duplicate dispatch.

        Note: poll phase doesn't hold a long transaction, avoiding REPEATABLE READ snapshot causing
        OpsExecution unable to read latest commit status (see _OpsExecutionMixin.poll).
        Dispatch phase uses an independent transaction to ensure write atomicity.
        """
        from taurus.models import WorkflowExecution
        from taurus.workflow.models import WorkflowDAGVersion, WorkflowNodeExecution

        import time as _time
        _tick_t0 = _time.perf_counter()

        execution = WorkflowExecution.objects.select_related("workflow").filter(pk=execution_id).first()
        if execution is None:
            raise WorkflowRunnerError(f"E3100: WorkflowExecution#{execution_id} does not exist")
        dag_ver: WorkflowDAGVersion | None = execution.dag_version
        if dag_ver is None:
            raise WorkflowRunnerError(f"E3101: WorkflowExecution#{execution_id} no dag_version")

        dag_def = dag_ver.definition or {}
        nodes: list[dict[str, Any]] = list(dag_def.get("nodes") or [])
        edges: list[dict[str, Any]] = normalize_edges(list(dag_def.get("edges") or []))

        tick = AdvanceTick(
            execution_id=execution_id,
            polled=0,
            newly_completed=0,
            newly_dispatched=0,
            newly_skipped=0,
            finished=False,
        )

        # 1) Build ctx (refresh node_statuses / node_outputs from DB first)
        ctx = _build_edge_ctx(execution, dag_ver)
        _refresh_ctx_from_db(execution, ctx)

        # 2) Poll all RUNNING rows —— no transaction execution, ensure OpsExecution reads latest commit
        #    (if REPEATABLE READ transaction is opened here, the OpsExecution snapshot read after
        #     select_for_update would misjudge completed ops as RUNNING)
        running_rows = list(
            WorkflowNodeExecution.objects.filter(
                execution_id=execution.pk, status=STATUS_RUNNING,
            )
        )
        tick.polled = len(running_rows)
        completed_keys_this_tick: set[str] = set()
        for row in running_rows:
            self._poll_one_row(row)
            if row.status in TERMINAL_STATUSES:
                tick.newly_completed += 1
                completed_keys_this_tick.add(row.node_key)

        if tick.polled > 0 and tick.newly_completed == 0:
            for row in running_rows:
                if row.status not in TERMINAL_STATUSES:
                    logger.warning(
                        "[wf-advance] ⚠ node still not terminal after poll node_key=%s pk=%d "
                        "row.status=%d adapter_state_keys=%s",
                        row.node_key, row.pk, row.status,
                        list((row.adapter_state or {}).keys()) if isinstance(row.adapter_state, dict) else "N/A",
                    )

        # === Below phases all execute in an independent transaction, ensuring write atomicity ===
        with transaction.atomic():
            # 2.05) Failed retry (S1-05): rows that became FAILED this tick and attempt_no < max_retries
            #       and error code belongs to retryable class (E2xxx/E4xxx) → create new PENDING row with attempt_no+1
            retry_created = self._retry_failed_rows(execution, dag_ver, completed_keys_this_tick, user_id)
            tick.newly_dispatched += retry_created

            # 2.1) Process fail_strategy: if fail_fast and there are nodes FAILED this tick,
            #      mark all PENDING / RUNNING nodes as CANCELLED (RUNNING first cancelled then marked)
            if execution.fail_strategy == "fail_fast":
                failed_exists = WorkflowNodeExecution.objects.filter(
                    execution_id=execution.pk, status=STATUS_FAILED,
                ).exists()
                if failed_exists:
                    self._apply_fail_fast_cancel(execution, user_id)

            # 3) Refresh ctx again (including just-polled results)
            _refresh_ctx_from_db(execution, ctx)

            # 3.1) Loop aggregate: check whether all child nodes of loop parent nodes are completed
            loop_parents = self._collect_loop_parent_rows(execution)
            for lp in loop_parents:
                aggregated = self._aggregate_loop_children(
                    execution, dag_ver, lp, ctx, user_id=user_id,
                )
                tick.newly_completed += aggregated

            # 3.2) Refresh ctx again (including aggregate results)
            _refresh_ctx_from_db(execution, ctx)

            # 4) Compute next runnables
            #    First build node_execution_rows dict (node_key -> rows, only care about status)
            all_rows = list(
                WorkflowNodeExecution.objects.filter(execution_id=execution.pk)
                .only("node_key", "status", "fail_strategy")
            )
            node_rows_map: dict[str, list[dict[str, Any]]] = {}
            for r in all_rows:
                node_rows_map.setdefault(r.node_key, []).append({
                    "status": r.status, "fail_strategy": r.fail_strategy,
                })
            next_keys = compute_next_runnables(nodes, edges, node_rows_map, ctx, execution_fail_strategy=execution.fail_strategy)

            # 5) Expand hosts for new runnables, create NodeExecution rows and dispatch
            newly_created: list[WorkflowNodeExecution] = []
            now = timezone.now()
            for nk in next_keys:
                if nk in node_rows_map and node_rows_map[nk]:
                    # Row already exists, skip (avoid duplicate creation)
                    continue
                node = _find_node(nodes, nk)
                if node is None:
                    continue
                rows = _prep_node_rows(execution, dag_ver, node, user_id)
                if str(node.get("node_type", "")) == "end":
                    for row in rows:
                        row.status = STATUS_SUCCESS
                        row.queued_at = now
                        row.started_at = now
                        row.finished_at = now
                        row.duration_ms = 0
                newly_created.extend(rows)
            if newly_created:
                WorkflowNodeExecution.objects.bulk_create(newly_created, batch_size=500)

            # 5.1) Simultaneously dispatch PENDING rows for loop child nodes
            #      Note: don't use adapter_state__contains query because SQLite JSONField
            #      __contains behavior for complex object values is unreliable, switched to Python filter
            _all_pending = list(
                WorkflowNodeExecution.objects.filter(
                    execution_id=execution.pk,
                    status=STATUS_PENDING,
                )
            )
            loop_pending_rows = [
                r for r in _all_pending
                if r.adapter_state and r.adapter_state.get("__loop_child__")
            ]
            all_pending = newly_created + loop_pending_rows
            tick.newly_dispatched = self._dispatch_pending(
                execution, dag_ver, all_pending, ctx, user_id=user_id,
            )

            # 5.2) Post-dispatch secondary aggregate check
            #      Synchronous adapters (e.g., testing_noop) become SUCCESS immediately after dispatch,
            #      at this point child nodes are terminal but aggregate hasn't triggered yet.
            #      Must check again before _finalize, otherwise workflow would be marked as finished prematurely.
            if tick.newly_dispatched > 0:
                _refresh_ctx_from_db(execution, ctx)
                loop_parents_2 = self._collect_loop_parent_rows(execution)
                for lp in loop_parents_2:
                    aggregated = self._aggregate_loop_children(
                        execution, dag_ver, lp, ctx, user_id=user_id,
                    )
                    tick.newly_completed += aggregated

            # 6) One-time fill SKIPPED rows for nodes that "didn't match any incoming edge and all upstream terminal"
            tick.newly_skipped = _mark_ineligible_nodes_skipped(
                execution, dag_ver, ctx, user_id=user_id,
            )

            # 7) Determine whether the workflow is overall completed
            if _finalize_workflow_if_done(execution):
                tick.finished = True

        wf_metrics.record_tick_duration(_time.perf_counter() - _tick_t0)
        return tick

    # ========================================================== internal implementation details
    def _retry_failed_rows(
        self,
        execution: Any,
        dag_ver: Any,
        completed_keys_this_tick: set[str],
        user_id: int | None,
    ) -> int:
        """S1-05 Failed retry: for rows that became FAILED this tick, create retry rows per max_retries.

        Retry conditions (all must be met):
          1) Row status == FAILED (just failed this tick)
          2) attempt_no < node.max_retries
          3) error_message contains retryable error code prefix (E2xxx/E4xxx), or no error code (conservative retry)

        Retry action:
          - Create new WorkflowNodeExecution row, attempt_no = original + 1
          - dispatch_id contains new attempt_no (ensures idempotency key is unique)
          - Status PENDING, handled by subsequent _dispatch_pending
          - Original failed row kept (audit trail)
        Returns number of newly created retry rows.
        """
        from taurus.workflow.models import WorkflowNodeExecution

        if not completed_keys_this_tick:
            return 0

        # Pull rows that became FAILED this tick (filter by node_key)
        failed_rows = list(
            WorkflowNodeExecution.objects.filter(
                execution_id=execution.pk,
                status=STATUS_FAILED,
                node_key__in=list(completed_keys_this_tick),
            ).order_by("node_key", "-attempt_no")
        )
        if not failed_rows:
            return 0

        # For each (node_key, host_id), only take the latest attempt_no's failed row
        latest_per_key_host: dict[tuple[str, str], Any] = {}
        for r in failed_rows:
            k = (r.node_key, str(r.host_id))
            if k not in latest_per_key_host or r.attempt_no > latest_per_key_host[k].attempt_no:
                latest_per_key_host[k] = r

        nodes = list(dag_ver.definition.get("nodes") or [])
        nodes_by_key = {str(n.get("node_key", "")): n for n in nodes}

        retry_rows: list[WorkflowNodeExecution] = []
        now = timezone.now()
        for (node_key, host_id), row in latest_per_key_host.items():
            node = nodes_by_key.get(node_key)
            if node is None:
                continue
            # Node from definition is vue-flow raw node, normalize first to ensure
            # max_retries / retry_delay_ms are promoted from data.config to top level
            _ensure_node_engine_fields(node)
            max_retries = int(node.get("max_retries", 0) or 0)
            if max_retries <= 0:
                continue
            if row.attempt_no >= max_retries:
                continue

            # Error code retry determination: E2xxx (host/network/infrastructure) / E4xxx (timeout) are retryable
            err_msg = str(row.error_message or "")
            is_retryable = False
            if err_msg:
                import re

                m = re.search(r"E(\d{4})", err_msg)
                if m:
                    code = int(m.group(1))
                    is_retryable = (2000 <= code < 3000) or (4000 <= code < 5000)
                else:
                    is_retryable = False
            else:
                # No error code (e.g., dispatch exception not categorized), conservative: don't retry
                is_retryable = False

            if not is_retryable:
                continue

            retry_delay_ms = int(node.get("retry_delay_ms", 0) or 0)
            queued_at = now
            if retry_delay_ms > 0:
                from datetime import timedelta

                queued_at = now + timedelta(milliseconds=retry_delay_ms)

            new_attempt = row.attempt_no + 1
            retry_rows.append(
                WorkflowNodeExecution(
                    execution=execution,
                    dag_version=dag_ver,
                    node_key=node_key,
                    node_type=row.node_type,
                    node_name=row.node_name,
                    host_id=host_id,
                    dispatch_id=_make_dispatch_id(execution.pk, node_key, host_id, new_attempt),
                    status=STATUS_PENDING,
                    attempt_no=new_attempt,
                    fail_strategy=row.fail_strategy,
                    timeout_sec=row.timeout_sec,
                    queued_at=queued_at,
                    user_id=user_id or row.user_id,
                    creator_id=user_id or row.creator_id,
                    modifier=str(user_id) if user_id is not None else None,
                    dept_belong_id=None,
                    create_datetime=now,
                    update_datetime=now,
                    last_error_code=row.last_error_code,
                )
            )

        if retry_rows:
            WorkflowNodeExecution.objects.bulk_create(retry_rows, batch_size=500)
        return len(retry_rows)

    # S1-02 PollingprotectedParameters
    MAX_POLL_ATTEMPTS = 200       # Max poll attempts per row (fallback protection; primary criterion changed to time)
    MAX_RUNTIME_SEC = 7200        # Max RUNNING survival time per node (seconds, 2 hours; safety net for unconfigured timeout)
    POLL_BACKOFF_INITIAL_SEC = 2  # Exponential backoff initial interval
    POLL_BACKOFF_MAX_SEC = 30     # Exponential backoff upper bound

    def _poll_one_row(self, row: Any) -> None:
        if row.status != STATUS_RUNNING:
            return

        # [Fix Bug 1] Exponential backoff actually takes effect: if time since last poll hasn't reached backoff interval, skip this round directly
        # Don't call adapter.poll(), don't increment poll_count, avoid user frequent refresh causing 200 empty rounds to exhaust
        poll_count = int(getattr(row, "poll_count", 0) or 0)
        now = timezone.now()
        state = dict(row.adapter_state or {})
        backoff_sec = float(state.get("__backoff_sec__") or 0)
        last_poll_ts_raw = state.get("__last_poll_ts__")
        last_poll_ts: Any = None
        if isinstance(last_poll_ts_raw, str):
            from django.utils.dateparse import parse_datetime
            try:
                last_poll_ts = parse_datetime(last_poll_ts_raw)
            except Exception:  # noqa: BLE001
                last_poll_ts = None
        if last_poll_ts is None and row.update_datetime is not None:
            last_poll_ts = row.update_datetime
        if (
            backoff_sec > 0
            and last_poll_ts is not None
            and (now - last_poll_ts).total_seconds() < backoff_sec
        ):
            # Backoff not reached, skip (change nothing — don't write update_datetime, don't increment poll_count)
            return

        # S1-02 timeout protection 1: time-based E4401 safety net
        # If node hasn't completed within MAX_RUNTIME_SEC from started_at, determine as zombie node.
        # This is more reliable than poll_count — poll_count is affected by user refresh frequency, while time is objective.
        if row.started_at:
            elapsed_total = (now - row.started_at).total_seconds()
            if elapsed_total > self.MAX_RUNTIME_SEC:
                row.status = STATUS_FAILED
                row.finished_at = now
                row.error_message = (
                    f"E4401: Node execution timeout (max_runtime={self.MAX_RUNTIME_SEC}s, "
                    f"elapsed={elapsed_total:.1f}s, poll_count={poll_count}),"
                    f" suspected adapter stuck or executor end status not written back"
                )
                row.save(update_fields=["status", "finished_at", "error_message", "update_datetime"])
                logger.warning(
                    "E4401 time timeout: dispatch_id=%s elapsed=%.1fs poll_count=%d",
                    row.dispatch_id, elapsed_total, poll_count,
                )
                return

        # Fallback protection: poll_count exceeds limit (extreme scenario — when started_at is empty, can still use count for determination)
        if poll_count >= self.MAX_POLL_ATTEMPTS:
            row.status = STATUS_FAILED
            row.finished_at = now
            row.error_message = (
                f"E4401: Poll count exceeded limit (max={self.MAX_POLL_ATTEMPTS}, actual={poll_count}),"
                f" suspected adapter stuck or status not updated"
            )
            row.save(update_fields=["status", "finished_at", "error_message", "update_datetime"])
            return

        # S1-02 timeout protection 2: node timeout_sec expires → determine as failed E4402
        timeout_sec = int(getattr(row, "timeout_sec", 0) or 0)
        if timeout_sec > 0 and row.started_at:
            from datetime import timedelta

            deadline = row.started_at + timedelta(seconds=timeout_sec)
            if now > deadline:
                row.status = STATUS_FAILED
                row.finished_at = now
                row.error_message = (
                    f"E4402: Node execution timeout (timeout={timeout_sec}s,"
                    f" elapsed={(now - row.started_at).total_seconds():.1f}s)"
                )
                # Attempt adapter cancel (cleanup remote resources)
                try:
                    adapter_cls = get_registry().get_class(row.node_type)
                    adapter = adapter_cls()
                    cfg = _build_rendered_cfg(row, params=dict(row.rendered_params or {}))
                    adapter.cancel(cfg, dict(row.adapter_state or {}))
                except Exception:  # noqa: BLE001
                    logger.warning("Timeout cancel adapter failed dispatch_id=%s", row.dispatch_id, exc_info=True)
                row.save(update_fields=["status", "finished_at", "error_message", "update_datetime"])
                return

        # S1-02 Exponential backoff: compute next wait interval based on poll_count.
        # Backoff formula: min(initial * 2^poll_count, max)
        backoff = min(
            self.POLL_BACKOFF_INITIAL_SEC * (2 ** poll_count),
            self.POLL_BACKOFF_MAX_SEC,
        )
        try:
            adapter_cls = get_registry().get_class(row.node_type)
        except Exception as exc:  # noqa: BLE001
            row.status = STATUS_FAILED
            row.finished_at = now
            row.error_message = f"E3110: adapter load failed {exc}"
            row.save(update_fields=["status", "finished_at", "error_message", "update_datetime"])
            return
        adapter = adapter_cls()
        _before_status = row.status
        try:
            cfg = _build_rendered_cfg(row, params=dict(row.rendered_params or {}))
            uo = adapter.poll(cfg, dict(row.adapter_state or {}))
        except Exception as exc:  # noqa: BLE001
            logger.exception("E3111: poll exception dispatch_id=%s", row.dispatch_id)
            row.status = STATUS_FAILED
            row.finished_at = now
            row.error_message = f"E3111: poll threw exception {type(exc).__name__}: {exc}"
            row.save(update_fields=["status", "finished_at", "error_message", "update_datetime"])
            return
        fields = _apply_unit_output(row, uo, initial=False)

        if row.status not in TERMINAL_STATUSES and uo.status in TERMINAL_STATUSES:
            logger.info(
                "[wf-poll] ⚠ status not advanced node_key=%s pk=%d before_status=%d uo_status=%d "
                "after_status=%d fields=%s adapter_state_keys=%s",
                row.node_key, row.pk, _before_status, uo.status,
                row.status, fields,
                list((row.adapter_state or {}).keys()) if isinstance(row.adapter_state, dict) else "N/A",
            )
        else:
            logger.debug(
                "[wf-poll] node_key=%s pk=%d before=%d uo=%d after=%d fields=%s",
                row.node_key, row.pk, _before_status, uo.status, row.status, fields,
            )

        # S1-02 Write backoff suggestion + this poll timestamp to adapter_state, for next tick to determine whether to skip
        if row.status == STATUS_RUNNING:
            state_new = dict(row.adapter_state or {})
            state_new["__backoff_sec__"] = backoff
            state_new["__poll_count__"] = poll_count + 1
            state_new["__last_poll_ts__"] = now.isoformat()
            row.adapter_state = state_new
            if "adapter_state" not in fields:
                fields.append("adapter_state")
        row.save(update_fields=fields)
        wf_metrics.observe_unit_output(row.node_type, uo, is_dispatch=False)
        if row.status in TERMINAL_STATUSES and row.started_at and row.finished_at:
            wf_metrics.record_node_duration(
                row.node_type,
                (row.finished_at - row.started_at).total_seconds(),
            )

    def _dispatch_pending(
        self,
        execution: Any,
        dag_ver: Any,
        candidate_rows: list[Any],
        ctx: dict[str, Any],
        *,
        user_id: int | None,
    ) -> int:
        """Validate_and_render → dispatch → persist PENDING rows one by one.

        1) Pure function render_node_config does interpolation;
        2) adapter.validate_config() checks final rendered parameters;
           - (Real adapter's validate_and_render is "render + runtime extra check", we've already done render in step 1,
              if adapter has runtime checks it can call again; here use safer "post-render validate_config" composite.)
        3) Build RenderedNodeConfig → adapter.dispatch(cfg)
        4) dispatch returns UnitOutput:
           - RUNNING → row status RUNNING, adapter_state saved for later poll
           - SUCCESS/FAILED → synchronous task, directly marked terminal, no more poll
        5) Failed always marks this row as FAILED, no longer raises, avoiding entire transaction rollback

        Returns number of rows that actually "entered RUNNING or synchronous terminal state" (for tick.newly_dispatched stats).
        """
        dispatched = 0
        for row in candidate_rows:
            if row.status != STATUS_PENDING:
                continue

            is_loop_child = bool(
                row.adapter_state and row.adapter_state.get("__loop_child__")
            )

            if is_loop_child:
                loop_item = row.adapter_state.get("loop_item")
                body_params = row.adapter_state.get("body_params") or {}
                merged_params = dict(body_params) if isinstance(body_params, dict) else {}
                if loop_item is not None:
                    merged_params["loop_item"] = loop_item
                node = {
                    "node_key": row.node_key,
                    "node_type": row.node_type,
                    "title": row.node_name,
                    "params": merged_params,
                }
            else:
                node = _find_node(list(dag_ver.definition.get("nodes") or []), row.node_key)
                # Node taken from dag_ver.definition["nodes"] is raw vue-flow structure,
                # need to first complete engine fields (ensure render_node_config reads correct top-level structure)
                if node is not None:
                    _ensure_node_engine_fields(node)
            if node is None:
                row.status = STATUS_FAILED
                row.finished_at = timezone.now()
                row.error_message = f"E3120: node_key={row.node_key} not found in DAG"
                row.save(update_fields=["status", "finished_at", "error_message", "update_datetime"])
                continue
            try:
                adapter_cls = get_registry().get_class(row.node_type)
            except Exception as exc:  # noqa: BLE001
                row.status = STATUS_FAILED
                row.finished_at = timezone.now()
                row.error_message = f"E3121: adapter load failed node_type={row.node_type!r} err={exc}"
                row.save(update_fields=["status", "finished_at", "error_message", "update_datetime"])
                continue

            adapter = adapter_cls()
            try:
                wf_ctx = _build_workflow_context(ctx)
                rendered = render_node_config(
                    adapter, node, wf_ctx,
                    host_id=row.host_id, attempt_no=row.attempt_no,
                    fallback_fail_strategy=row.fail_strategy,
                )
            except RenderError as exc:
                row.status = STATUS_FAILED
                row.finished_at = timezone.now()
                row.error_message = f"E3122: render failed {exc}"
                row.save(update_fields=["status", "finished_at", "error_message", "update_datetime"])
                continue

            try:
                vr = adapter.validate_config(rendered)
                if not vr.ok:
                    errs = vr.errors or {}
                    first = next(iter(errs.items()), None)
                    if first:
                        msg = f"validate_config failed {first[0]}: {first[1]}"
                    else:
                        msg = "validate_config failed"
                    row.status = STATUS_FAILED
                    row.finished_at = timezone.now()
                    row.error_message = f"E3123: {msg}"
                    row.save(update_fields=["status", "finished_at", "error_message", "update_datetime"])
                    continue
            except Exception as exc:  # noqa: BLE001
                logger.exception("E3124: validate_config threw exception dispatch_id=%s", row.dispatch_id)
                row.status = STATUS_FAILED
                row.finished_at = timezone.now()
                row.error_message = f"E3124: validate_config exception {type(exc).__name__}: {exc}"
                row.save(update_fields=["status", "finished_at", "error_message", "update_datetime"])
                continue

            # Fill basic fields (regardless of dispatch success or not, these are required)
            row.rendered_params = dict(rendered)
            if user_id is not None:
                row.user_id = user_id
            row.started_at = timezone.now()

            cfg = _build_rendered_cfg(row, params=dict(rendered))
            # Optionalhook on_before_dispatch
            try:
                adapter.on_before_dispatch(cfg)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "on_before_dispatch hook failed dispatch_id=%s",
                    row.dispatch_id, exc_info=True,
                )
            try:
                uo = adapter.dispatch(cfg)
            except Exception as exc:  # noqa: BLE001
                logger.exception("E3125: dispatch threw exception dispatch_id=%s", row.dispatch_id)
                row.status = STATUS_FAILED
                row.finished_at = timezone.now()
                row.error_message = f"E3125: dispatch exception {type(exc).__name__}: {exc}"
                row.save(update_fields=[
                    "status", "started_at", "finished_at",
                    "error_message", "rendered_params",
                    "user_id" if user_id is not None else None,
                    "update_datetime",
                ])
                continue

            # First set basic status to RUNNING (ensure _apply_unit_output's transition is valid)
            row.status = STATUS_RUNNING
            save_fields: list[str] = _apply_unit_output(row, uo, initial=True)
            # When dispatch first persists, status must be persisted (even RUNNING→RUNNING no change,
            # row in DB is still PENDING, needs explicit write)
            if "status" not in save_fields:
                save_fields.append("status")
            # Add fields prepared by on_before_dispatch but not touched by _apply_unit_output
            if "rendered_params" not in save_fields:
                save_fields.append("rendered_params")
            if "started_at" not in save_fields:
                save_fields.append("started_at")
            if user_id is not None and "user_id" not in save_fields:
                save_fields.append("user_id")
            # finalizationhook
            if row.status in TERMINAL_STATUSES:
                try:
                    adapter.on_after_finish(_build_rendered_cfg(row, params=dict(rendered)), uo)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "on_after_finish hook failed dispatch_id=%s",
                        row.dispatch_id, exc_info=True,
                    )
            row.save(update_fields=[f for f in save_fields if f])
            dispatched += 1
            wf_metrics.observe_unit_output(row.node_type, uo, is_dispatch=True)
            if row.status in TERMINAL_STATUSES and row.started_at and row.finished_at:
                wf_metrics.record_node_duration(
                    row.node_type,
                    (row.finished_at - row.started_at).total_seconds(),
                )

            if uo.status in TERMINAL_STATUSES and uo.output and uo.output.get("loop_items"):
                self._expand_loop_children(
                    execution, dag_ver, row, uo, ctx, user_id=user_id,
                )
        return dispatched

    # ------------------------------------------------------------------ loop support
    def _expand_loop_children(
        self,
        execution: Any,
        dag_ver: Any,
        parent_row: Any,
        uo: UnitOutput,
        ctx: dict[str, Any],
        *,
        user_id: int | None,
    ) -> None:
        """Loop node expansion: create child node execution rows for each loop_item.

        Called when loop node dispatch returns SUCCESS + loop_items.
        Creates a PENDING child row for each item, child row node_key = "{parent}__loop_{i}",
        uses body_node_type to specify adapter.
        """
        from taurus.workflow.models import WorkflowNodeExecution

        output = uo.output or {}
        loop_items = output.get("loop_items") or []
        body_node_type = output.get("body_node_type") or ""
        body_params = output.get("body_params") or {}
        if not loop_items or not body_node_type:
            return

        parent_key = parent_row.node_key
        now = timezone.now()
        child_rows: list[WorkflowNodeExecution] = []

        # Loop child node timeout calculation: body_node_type may differ from parent node
        # if body_node_type supports timeout and parent node timeout_sec is 0, inject global timeout
        child_timeout_sec = parent_row.timeout_sec
        if child_timeout_sec == 0 and body_node_type in _TIMEOUT_ELIGIBLE_NODE_TYPES:
            ctx_data = execution.context or {}
            global_timeout = int(ctx_data.get("global_timeout_sec", 0) or 0)
            if global_timeout > 0:
                child_timeout_sec = global_timeout

        for i, item in enumerate(loop_items):
            child_key = f"{parent_key}__loop_{i}"
            child_rows.append(
                WorkflowNodeExecution(
                    execution=execution,
                    dag_version=dag_ver,
                    node_key=child_key,
                    node_type=body_node_type,
                    node_name=f"{parent_row.node_name} iteration {i+1}/{len(loop_items)}",
                    host_id=parent_row.host_id,
                    dispatch_id=_make_dispatch_id(
                        execution.pk, child_key, parent_row.host_id,
                    ),
                    status=STATUS_PENDING,
                    attempt_no=1,
                    fail_strategy=parent_row.fail_strategy,
                    timeout_sec=child_timeout_sec,
                    queued_at=now,
                    user_id=user_id,
                    creator_id=user_id,
                    modifier=str(user_id) if user_id is not None else None,
                    adapter_state={
                        "__loop_child__": True,
                        "loop_index": i,
                        "loop_item": item,
                        "loop_parent_node_key": parent_key,
                        "loop_total": len(loop_items),
                        "body_params": body_params,
                    },
                    dept_belong_id=None,
                    create_datetime=now,
                    update_datetime=now,
                ),
            )

        if child_rows:
            WorkflowNodeExecution.objects.bulk_create(child_rows, batch_size=500)
            logger.info(
                "Loop expanded: parent=%s, children=%d, body_type=%s",
                parent_key, len(child_rows), body_node_type,
            )

    def _aggregate_loop_children(
        self,
        execution: Any,
        dag_ver: Any,
        parent_row: Any,
        ctx: dict[str, Any],
        *,
        user_id: int | None,
    ) -> int:
        """Aggregate loop child node results.

        Check whether all __loop_child__ child rows of parent_row have reached terminal state.
        If all completed, aggregate output to parent node and mark parent node as SUCCESS/FAILED.
        If child rows still in RUNNING/PENDING, return 0.
        """
        from taurus.workflow.models import WorkflowNodeExecution

        parent_key = parent_row.node_key
        child_rows = list(
            WorkflowNodeExecution.objects.filter(
                execution_id=execution.pk,
                node_key__startswith=f"{parent_key}__loop_",
            )
        )
        if not child_rows:
            state = parent_row.adapter_state or {}
            if state.get("loop_items") or parent_row.node_type == "loop":
                parent_row.output = {
                    "loop_results": [],
                    "loop_count": 0,
                    "loop_success": True,
                }
                parent_row.status = STATUS_SUCCESS
                parent_row.error_message = None
                parent_row.finished_at = timezone.now()
                if parent_row.started_at:
                    parent_row.duration_ms = max(
                        0, int((parent_row.finished_at - parent_row.started_at).total_seconds() * 1000)
                    )
                parent_row.adapter_state = dict(state)
                parent_row.adapter_state["loop_aggregated"] = True
                parent_row.save(update_fields=[
                    "status", "output", "adapter_state", "finished_at",
                    "duration_ms", "error_message", "update_datetime",
                ])
                logger.info(
                    "Loop aggregated (empty): parent=%s",
                    parent_key,
                )
                return 1
            return 0

        all_terminal = True
        any_failed = False
        outputs: list[dict[str, Any]] = []
        for child in child_rows:
            if child.status not in TERMINAL_STATUSES:
                all_terminal = False
                break
            if child.status == STATUS_FAILED:
                any_failed = True
            outputs.append({
                "index": child.adapter_state.get("loop_index"),
                "item": child.adapter_state.get("loop_item"),
                "status": child.status,
                "output": child.output or {},
                "error_message": child.error_message or "",
            })

        if not all_terminal:
            return 0

        parent_row.output = {
            "loop_results": outputs,
            "loop_count": len(child_rows),
            "loop_success": not any_failed,
        }
        if any_failed:
            parent_row.status = STATUS_FAILED
            parent_row.error_message = "Some loop child nodes failed"
        else:
            parent_row.status = STATUS_SUCCESS
            parent_row.error_message = None
        parent_row.finished_at = timezone.now()
        if parent_row.started_at:
            parent_row.duration_ms = max(
                0, int((parent_row.finished_at - parent_row.started_at).total_seconds() * 1000)
            )
        parent_row.adapter_state = dict(parent_row.adapter_state or {})
        parent_row.adapter_state["loop_aggregated"] = True
        parent_row.save(update_fields=[
            "status", "output", "adapter_state", "finished_at",
            "duration_ms", "error_message", "update_datetime",
        ])
        logger.info(
            "Loop aggregated: parent=%s, children=%d, failed=%s",
            parent_key, len(child_rows), any_failed,
        )
        return 1

    def _collect_loop_parent_rows(self, execution: Any) -> list[Any]:
        """Collect all loop parent node rows that need aggregate checking.

        Find all nodes with loop_items (or node_type=loop) that haven't completed aggregation yet,
        no status restriction, because loop parent nodes become SUCCESS immediately after dispatch,
        but need to wait for all child nodes to complete before being marked as actually finished.

        Note: adapter_state __contains query in SQLite JSONField for complex
        object values (like loop_item={"name": "x"}) is unreliable, therefore use full load
        + Python filter to ensure cross-database backend consistency.
        """
        from taurus.workflow.models import WorkflowNodeExecution

        parents: list[Any] = []
        rows = list(
            WorkflowNodeExecution.objects.filter(
                execution_id=execution.pk,
            )
        )
        for row in rows:
            state = row.adapter_state or {}
            if state.get("__loop_child__"):
                continue
            if state.get("loop_aggregated"):
                continue
            if row.node_type == "loop" or state.get("loop_items"):
                parents.append(row)
        return parents

    def _apply_fail_fast_cancel(self, execution: Any, user_id: int | None) -> None:
        """fail_fast strategy: once a failed node appears → cancel remaining PENDING/RUNNING."""
        from taurus.workflow.models import WorkflowNodeExecution

        rows = list(
            WorkflowNodeExecution.objects.filter(
                execution_id=execution.pk,
                status__in=(STATUS_PENDING, STATUS_RUNNING),
            ).select_for_update()
        )
        for row in rows:
            if row.status == STATUS_RUNNING:
                try:
                    adapter_cls = get_registry().get_class(row.node_type)
                    adapter = adapter_cls()
                    cfg = _build_rendered_cfg(row, params=dict(row.rendered_params or {}))
                    uo = adapter.cancel(cfg, dict(row.adapter_state or {}))
                    _apply_unit_output(row, uo, initial=False)
                except Exception:  # noqa: BLE001
                    logger.exception("E3130: fail_fast cancel failed dispatch_id=%s", row.dispatch_id)
            # Force set to CANCELLED (consistent with cancel_workflow)
            if row.status != STATUS_CANCELLED:
                row.status = STATUS_CANCELLED
                row.finished_at = row.finished_at or timezone.now()
            if not row.error_message:
                row.error_message = "fail_fast: upstream failed"
            if user_id is not None:
                row.user_id = user_id
            save_fields = [
                "status", "finished_at", "error_message",
                "adapter_state", "output", "output_refs",
                "exit_code", "duration_ms", "update_datetime",
            ]
            if user_id is not None:
                save_fields.append("user_id")
            row.save(update_fields=save_fields)