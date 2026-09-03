"""S1-05 WorkflowRunner Integration test:trigger → advance → advance → finished.

走真实 Django ORM(TransactionTestCase 自动建/删testlibrary).
Adapter用内置同步 testing_noop / testing_fail, dispatch 直接Terminal state, 
不require真实异步 poll.
"""
from __future__ import annotations

from django.test import TransactionTestCase

from taurus.models import Host, Workflow
from dvadmin.system.models import Users
from taurus.workflow.engine.runner import AdvanceTick, WorkflowRunner
from taurus.workflow.models import WorkflowDAGVersion, WorkflowNodeExecution
from taurus.workflow.engine.executor import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
)
from taurus.workflow.exceptions import WorkflowRunnerError


def _simple_dag(nodes: list[dict], edges: list[dict]) -> dict:
    """nodes=[{node_key,node_name,node_type,params,host_ids?}, ...]
    edges=[{from,to,condition?}, ...]
    """
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


class WorkflowRunnerSyncNoopTests(TransactionTestCase):
    """testing_noop 同步Adapter:dispatch 直接return SUCCESS."""

    def setUp(self) -> None:
        self.runner = WorkflowRunner()
        # Users / Host 都只Yes为了让foreign key + M2M 不为Empty, 实际Adapter requires_host=False
        self.user = Users.objects.create(
            username="wf_runner_test",
            password="noop",  # 实际不走认证
            email="wf_runner_test@example.com",
            is_active=True,
        )
        self.host = Host.objects.create(
            host_name="runner-host-1",
            host_ip="127.0.0.1",
            status=1,
        )
        self.workflow = Workflow.objects.create(
            name="wf-runner-simple",
            workflow_mode="dag",
            status=1,
            global_envs={"greeting": "hello"},
        )
        self.workflow.hosts.add(self.host)  # 满足 M2M

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
    def test_trigger_without_published_version_raises(self) -> None:
        with self.assertRaises(WorkflowRunnerError):
            self.runner.trigger_workflow(
                self.workflow, user_id=self.user.pk,
            )

    def test_linear_2node_noop(self) -> None:
        # A → B, A Output echo=v1, B Reference ${nodes.A.output.echo}
        dag = _simple_dag(
            nodes=[
                {
                    "node_key": "A",
                    "node_name": "step A",
                    "node_type": "testing_noop",
                    "params": {"value": "v1"},
                },
                {
                    "node_key": "B",
                    "node_name": "step B",
                    "node_type": "testing_noop",
                    "params": {"copy": "${nodes.A.output.echo}"},
                },
            ],
            edges=[
                {"from": "A", "to": "B"},
            ],
        )
        ver = self._publish(dag)
        r = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)
        self.assertEqual(r.dag_version_id, ver.pk)
        self.assertEqual(r.initial_runnables, ["A"])
        # testing_noop dispatch 直接 SUCCESS(同步), trigger 后 A Completed
        rows = list(
            WorkflowNodeExecution.objects.filter(execution_id=r.execution_id)
            .order_by("node_key")
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].node_key, "A")
        self.assertEqual(rows[0].status, STATUS_SUCCESS)
        self.assertEqual(rows[0].output.get("echo"), "v1")
        self.assertIsNotNone(rows[0].finished_at)
        self.assertIsNotNone(rows[0].duration_ms)

        # Page 1 次 advance:should推出 B
        t1 = self.runner.advance_workflow(r.execution_id, user_id=self.user.pk)
        self.assertIsInstance(t1, AdvanceTick)
        self.assertEqual(t1.polled, 0)  # no RUNNING
        self.assertEqual(t1.newly_dispatched, 1)
        # B 同步Success
        b = WorkflowNodeExecution.objects.get(execution_id=r.execution_id, node_key="B")
        self.assertEqual(b.status, STATUS_SUCCESS)
        self.assertEqual(b.output.get("echo"), "v1")  # params.copy 被Render成 v1
        self.assertIsNotNone(b.finished_at)

        # Page 2 次 advance:no更多可推, workflow Terminal state
        t2 = self.runner.advance_workflow(r.execution_id, user_id=self.user.pk)
        self.assertTrue(t2.finished)
        self.assertEqual(t2.newly_dispatched, 0)
        self.assertEqual(t2.newly_completed, 0)
        self.assertEqual(t2.polled, 0)

        # 再次 advance Idempotency, finished 依旧 True
        t3 = self.runner.advance_workflow(r.execution_id, user_id=self.user.pk)
        self.assertTrue(t3.finished)

    def test_diamond_all_success(self) -> None:
        #  A → B → D
        #  └→ C ─┘
        dag = _simple_dag(
            nodes=[
                {"node_key": "A", "node_type": "testing_noop", "params": {"value": "a"}},
                {"node_key": "B", "node_type": "testing_noop", "params": {"value": "b"}},
                {"node_key": "C", "node_type": "testing_noop", "params": {"value": "c"}},
                {"node_key": "D", "node_type": "testing_noop", "params": {"value": "d"}},
            ],
            edges=[
                {"from": "A", "to": "B"},
                {"from": "A", "to": "C"},
                {"from": "B", "to": "D"},
                {"from": "C", "to": "D"},
            ],
        )
        self._publish(dag)
        r = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)
        t1 = self.runner.advance_workflow(r.execution_id, user_id=self.user.pk)
        # B, C simultaneously推出(因 A 已在 trigger 里同步完成)
        self.assertEqual(t1.newly_dispatched, 2)
        t2 = self.runner.advance_workflow(r.execution_id, user_id=self.user.pk)
        self.assertEqual(t2.newly_dispatched, 1)  # D
        t3 = self.runner.advance_workflow(r.execution_id, user_id=self.user.pk)
        self.assertTrue(t3.finished)
        # Terminal statecheck
        rows = {
            x.node_key: x.status
            for x in WorkflowNodeExecution.objects.filter(execution_id=r.execution_id)
        }
        self.assertEqual(rows, {
            "A": STATUS_SUCCESS, "B": STATUS_SUCCESS,
            "C": STATUS_SUCCESS, "D": STATUS_SUCCESS,
        })

    def test_else_branch_skips(self) -> None:
        # A →(success)→ B; A →(__else__)→ C
        dag = _simple_dag(
            nodes=[
                {"node_key": "A", "node_type": "testing_noop", "params": {"value": "a"}},
                {"node_key": "B", "node_type": "testing_noop", "params": {"value": "b"}},
                {"node_key": "C", "node_type": "testing_noop", "params": {"value": "c"}},
            ],
            edges=[
                {"from": "A", "to": "B", "condition": "success(A)"},
                {"from": "A", "to": "C", "condition": "__else__"},
            ],
        )
        self._publish(dag)
        r = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)
        # advance 多次直到 finished
        for _ in range(5):
            t = self.runner.advance_workflow(r.execution_id, user_id=self.user.pk)
            if t.finished:
                break
        rows = {
            x.node_key: (x.status, x.error_message)
            for x in WorkflowNodeExecution.objects.filter(execution_id=r.execution_id)
        }
        self.assertEqual(rows["A"][0], STATUS_SUCCESS)
        self.assertEqual(rows["B"][0], STATUS_SUCCESS)
        # C 未命中entries件, 被补 SKIPPED 行
        self.assertEqual(rows["C"][0], STATUS_SKIPPED)

    def test_end_node_marks_success_after_upstream_complete(self) -> None:
        dag = _simple_dag(
            nodes=[
                {"node_key": "start", "node_type": "start"},
                {"node_key": "webhook", "node_type": "testing_noop", "params": {"value": "ok"}},
                {"node_key": "end", "node_type": "end"},
            ],
            edges=[
                {"from": "start", "to": "webhook"},
                {"from": "webhook", "to": "end"},
            ],
        )
        ver = self._publish(dag)
        r = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)

        rows = {
            x.node_key: x.status
            for x in WorkflowNodeExecution.objects.filter(execution_id=r.execution_id)
        }
        self.assertEqual(rows["start"], STATUS_SUCCESS)

        t1 = self.runner.advance_workflow(r.execution_id, user_id=self.user.pk)
        self.assertTrue(t1.finished)
        rows2 = {
            x.node_key: (x.status, x.duration_ms)
            for x in WorkflowNodeExecution.objects.filter(execution_id=r.execution_id)
        }
        self.assertEqual(rows2["end"][0], STATUS_SUCCESS)
        self.assertIsNotNone(rows2["end"][1])

    def test_end_node_timestamp_after_sync_dispatch_delay(self) -> None:
        """回归:同步节. dispatch 耗时后, end 节. started_at must在 upstream after."""
        import time as _time
        from unittest.mock import patch

        dag = _simple_dag(
            nodes=[
                {"node_key": "start", "node_type": "start"},
                {"node_key": "webhook", "node_type": "testing_noop", "params": {"value": "ok"}},
                {"node_key": "end", "node_type": "end"},
            ],
            edges=[
                {"from": "start", "to": "webhook"},
                {"from": "webhook", "to": "end"},
            ],
        )
        ver = self._publish(dag)

        original_dispatch = WorkflowRunner._dispatch_pending

        def delayed_dispatch(self_inner, execution, dag_ver, candidate_rows, ctx, *, user_id=None):
            _time.sleep(0.05)
            return original_dispatch(self_inner, execution, dag_ver, candidate_rows, ctx, user_id=user_id)

        with patch.object(WorkflowRunner, '_dispatch_pending', delayed_dispatch):
            r = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)

        end_row = WorkflowNodeExecution.objects.get(execution_id=r.execution_id, node_key="end")
        webhook_row = WorkflowNodeExecution.objects.get(execution_id=r.execution_id, node_key="webhook")

        self.assertEqual(end_row.status, STATUS_SUCCESS)
        self.assertGreaterEqual(end_row.started_at, webhook_row.finished_at)


class WorkflowRunnerSyncFailTests(TransactionTestCase):
    """testing_fail:dispatch 直接 FAILED, validate fail_fast Branch."""

    def setUp(self) -> None:
        self.runner = WorkflowRunner()
        self.user = Users.objects.create(
            username="wf_fail_test",
            password="nope",
            email="wf_fail_test@example.com",
            is_active=True,
        )
        self.host = Host.objects.create(
            host_name="runner-host-2", host_ip="127.0.0.2", status=1,
        )
        self.workflow = Workflow.objects.create(
            name="wf-runner-fail",
            workflow_mode="dag",
            status=1,
        )
        self.workflow.hosts.add(self.host)

    def _publish(self, dag_def: dict) -> None:
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

    def test_fail_fast_cancels_downstream(self) -> None:
        # A(fail) → B → C:fail_strategy=fail_fast, B 和 C should CANCELLED
        dag = {
            "nodes": [
                {
                    "node_key": "A", "node_name": "A",
                    "node_type": "testing_fail",
                    "params": {"error_code": "E3499", "message": "boom"},
                    "host_ids": None, "timeout_sec": 0,
                    "fail_strategy": "fail_fast",
                },
                {
                    "node_key": "B", "node_name": "B",
                    "node_type": "testing_noop",
                    "params": {"value": "b"},
                    "host_ids": None, "timeout_sec": 0,
                    "fail_strategy": "fail_fast",
                },
                {
                    "node_key": "C", "node_name": "C",
                    "node_type": "testing_noop",
                    "params": {"value": "c"},
                    "host_ids": None, "timeout_sec": 0,
                    "fail_strategy": "fail_fast",
                },
            ],
            "edges": [
                {"from": "A", "to": "B", "condition": "success(A)"},
                {"from": "B", "to": "C", "condition": "success(B)"},
            ],
        }
        self._publish(dag)
        r = self.runner.trigger_workflow(
            self.workflow, user_id=self.user.pk,
            fail_strategy="fail_fast",
        )
        # trigger internal直接 dispatch A;A 同步 FAILED
        a = WorkflowNodeExecution.objects.get(execution_id=r.execution_id, node_key="A")
        self.assertEqual(a.status, STATUS_FAILED)
        self.assertIn("E3499", a.error_message or "")
        # advance:fail_fast 命中, B/C 被补 CANCELLED/SKIPPED, workflow 结束
        for _ in range(3):
            t = self.runner.advance_workflow(r.execution_id, user_id=self.user.pk)
            if t.finished:
                break
        self.assertTrue(t.finished)
        rows = {
            x.node_key: x.status
            for x in WorkflowNodeExecution.objects.filter(execution_id=r.execution_id)
        }
        self.assertEqual(rows["A"], STATUS_FAILED)
        # B 和 C 都未actually dispatch:被 advance 里的 ineligible_nodes 补 SKIPPED
        # (fail_fast 只Cancel PENDING/RUNNING, 而 B/C 压root没create行 → 会被 SKIPPED 补齐)
        self.assertIn(rows.get("B"), {STATUS_SKIPPED, STATUS_CANCELLED})
        self.assertIn(rows.get("C"), {STATUS_SKIPPED, STATUS_CANCELLED})


class WorkflowRunnerCancelTests(TransactionTestCase):
    def setUp(self) -> None:
        self.runner = WorkflowRunner()
        self.user = Users.objects.create(
            username="wf_cancel_test",
            password="nope",
            email="wf_cancel_test@example.com",
            is_active=True,
        )
        self.host = Host.objects.create(host_name="h3", host_ip="127.0.0.3", status=1)
        self.workflow = Workflow.objects.create(
            name="wf-cancel", workflow_mode="dag", status=1,
        )
        self.workflow.hosts.add(self.host)

    def test_cancel_after_trigger(self) -> None:
        dag = _simple_dag(
            nodes=[
                {"node_key": "A", "node_type": "testing_noop", "params": {"value": "a"}},
                {"node_key": "B", "node_type": "testing_noop", "params": {"value": "b"}},
            ],
            edges=[{"from": "A", "to": "B"}],
        )
        ver = WorkflowDAGVersion.objects.create(
            workflow=self.workflow, version=1,
            definition=dag, global_envs={},
            release_note="cancel", creator=self.user,
        )
        self.workflow.dag_published_version = ver
        self.workflow.save(update_fields=["dag_published_version"])
        r = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)
        # Advance一次推出 B
        self.runner.advance_workflow(r.execution_id, user_id=self.user.pk)
        # Cancel
        self.runner.cancel_workflow(r.execution_id, reason="user abort")
        rows = list(WorkflowNodeExecution.objects.filter(execution_id=r.execution_id))
        # A may已Success(同步), B 也已Success;cancel 只针对 PENDING/RUNNING
        # so本场景 cancel 只改 execution.status, 不会动已 SUCCESS 的行
        # 再Trigger一次没完成的场景:用 trigger 后立刻 cancel(此时 B 还没 advance)
        r2 = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)
        self.runner.cancel_workflow(r2.execution_id, reason="early abort")
        rows2 = {
            x.node_key: x.status
            for x in WorkflowNodeExecution.objects.filter(execution_id=r2.execution_id)
        }
        # A 在 trigger 里同步 dispatch Success, cancel 只针对 PENDING/RUNNING, 
        # therefore A 保持 SUCCESS(合理:已Success的cannot"反Success").
        self.assertEqual(rows2["A"], STATUS_SUCCESS)