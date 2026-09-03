"""S5-03 压测基line:100 节. × 10 Host的 DAG AdvancePerformance.

validate.:
  1. 100 节. × 10 Host = 1000 entries NodeExecution 行能正确create
  2. advance_workflow 单 tick Advance耗时 < 5s(基line)
  3. all节.最终到达 SUCCESS

run:
  python -m pytest tests/workflow/perf_dag_100nodes_10hosts.py -v -s
"""
from __future__ import annotations

import time

import pytest

from taurus.workflow.engine.runner import WorkflowRunner
from taurus.workflow.models import WorkflowDAGVersion, WorkflowNodeExecution

pytestmark = [pytest.mark.django_db, pytest.mark.perf]

NODE_COUNT = 100
HOST_COUNT = 10
TICK_TIMEOUT_SEC = 5.0


def _build_linear_dag(node_count: int, host_count: int) -> dict:
    """构造line性链 DAG:N1 → N2 → ... → N100, 每节.绑定 10 hosts."""
    host_ids = [f"host-{i:02d}" for i in range(host_count)]
    nodes = []
    for i in range(1, node_count + 1):
        nodes.append({
            "node_key": f"N{i:03d}",
            "node_name": f"压测节点 {i}",
            "node_type": "testing_noop",
            "params": {"value": f"result-{i}"},
            "host_ids": host_ids,
            "timeout_sec": 0,
            "fail_strategy": "fail_fast",
        })
    edges = []
    for i in range(1, node_count):
        edges.append({
            "from": f"N{i:03d}",
            "to": f"N{i + 1:03d}",
            "condition": "success(__from__)",
        })
    return {"nodes": nodes, "edges": edges}


@pytest.mark.perf
def test_perf_100nodes_10hosts():
    """100 节. × 10 Hostline性 DAG AdvancePerformance基line."""
    from django.contrib.auth import get_user_model
    from taurus.models import Workflow, WorkflowExecution

    User = get_user_model()
    user = User.objects.filter(is_superuser=True).first()
    if user is None:
        user = User.objects.create_user(username="perf-user", password="x")

    workflow = Workflow.objects.create(
        name="perf-workflow-100x10",
        workflow_mode="dag",
        status=1,
        global_envs={},
    )
    dag = _build_linear_dag(NODE_COUNT, HOST_COUNT)
    dag_ver = WorkflowDAGVersion.objects.create(
        workflow=workflow,
        version=1,
        definition=dag,
        global_envs={},
        release_note="perf baseline",
        creator=user,
    )
    workflow.dag_published_version = dag_ver
    workflow.save(update_fields=["dag_published_version", "update_datetime"])

    execution = WorkflowExecution.objects.create(
        workflow=workflow,
        dag_version=dag_ver,
        status=1,
        trigger_type="manual",
        fail_strategy="fail_fast",
        creator=user,
    )

    runner = WorkflowRunner()
    max_ticks = NODE_COUNT + 5
    total_tick_time = 0.0

    for tick_no in range(1, max_ticks + 1):
        t0 = time.perf_counter()
        tick = runner.advance_workflow(execution.pk, user_id=user.pk)
        elapsed = time.perf_counter() - t0
        total_tick_time += elapsed

        assert elapsed < TICK_TIMEOUT_SEC, (
            f"tick#{tick_no} 耗时 {elapsed:.3f}s 超过基line {TICK_TIMEOUT_SEC}s"
        )

        if tick.finished:
            break
    else:
        pytest.fail(f"工作流在 {max_ticks} 个 tick 内未完成")

    execution.refresh_from_db()
    assert execution.status == 2, f"工作流未成功完成，status={execution.status}"

    total_rows = WorkflowNodeExecution.objects.filter(execution_id=execution.pk).count()
    expected_rows = NODE_COUNT * HOST_COUNT
    assert total_rows == expected_rows, (
        f"NodeExecution 行数 {total_rows} != 预期 {expected_rows}"
    )

    failed_count = WorkflowNodeExecution.objects.filter(
        execution_id=execution.pk, status=3,
    ).count()
    assert failed_count == 0, f"有 {failed_count} 个节点失败"

    print(
        f"\n[perf] 100×10 DAG: ticks={tick_no}, "
        f"total_tick_time={total_tick_time:.3f}s, "
        f"avg_tick={total_tick_time / tick_no:.3f}s, "
        f"rows={total_rows}"
    )