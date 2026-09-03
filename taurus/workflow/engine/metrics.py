"""S1-06 WorkflowEngine Prometheus metrics definition and instrumentation.

Metrics manifest:
  - workflow_dispatch_total{node_type, status}
      dispatch call count (status: success/failed/running)
  - workflow_poll_total{node_type, status}
      poll call count
  - workflow_node_duration_seconds{node_type}
      Node execution duration histogram (dispatch → terminal state)
  - workflow_tick_duration_seconds
      advance_workflow single tick duration
  - workflow_active_executions
      Number of workflow executions currently in RUNNING state (Gauge)

Dependency: prometheus_client (pure Python, no django_prometheus middleware required)
Exposure: via /metrics endpoint (see urls.py)
"""
from __future__ import annotations

from typing import Any

try:
    from prometheus_client import Counter, Gauge, Histogram
    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False
    Counter = Gauge = Histogram = None  # type: ignore[assignment,misc]


if _HAS_PROMETHEUS:
    dispatch_total = Counter(
        "workflow_dispatch_total",
        "Workflow node dispatch call count",
        ["node_type", "status"],
    )
    poll_total = Counter(
        "workflow_poll_total",
        "Workflow node poll call count",
        ["node_type", "status"],
    )
    node_duration_seconds = Histogram(
        "workflow_node_duration_seconds",
        "Node execution duration (dispatch → terminal state)",
        ["node_type"],
        buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600),
    )
    tick_duration_seconds = Histogram(
        "workflow_tick_duration_seconds",
        "advance_workflow single tick duration",
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
    )
    active_executions = Gauge(
        "workflow_active_executions",
        "Number of workflow executions currently in RUNNING state",
    )
else:
    dispatch_total = None  # type: ignore[assignment]
    poll_total = None  # type: ignore[assignment]
    node_duration_seconds = None  # type: ignore[assignment]
    tick_duration_seconds = None  # type: ignore[assignment]
    active_executions = None  # type: ignore[assignment]


def record_dispatch(node_type: str, status: str) -> None:
    if dispatch_total is not None:
        dispatch_total.labels(node_type=node_type, status=status).inc()


def record_poll(node_type: str, status: str) -> None:
    if poll_total is not None:
        poll_total.labels(node_type=node_type, status=status).inc()


def record_node_duration(node_type: str, seconds: float) -> None:
    if node_duration_seconds is not None:
        node_duration_seconds.labels(node_type=node_type).observe(seconds)


def record_tick_duration(seconds: float) -> None:
    if tick_duration_seconds is not None:
        tick_duration_seconds.observe(seconds)


def set_active_executions(count: int) -> None:
    if active_executions is not None:
        active_executions.set(count)


def observe_unit_output(node_type: str, uo: Any, *, is_dispatch: bool) -> None:
    """Instrument according to UnitOutput status (unified entry point)."""
    from taurus.workflow.engine.schemas import STATUS_RUNNING, STATUS_SUCCESS, STATUS_FAILED

    status_map = {STATUS_RUNNING: "running", STATUS_SUCCESS: "success", STATUS_FAILED: "failed"}
    status_str = status_map.get(int(uo.status), "unknown")
    if is_dispatch:
        record_dispatch(node_type, status_str)
    else:
        record_poll(node_type, status_str)