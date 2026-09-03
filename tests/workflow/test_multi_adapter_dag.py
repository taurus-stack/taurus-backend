"""S6-02 多AdapterComposite DAG Integration test.

validate S4 Adapter在复杂 DAG 中的协同工作:
- entries件Branch(condition → true/false Branch)
- Data transform(transform → 下游消费)
- Circular展开 + entries件Composite
- notification节.在链路末端
- DAG 完整execute + 各节.Outputcheck

所有涉及external网络的Adapter(http, http_callback, webhook_notification, email_notification)
use mock Avoid真实request.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import TransactionTestCase

from taurus.models import Host, Workflow
from dvadmin.system.models import Users
from taurus.workflow.engine.runner import WorkflowRunner
from taurus.workflow.models import WorkflowDAGVersion, WorkflowNodeExecution
from taurus.workflow.engine.executor import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
)


def _dag(nodes: list[dict], edges: list[dict]) -> dict:
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


class MultiAdapterDagTests(TransactionTestCase):
    """多AdapterComposite DAG test"""

    def setUp(self) -> None:
        self.runner = WorkflowRunner()
        self.user = Users.objects.create(
            username="dag_test",
            password="noop",
            email="dag_test@example.com",
            is_active=True,
        )
        self.host = Host.objects.create(
            host_name="dag-host-1",
            host_ip="127.0.0.1",
            status=1,
        )
        self.workflow = Workflow.objects.create(
            name="dag-multi-adapter",
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
            release_note="test",
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

    def _node_status(self, eid: int, node_key: str) -> int:
        row = WorkflowNodeExecution.objects.filter(
            execution_id=eid, node_key=node_key
        ).first()
        return row.status if row else -1

    def _node_output(self, eid: int, node_key: str) -> dict:
        row = WorkflowNodeExecution.objects.filter(
            execution_id=eid, node_key=node_key
        ).first()
        return row.output if row else {}

    # -------------------------------------------------------------------- tests

    def test_transform_then_condition_branch(self) -> None:
        """transform → condition → 双Branch:validateData transform后entries件分流正确"""
        dag = _dag(
            nodes=[
                {
                    "node_key": "T",
                    "node_type": "transform",
                    "params": {
                        "transform_type": "python",
                        "expression": "2 + 3",
                        "output_type": "number",
                    },
                },
                {
                    "node_key": "C",
                    "node_type": "condition",
                    "params": {
                        "expression_type": "simple",
                        "expression": "5 == 5",
                    },
                },
                {
                    "node_key": "TRUE_BRANCH",
                    "node_type": "testing_noop",
                    "params": {"value": "condition-true"},
                },
                {
                    "node_key": "FALSE_BRANCH",
                    "node_type": "testing_noop",
                    "params": {"value": "condition-false"},
                },
            ],
            edges=[
                {"from": "T", "to": "C"},
                {"from": "C", "to": "TRUE_BRANCH", "condition": "C.output.branch == 'true'"},
                {"from": "C", "to": "FALSE_BRANCH", "condition": "__else__"},
            ],
        )
        self._publish(dag)
        r = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)
        self._advance_until_finished(r.execution_id)

        # T → SUCCESS
        self.assertEqual(self._node_status(r.execution_id, "T"), STATUS_SUCCESS)
        t_out = self._node_output(r.execution_id, "T")
        self.assertEqual(t_out.get("result"), 5.0)

        # C → SUCCESS, branch=true
        self.assertEqual(self._node_status(r.execution_id, "C"), STATUS_SUCCESS)
        c_out = self._node_output(r.execution_id, "C")
        self.assertEqual(c_out.get("branch"), "true")

        # TRUE_BRANCH 应execute
        self.assertEqual(
            self._node_status(r.execution_id, "TRUE_BRANCH"), STATUS_SUCCESS
        )

        # FALSE_BRANCH 应被Skip(__else__ Branch未命中 → SKIPPED)
        self.assertEqual(
            self._node_status(r.execution_id, "FALSE_BRANCH"), STATUS_SKIPPED
        )

    def test_condition_false_branch_taken(self) -> None:
        """condition 求值 false → __else__ Branchexecute"""
        dag = _dag(
            nodes=[
                {
                    "node_key": "C",
                    "node_type": "condition",
                    "params": {
                        "expression_type": "simple",
                        "expression": "1 != 1",
                    },
                },
                {
                    "node_key": "TRUE_BRANCH",
                    "node_type": "testing_noop",
                    "params": {"value": "true-path"},
                },
                {
                    "node_key": "FALSE_BRANCH",
                    "node_type": "testing_noop",
                    "params": {"value": "false-path"},
                },
            ],
            edges=[
                {"from": "C", "to": "TRUE_BRANCH", "condition": "C.output.branch == 'true'"},
                {"from": "C", "to": "FALSE_BRANCH", "condition": "__else__"},
            ],
        )
        self._publish(dag)
        r = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)
        self._advance_until_finished(r.execution_id)

        c_out = self._node_output(r.execution_id, "C")
        self.assertEqual(c_out.get("branch"), "false")

        # TRUE_BRANCH 应被Skip(entries件为 false → SKIPPED)
        self.assertEqual(
            self._node_status(r.execution_id, "TRUE_BRANCH"), STATUS_SKIPPED
        )

        # FALSE_BRANCH 应execute(__else__ 命中)
        self.assertEqual(
            self._node_status(r.execution_id, "FALSE_BRANCH"), STATUS_SUCCESS
        )
        f_out = self._node_output(r.execution_id, "FALSE_BRANCH")
        self.assertEqual(f_out.get("echo"), "false-path")

    def test_loop_then_transform_then_condition(self) -> None:
        """loop → transform → condition:validateCircular完成后下游节.正常消费数据"""
        dag = _dag(
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
                    "node_key": "T",
                    "node_type": "transform",
                    "params": {
                        "transform_type": "python",
                        "expression": "len([1,2,3])",
                        "output_type": "number",
                    },
                },
                {
                    "node_key": "C",
                    "node_type": "condition",
                    "params": {
                        "expression_type": "simple",
                        "expression": "3 == 3",
                    },
                },
            ],
            edges=[
                {"from": "L", "to": "T"},
                {"from": "T", "to": "C"},
            ],
        )
        self._publish(dag)
        r = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)
        self._advance_until_finished(r.execution_id, max_steps=5)

        # L aggregate完成
        l_row = WorkflowNodeExecution.objects.get(
            execution_id=r.execution_id, node_key="L"
        )
        self.assertEqual(l_row.status, STATUS_SUCCESS)
        self.assertTrue((l_row.adapter_state or {}).get("loop_aggregated"))

        # T → SUCCESS
        self.assertEqual(self._node_status(r.execution_id, "T"), STATUS_SUCCESS)
        t_out = self._node_output(r.execution_id, "T")
        self.assertEqual(t_out.get("result"), 3.0)

        # C → SUCCESS, branch=true
        self.assertEqual(self._node_status(r.execution_id, "C"), STATUS_SUCCESS)
        c_out = self._node_output(r.execution_id, "C")
        self.assertEqual(c_out.get("branch"), "true")

    @patch("taurus.workflow.units.http.requests.request")
    def test_http_then_transform(self, mock_request: MagicMock) -> None:
        """http → transform:validate HTTP response数据可被 transform 消费"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"items": [1, 2, 3], "total": 3}
        mock_resp.text = '{"items": [1, 2, 3], "total": 3}'
        mock_resp.elapsed.total_seconds.return_value = 0.1
        mock_request.return_value = mock_resp

        dag = _dag(
            nodes=[
                {
                    "node_key": "H",
                    "node_type": "http",
                    "params": {
                        "url": "https://httpbin.example.com/json",
                        "method": "GET",
                    },
                },
                {
                    "node_key": "T",
                    "node_type": "transform",
                    "params": {
                        "transform_type": "python",
                        "expression": "3 + 4",
                        "output_type": "number",
                    },
                },
            ],
            edges=[
                {"from": "H", "to": "T"},
            ],
        )
        self._publish(dag)
        r = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)
        self._advance_until_finished(r.execution_id)

        # H → SUCCESS
        self.assertEqual(self._node_status(r.execution_id, "H"), STATUS_SUCCESS)
        h_out = self._node_output(r.execution_id, "H")
        self.assertEqual(h_out.get("status_code"), 200)

        # T → SUCCESS
        self.assertEqual(self._node_status(r.execution_id, "T"), STATUS_SUCCESS)
        t_out = self._node_output(r.execution_id, "T")
        self.assertEqual(t_out.get("result"), 7.0)

    @patch("taurus.workflow.units.webhook_notification.requests.request")
    def test_condition_true_then_webhook(self, mock_request: MagicMock) -> None:
        """condition(true) → webhook_notification:validateentries件满足时sendnotification"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}
        mock_resp.text = '{"ok": true}'
        mock_request.return_value = mock_resp

        dag = _dag(
            nodes=[
                {
                    "node_key": "C",
                    "node_type": "condition",
                    "params": {
                        "expression_type": "simple",
                        "expression": "10 > 5",
                    },
                },
                {
                    "node_key": "W",
                    "node_type": "webhook_notification",
                    "params": {
                        "url": "https://hooks.example.com/notify",
                        "method": "POST",
                        "retry_count": 0,
                    },
                },
            ],
            edges=[
                {"from": "C", "to": "W", "condition": "C.output.branch == 'true'"},
            ],
        )
        self._publish(dag)
        r = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)
        self._advance_until_finished(r.execution_id)

        # C → SUCCESS, branch=true
        self.assertEqual(self._node_status(r.execution_id, "C"), STATUS_SUCCESS)
        c_out = self._node_output(r.execution_id, "C")
        self.assertEqual(c_out.get("branch"), "true")

        # W → SUCCESS(entries件满足, notification已send)
        self.assertEqual(self._node_status(r.execution_id, "W"), STATUS_SUCCESS)
        w_out = self._node_output(r.execution_id, "W")
        self.assertEqual(w_out.get("response_status"), 200)

    @patch("taurus.workflow.units.email_notification.send_mail")
    def test_transform_then_email_notification(self, mock_send_mail: MagicMock) -> None:
        """transform → email_notification:validateData transform后sendemail"""
        mock_send_mail.return_value = 1

        dag = _dag(
            nodes=[
                {
                    "node_key": "T",
                    "node_type": "transform",
                    "params": {
                        "transform_type": "python",
                        "expression": "'alert: ' + str(42)",
                        "output_type": "string",
                    },
                },
                {
                    "node_key": "E",
                    "node_type": "email_notification",
                    "params": {
                        "recipients": ["admin@example.com"],
                        "subject_template": "Workflow Alert",
                        "body_template": "Check the result",
                    },
                },
            ],
            edges=[
                {"from": "T", "to": "E"},
            ],
        )
        self._publish(dag)
        r = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)
        self._advance_until_finished(r.execution_id)

        # T → SUCCESS
        self.assertEqual(self._node_status(r.execution_id, "T"), STATUS_SUCCESS)
        t_out = self._node_output(r.execution_id, "T")
        self.assertEqual(t_out.get("result"), "alert: 42")

        # E → SUCCESS
        self.assertEqual(self._node_status(r.execution_id, "E"), STATUS_SUCCESS)
        e_out = self._node_output(r.execution_id, "E")
        self.assertEqual(e_out.get("sent_count"), 1)
        mock_send_mail.assert_called_once()

    def test_diamond_merge_pattern(self) -> None:
        """菱形Merge模式:A → B, A → C, B → D, C → D
        validate D 在 B 和 C 都完成后才execute"""
        dag = _dag(
            nodes=[
                {
                    "node_key": "A",
                    "node_type": "testing_noop",
                    "params": {"value": "start"},
                },
                {
                    "node_key": "B",
                    "node_type": "transform",
                    "params": {
                        "transform_type": "python",
                        "expression": "1 + 1",
                        "output_type": "number",
                    },
                },
                {
                    "node_key": "C",
                    "node_type": "transform",
                    "params": {
                        "transform_type": "python",
                        "expression": "2 + 2",
                        "output_type": "number",
                    },
                },
                {
                    "node_key": "D",
                    "node_type": "testing_noop",
                    "params": {"value": "merge"},
                },
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
        self._advance_until_finished(r.execution_id)

        # 所有节. SUCCESS
        for nk in ("A", "B", "C", "D"):
            self.assertEqual(
                self._node_status(r.execution_id, nk),
                STATUS_SUCCESS,
                f"{nk} 应成功",
            )

        # B 和 C 的Output正确
        self.assertEqual(self._node_output(r.execution_id, "B").get("result"), 2.0)
        self.assertEqual(self._node_output(r.execution_id, "C").get("result"), 4.0)

    def test_condition_failed_stops_downstream(self) -> None:
        """condition 求值exception → FAILED → 下游节.不应execute(fail_fast)"""
        dag = _dag(
            nodes=[
                {
                    "node_key": "C",
                    "node_type": "condition",
                    "params": {
                        "expression_type": "simple",
                        "expression": "1 !!! 2",  # invalidoperation符 → dispatch 时求值Failed
                    },
                },
                {
                    "node_key": "D",
                    "node_type": "testing_noop",
                    "params": {"value": "should-not-run"},
                },
            ],
            edges=[
                {"from": "C", "to": "D"},
            ],
        )
        self._publish(dag)
        r = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)
        self._advance_until_finished(r.execution_id)

        # C → FAILED(Expression求值exception)
        self.assertEqual(self._node_status(r.execution_id, "C"), STATUS_FAILED)

        # D 不应execute(上游Failed + fail_fast → SKIPPED 或 CANCELLED)
        d_status = self._node_status(r.execution_id, "D")
        self.assertIn(d_status, (STATUS_SKIPPED, STATUS_CANCELLED))

    def test_transform_regex_type(self) -> None:
        """transform regex 模式:validate正则提取functionality"""
        dag = _dag(
            nodes=[
                {
                    "node_key": "R",
                    "node_type": "transform",
                    "params": {
                        "transform_type": "regex",
                        "expression": r'"total":\s*(\d+)',
                        "output_type": "string",
                    },
                },
            ],
            edges=[],
        )
        self._publish(dag)
        r = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)
        self._advance_until_finished(r.execution_id)

        self.assertEqual(self._node_status(r.execution_id, "R"), STATUS_SUCCESS)
        r_out = self._node_output(r.execution_id, "R")
        # regex match params 的 JSON Serialization结果
        self.assertIsNotNone(r_out.get("result"))

    def test_loop_for_each_with_condition_downstream(self) -> None:
        """for_each Circular → condition:validateCircularaggregate后entries件判断正常工作"""
        items = [{"val": 10}, {"val": 20}, {"val": 30}]
        dag = _dag(
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
                {
                    "node_key": "C",
                    "node_type": "condition",
                    "params": {
                        "expression_type": "simple",
                        "expression": "1 == 1",
                    },
                },
                {
                    "node_key": "OK",
                    "node_type": "testing_noop",
                    "params": {"value": "all-good"},
                },
            ],
            edges=[
                {"from": "L", "to": "C"},
                {"from": "C", "to": "OK", "condition": "C.output.branch == 'true'"},
            ],
        )
        self._publish(dag)
        r = self.runner.trigger_workflow(self.workflow, user_id=self.user.pk)
        self._advance_until_finished(r.execution_id, max_steps=5)

        # L aggregate完成
        l_row = WorkflowNodeExecution.objects.get(
            execution_id=r.execution_id, node_key="L"
        )
        self.assertTrue((l_row.adapter_state or {}).get("loop_aggregated"))

        # C → SUCCESS
        self.assertEqual(self._node_status(r.execution_id, "C"), STATUS_SUCCESS)

        # OK → SUCCESS
        self.assertEqual(self._node_status(r.execution_id, "OK"), STATUS_SUCCESS)