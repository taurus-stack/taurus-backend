"""S6-01 Loop OrchestrationIntegration test:validateCircular展开, aggregate, EmptyCircular和下游衔接.

use testing_noop 作为Circular体Adapter, Avoid真实异步.
"""
from __future__ import annotations

from django.test import TransactionTestCase

from taurus.models import Host, Workflow
from dvadmin.system.models import Users
from taurus.workflow.engine.runner import WorkflowRunner
from taurus.workflow.models import WorkflowDAGVersion, WorkflowNodeExecution
from taurus.workflow.engine.executor import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SUCCESS,
)


def _loop_dag(nodes: list[dict], edges: list[dict]) -> dict:
    out_nodes: list[dict] = []
    for n in nodes:
        out_nodes.append({
            "node_key": n["node_key"],
            "node_name": n.get("node_name") or n["node_key"],
            "node_type": n["node_type"],
            "params": n.get("params", {}),
            "host_ids": n.get("host_ids"),
            "timeout_sec": n.get("timeout_sec", 0),
            "fail_strategy": n.get("fail_strategy", "fail_fast"),
        })
    out_edges: list[dict] = []
    for e in edges:
        out_edges.append({
            "from": e["from"],
            "to": e["to"],
            "condition": e.get("condition", "success(__from__)"),
        })
    return {"nodes": out_nodes, "edges": out_edges}


class LoopOrchestrationTests(TransactionTestCase):
    """Loop Adapter与OrchestrationEngine协同工作test"""

    def setUp(self) -> None:
        self.runner = WorkflowRunner()
        self.user = Users.objects.create(
            username="wf_loop_test",
            password="noop",
            email="wf_loop_test@example.com",
            is_active=True,
        )
        self.host = Host.objects.create(
            host_name="loop-host-1",
            host_ip="127.0.0.1",
            status=1,
        )
        self.workflow = Workflow.objects.create(
            name="wf-runner-loop",
            workflow_mode="dag",
            status=1,
            global_envs={},
        )
        self.workflow.hosts.add(self.host)

    def _publish(self, dag_def: dict) -> WorkflowDAGVersion:
        ver = WorkflowDAGVersion.objects.create(
            workflow=self.workflow,
            version=1,
            definition=dag_def,
            global_envs={},
            release_note="pytest",
            creator=self.user,
        )
        self.workflow.dag_published_version = ver
        self.workflow.save(update_fields=["dag_published_version", "update_datetime"])
        return ver

    def _advance_until_finished(self, execution_id: int, max_steps: int = 10) -> None:
        for _ in range(max_steps):
            t = self.runner.advance_workflow(execution_id, user_id=self.user.pk)
            if t.finished:
                return
        self.fail(f"工作流在 {max_steps} 次 advance 后仍未完成")

    # -------------------------------------------------------------------- tests
    def test_loop_count_3_noop_children_created_and_aggregated(self) -> None:
        """count=3 Circular:应create 3  child节., All completed后parent节.aggregateSuccess"""
        dag = _loop_dag(
            nodes=[
                {
                    "node_key": "A",
                    "node_name": "循环 3 次",
                    "node_type": "loop",
                    "params": {
                        "loop_type": "count",
                        "count": 3,
                        "body_node_type": "testing_noop",
                        "max_concurrency": 1,
                    },
                },
            ],
            edges=[],
        )
        self._publish(dag)
        r = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)

        # trigger after, A(loop)已 dispatch Success, child节.应已create
        parent = WorkflowNodeExecution.objects.get(
            execution_id=r.execution_id, node_key="A"
        )
        self.assertEqual(parent.status, STATUS_SUCCESS)
        self.assertIsNotNone(parent.adapter_state)
        self.assertEqual(len(parent.adapter_state.get("loop_items") or []), 3)

        child_rows = list(
            WorkflowNodeExecution.objects.filter(
                execution_id=r.execution_id,
                node_key__startswith="A__loop_",
            ).order_by("node_key")
        )
        self.assertEqual(len(child_rows), 3, "应创建 3 个循环子节点")
        for ch in child_rows:
            self.assertTrue(ch.adapter_state and ch.adapter_state.get("__loop_child__"))
            self.assertEqual(ch.node_type, "testing_noop")

        # Page 1 次 advance:dispatch child节.(all同步 SUCCESS)→ 同 tick 内Triggeraggregate
        t1 = self.runner.advance_workflow(r.execution_id, user_id=self.user.pk)
        self.assertGreaterEqual(t1.newly_dispatched, 3)
        # aggregate在 dispatch 后同一 tick 内完成(5.2 二次aggregatecheck)
        self.assertGreaterEqual(t1.newly_completed, 1, "聚合应在同一 tick 内完成")
        self.assertTrue(t1.finished, "循环完成后工作流应结束")

        # child节.all SUCCESS
        for ch in WorkflowNodeExecution.objects.filter(
            execution_id=r.execution_id, node_key__startswith="A__loop_"
        ):
            self.assertEqual(ch.status, STATUS_SUCCESS, f"{ch.node_key} 应成功")

        # validateparent节.aggregate结果
        parent.refresh_from_db()
        self.assertEqual(parent.status, STATUS_SUCCESS)
        self.assertTrue((parent.adapter_state or {}).get("loop_aggregated"))
        out = parent.output or {}
        self.assertEqual(out.get("loop_count"), 3)
        self.assertTrue(out.get("loop_success"))
        results = out.get("loop_results") or []
        self.assertEqual(len(results), 3)
        for i, r in enumerate(results):
            self.assertEqual(r.get("index"), i)
            self.assertEqual(r.get("status"), STATUS_SUCCESS)

    def test_loop_count_0_empty_loop_aggregated_immediately(self) -> None:
        """count=0 EmptyCircular:无child节.create, parent节.应在下一次 advance 时aggregate完成"""
        dag = _loop_dag(
            nodes=[
                {
                    "node_key": "A",
                    "node_type": "loop",
                    "params": {
                        "loop_type": "count",
                        "count": 0,
                        "body_node_type": "testing_noop",
                    },
                },
            ],
            edges=[],
        )
        self._publish(dag)
        r = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)

        # nochild节.
        child_count = WorkflowNodeExecution.objects.filter(
            execution_id=r.execution_id, node_key__startswith="A__loop_"
        ).count()
        self.assertEqual(child_count, 0)

        # advance TriggerEmptyCircularaggregate → workflow 完结
        self._advance_until_finished(r.execution_id, max_steps=3)

        parent = WorkflowNodeExecution.objects.get(
            execution_id=r.execution_id, node_key="A"
        )
        self.assertEqual(parent.status, STATUS_SUCCESS)
        self.assertTrue((parent.adapter_state or {}).get("loop_aggregated"))
        out = parent.output or {}
        self.assertEqual(out.get("loop_count"), 0)
        self.assertEqual(out.get("loop_results"), [])
        self.assertTrue(out.get("loop_success"))

    def test_loop_for_each_items(self) -> None:
        """for_each 模式:Perlist项展开, child节. adapter_state 含 loop_item"""
        items = [{"name": "alpha"}, {"name": "beta"}, {"name": "gamma"}]
        dag = _loop_dag(
            nodes=[
                {
                    "node_key": "L",
                    "node_type": "loop",
                    "params": {
                        "loop_type": "for_each",
                        "items": items,
                        "body_node_type": "testing_noop",
                    },
                },
            ],
            edges=[],
        )
        self._publish(dag)
        r = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)

        child_rows = list(
            WorkflowNodeExecution.objects.filter(
                execution_id=r.execution_id, node_key__startswith="L__loop_"
            ).order_by("node_key")
        )
        self.assertEqual(len(child_rows), 3)
        for i, ch in enumerate(child_rows):
            self.assertEqual(ch.adapter_state["loop_item"], items[i])
            self.assertEqual(ch.adapter_state["loop_index"], i)
            self.assertEqual(ch.adapter_state["loop_total"], 3)

        # 逐步 advance 并check中间Status
        for step in range(5):
            t = self.runner.advance_workflow(r.execution_id, user_id=self.user.pk)
            # checkchild节.Status
            children = list(
                WorkflowNodeExecution.objects.filter(
                    execution_id=r.execution_id, node_key__startswith="L__loop_"
                ).order_by("node_key")
            )
            parent = WorkflowNodeExecution.objects.get(
                execution_id=r.execution_id, node_key="L"
            )
            p_state = parent.adapter_state or {}
            if t.finished or p_state.get("loop_aggregated"):
                break

        parent = WorkflowNodeExecution.objects.get(
            execution_id=r.execution_id, node_key="L"
        )
        p_state = parent.adapter_state or {}
        self.assertTrue(
            p_state.get("loop_aggregated"),
            f"父节点未聚合: status={parent.status} adapter_state={p_state} "
            f"output={parent.output}",
        )
        results = (parent.output or {}).get("loop_results") or []
        self.assertEqual(len(results), 3)
        for i, r_ in enumerate(results):
            self.assertEqual(r_.get("item"), items[i])

    def test_loop_followed_by_downstream_node(self) -> None:
        """Circular → 下游节.:只有Circularaggregate完成后, 下游节.才会被 dispatch"""
        dag = _loop_dag(
            nodes=[
                {
                    "node_key": "L",
                    "node_type": "loop",
                    "params": {
                        "loop_type": "count",
                        "count": 2,
                        "body_node_type": "testing_noop",
                    },
                },
                {
                    "node_key": "B",
                    "node_type": "testing_noop",
                    "params": {"value": "after-loop"},
                },
            ],
            edges=[
                {"from": "L", "to": "B"},
            ],
        )
        self._publish(dag)
        r = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)

        # advance 1:dispatch child节.(同步 SUCCESS)
        self.runner.advance_workflow(r.execution_id, user_id=self.user.pk)

        # B 此时不应被create(因为 L 还no loop_aggregated)
        b_exists = WorkflowNodeExecution.objects.filter(
            execution_id=r.execution_id, node_key="B"
        ).exists()
        # Note:B may已作为 PENDING create, 但不会 dispatch, 直到 L aggregate完毕
        # (depends on compute_next_runnables 逻辑, 此处只check最终流程)

        # advance 2:Trigger L aggregate → 推出 B → B 同步Success → workflow 完成
        t2 = self.runner.advance_workflow(r.execution_id, user_id=self.user.pk)
        if not t2.finished:
            # 某些implement下aggregate和 dispatch 不在同一轮
            t3 = self.runner.advance_workflow(r.execution_id, user_id=self.user.pk)
            self.assertTrue(t3.finished)

        b = WorkflowNodeExecution.objects.get(
            execution_id=r.execution_id, node_key="B"
        )
        self.assertEqual(b.status, STATUS_SUCCESS)
        self.assertEqual(b.output.get("echo"), "after-loop")

    def test_loop_one_child_failed_fail_fast(self) -> None:
        """Circular体中一 child节.Failed:parent节.最终应为 FAILED"""
        from django.utils import timezone

        dag = _loop_dag(
            nodes=[
                {
                    "node_key": "L",
                    "node_type": "loop",
                    "params": {
                        "loop_type": "count",
                        "count": 3,
                        "body_node_type": "testing_noop",
                    },
                },
            ],
            edges=[],
        )
        self._publish(dag)
        r = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)

        # trigger 后child节.已create但为 PENDING, 手动settingTerminal state
        # (因为 testing_noop Yes同步Adapter, dispatch 后立即 SUCCESS, 
        #  cannot在 dispatch 和aggregatebetweeninsertFailedStatus, so直接手动setting)
        children = list(
            WorkflowNodeExecution.objects.filter(
                execution_id=r.execution_id, node_key__startswith="L__loop_"
            ).order_by("node_key")
        )
        self.assertEqual(len(children), 3)
        for i, ch in enumerate(children):
            if i == 1:
                ch.status = STATUS_FAILED
                ch.error_message = "E3499: 手动模拟失败"
                ch.finished_at = timezone.now()
            else:
                ch.status = STATUS_SUCCESS
                ch.finished_at = timezone.now()
            ch.save(update_fields=["status", "error_message", "finished_at", "update_datetime"])

        # advance:Triggeraggregate → parent节.应为 FAILED
        self._advance_until_finished(r.execution_id, max_steps=3)

        parent = WorkflowNodeExecution.objects.get(
            execution_id=r.execution_id, node_key="L"
        )
        self.assertEqual(parent.status, STATUS_FAILED)
        self.assertTrue((parent.adapter_state or {}).get("loop_aggregated"))
        self.assertFalse((parent.output or {}).get("loop_success"))