"""S2-06 childWorkflowAdapterUnit test."""
from __future__ import annotations

import pytest

from django.test import TransactionTestCase

from taurus.models import Host, Workflow
from dvadmin.system.models import Users
from taurus.workflow.engine.runner import WorkflowRunner
from taurus.workflow.models import WorkflowDAGVersion, WorkflowNodeExecution


def _simple_dag(nodes, edges):
    out_nodes = []
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
    out_edges = []
    for e in edges:
        out_edges.append({
            "from": e["from"],
            "to": e["to"],
            "condition": e.get("condition", "success(__from__)"),
        })
    return {"nodes": out_nodes, "edges": out_edges}


class TestSubWorkflowAdapterClassAttrs:
    """childWorkflowAdapterclassattributecheck."""

    def test_registration_and_flags(self):
        from taurus.workflow.engine.registry import get_registry

        r = get_registry()
        assert r.has("sub_workflow")
        cls = r.get_class("sub_workflow")
        assert cls.node_type == "sub_workflow"
        assert cls.display_name == "子工作流"
        assert cls.requires_host is False
        assert cls.is_asynchronous_human is False

    def test_instantiate(self):
        from taurus.workflow.engine.registry import get_registry
        adapter = get_registry().instantiate("sub_workflow")
        assert adapter is not None


class TestSubWorkflowAdapterValidateConfig:
    """staticcheck逻辑."""

    @pytest.fixture()
    def adapter(self):
        from taurus.workflow.engine.registry import get_registry
        return get_registry().instantiate("sub_workflow")

    def test_validate_requires_sub_workflow_id(self, adapter):
        r = adapter.validate_config({})
        assert r.ok is False
        assert "/params/sub_workflow_id" in (r.errors or {})
        assert "E0601" in str(r.errors)

    def test_validate_accepts_minimal_config(self, adapter):
        r = adapter.validate_config({"sub_workflow_id": 1})
        assert r.ok is True

    def test_validate_rejects_bad_trigger_params_type(self, adapter):
        r = adapter.validate_config({
            "sub_workflow_id": 1,
            "trigger_params": [1, 2, 3],
        })
        assert r.ok is False
        assert "/params/trigger_params" in (r.errors or {})

    def test_validate_rejects_bad_fail_strategy(self, adapter):
        r = adapter.validate_config({
            "sub_workflow_id": 1,
            "fail_strategy": "unknown",
        })
        assert r.ok is False
        assert "/params/fail_strategy" in (r.errors or {})

    def test_validate_accepts_full_config(self, adapter):
        r = adapter.validate_config({
            "sub_workflow_id": 1,
            "trigger_params": {"k": "v"},
            "fail_strategy": "continue",
        })
        assert r.ok is True


class TestSubWorkflowAdapterRender:
    """InterpolateRender."""

    @pytest.fixture()
    def adapter(self):
        from taurus.workflow.engine.registry import get_registry
        return get_registry().instantiate("sub_workflow")

    def test_render_interpolates_sub_workflow_id(self, adapter):
        from taurus.workflow.engine.context import WorkflowContext

        ctx = WorkflowContext(
            workflow_env={"SUB_ID": "42", "FOO": "bar"},
        )
        params = {
            "sub_workflow_id": "${workflow.env.SUB_ID}",
            "trigger_params": {"foo": "${workflow.env.FOO}"},
        }
        rp = adapter.validate_and_render(params, ctx)
        assert rp["sub_workflow_id"] == "42"
        assert rp["trigger_params"]["foo"] == "bar"


# ============ Integration test:require真实 Workflow + DAG + Runner ============

class SubWorkflowIntegrationTests(TransactionTestCase):
    """childWorkflow dispatch → poll → cancel Integration test."""

    def setUp(self):
        self.runner = WorkflowRunner()
        self.user = Users.objects.create(
            username="sub_wf_test_user",
            password="noop",
            email="sub_wf@example.com",
            is_active=True,
        )
        self.host = Host.objects.create(
            host_name="sub-wf-host",
            host_ip="127.0.0.2",
            status=1,
        )

        # childWorkflow:单节. testing_noop
        self.sub_workflow = Workflow.objects.create(
            name="sub-workflow-1",
            workflow_mode="dag",
            status=1,
            global_envs={"sub_greeting": "hi"},
        )
        self.sub_workflow.hosts.add(self.host)
        sub_dag = _simple_dag(
            nodes=[{
                "node_key": "S1",
                "node_name": "子工作流节点 S1",
                "node_type": "testing_noop",
                "params": {"value": "sub-result"},
            }],
            edges=[],
        )
        sub_ver = WorkflowDAGVersion.objects.create(
            workflow=self.sub_workflow,
            version=1,
            definition=sub_dag,
            global_envs={},
            release_note="sub wf test",
            creator=self.user,
        )
        self.sub_workflow.dag_published_version = sub_ver
        self.sub_workflow.save(update_fields=["dag_published_version", "update_datetime"])

        # parentWorkflow:单节. sub_workflow
        self.parent_workflow = Workflow.objects.create(
            name="parent-workflow-1",
            workflow_mode="dag",
            status=1,
            global_envs={},
        )
        self.parent_workflow.hosts.add(self.host)
        parent_dag = _simple_dag(
            nodes=[{
                "node_key": "P1",
                "node_name": "父节点 P1（触发子工作流）",
                "node_type": "sub_workflow",
                "params": {"sub_workflow_id": self.sub_workflow.pk},
            }],
            edges=[],
        )
        parent_ver = WorkflowDAGVersion.objects.create(
            workflow=self.parent_workflow,
            version=1,
            definition=parent_dag,
            global_envs={},
            release_note="parent wf test",
            creator=self.user,
        )
        self.parent_workflow.dag_published_version = parent_ver
        self.parent_workflow.save(update_fields=["dag_published_version", "update_datetime"])

    def test_dispatch_creates_sub_execution_returns_running(self):
        from taurus.workflow.engine.schemas import STATUS_RUNNING, STATUS_SUCCESS
        from taurus.models import WorkflowExecution

        # TriggerparentWorkflow
        r = self.runner.trigger_workflow(self.parent_workflow, user_id=self.user.pk)
        self.assertEqual(r.initial_runnables, ["P1"])

        # P1 shouldYes RUNNING(childWorkflow已Trigger但未完成)
        p1 = WorkflowNodeExecution.objects.get(execution_id=r.execution_id, node_key="P1")
        # childWorkflowYes testing_noop 同步execute, may trigger 后就完成了
        # 但 sub_workflow Adapter dispatch 后Yes RUNNING, require advance Advance
        self.assertIn(p1.status, [STATUS_RUNNING, STATUS_SUCCESS])

        # childexecuteshould已create
        sub_exec = WorkflowExecution.objects.filter(
            workflow=self.sub_workflow,
        ).order_by("-pk").first()
        self.assertIsNotNone(sub_exec)

    def test_dispatch_without_sub_workflow_id_returns_failed(self):
        from taurus.workflow.engine.schemas import STATUS_FAILED
        from taurus.workflow.engine.runner import WorkflowRunner
        from taurus.models import Workflow
        from taurus.workflow.models import WorkflowDAGVersion

        # create一  sub_workflow_id missing的parentWorkflow
        wf = Workflow.objects.create(
            name="parent-no-sub-id",
            workflow_mode="dag",
            status=1,
        )
        wf.hosts.add(self.host)
        dag = _simple_dag(
            nodes=[{
                "node_key": "P1",
                "node_name": "P1",
                "node_type": "sub_workflow",
                "params": {},  # missing sub_workflow_id
            }],
            edges=[],
        )
        ver = WorkflowDAGVersion.objects.create(
            workflow=wf, version=1, definition=dag, global_envs={},
            release_note="test", creator=self.user,
        )
        wf.dag_published_version = ver
        wf.save(update_fields=["dag_published_version", "update_datetime"])

        r = self.runner.trigger_workflow(wf, user_id=self.user.pk)
        p1 = WorkflowNodeExecution.objects.get(execution_id=r.execution_id, node_key="P1")
        # shouldYes FAILED, Error码 E0601
        from taurus.workflow.engine.executor import STATUS_FAILED as EX_FAILED
        self.assertEqual(p1.status, EX_FAILED)

    def test_dispatch_sub_workflow_not_exists_returns_failed(self):
        from taurus.models import Workflow
        from taurus.workflow.models import WorkflowDAGVersion
        from taurus.workflow.engine.executor import STATUS_FAILED

        wf = Workflow.objects.create(
            name="parent-bad-sub",
            workflow_mode="dag",
            status=1,
        )
        wf.hosts.add(self.host)
        dag = _simple_dag(
            nodes=[{
                "node_key": "P1",
                "node_name": "P1",
                "node_type": "sub_workflow",
                "params": {"sub_workflow_id": 999999},
            }],
            edges=[],
        )
        ver = WorkflowDAGVersion.objects.create(
            workflow=wf, version=1, definition=dag, global_envs={},
            release_note="test", creator=self.user,
        )
        wf.dag_published_version = ver
        wf.save(update_fields=["dag_published_version", "update_datetime"])

        r = self.runner.trigger_workflow(wf, user_id=self.user.pk)
        p1 = WorkflowNodeExecution.objects.get(execution_id=r.execution_id, node_key="P1")
        self.assertEqual(p1.status, STATUS_FAILED)

    def test_full_flow_parent_advances_until_sub_finished(self):
        """完整流程:parentTrigger → advance → child完成 → parent P1 完成 → parentWorkflow完成."""
        from taurus.workflow.engine.executor import STATUS_SUCCESS
        from taurus.models import WorkflowExecution

        # TriggerparentWorkflow
        r = self.runner.trigger_workflow(self.parent_workflow, user_id=self.user.pk)

        # 持续Advance直到parentWorkflow完成
        max_ticks = 10
        for _ in range(max_ticks):
            tick = self.runner.advance_workflow(r.execution_id)
            if tick.finished:
                break

        # parentWorkflowshould完成
        parent_exec = WorkflowExecution.objects.get(pk=r.execution_id)
        self.assertEqual(parent_exec.status, 2)  # 2=Completed

        # P1 shouldYes SUCCESS
        p1 = WorkflowNodeExecution.objects.get(execution_id=r.execution_id, node_key="P1")
        self.assertEqual(p1.status, STATUS_SUCCESS)

        # childWorkflowexecute也should完成
        sub_exec = WorkflowExecution.objects.filter(
            workflow=self.sub_workflow,
        ).order_by("-pk").first()
        self.assertEqual(sub_exec.status, 2)

    def test_sub_outputs_mapped_to_parent(self):
        """childWorkflow完成后, 其节.Output应map到parent节.的 output.sub_outputs."""
        from taurus.workflow.engine.executor import STATUS_SUCCESS
        from taurus.models import WorkflowExecution

        r = self.runner.trigger_workflow(self.parent_workflow, user_id=self.user.pk)
        for _ in range(10):
            tick = self.runner.advance_workflow(r.execution_id)
            if tick.finished:
                break

        p1 = WorkflowNodeExecution.objects.get(execution_id=r.execution_id, node_key="P1")
        self.assertEqual(p1.status, STATUS_SUCCESS)

        # P1 的 output 应contain sub_outputs, 且其中有childWorkflow节. S1 的Output
        sub_outputs = (p1.output or {}).get("sub_outputs")
        self.assertIsNotNone(sub_outputs)
        self.assertIn("S1", sub_outputs)
        self.assertEqual(sub_outputs["S1"]["status"], STATUS_SUCCESS)
        # testing_noop AdapterOutput echo Field
        self.assertIn("echo", sub_outputs["S1"]["output"])

    def test_nesting_depth_exceeds_limit_returns_e0410(self):
        """嵌套深度超过 3 层时, dispatch return E0410 Error."""
        from taurus.workflow.engine.schemas import STATUS_FAILED

        # via trigger_params 注入 __nesting_depth__=3, Mockcurrent已YesPage 3 层
        # 此时再TriggerchildWorkflow, 深度=4, 应被拒绝
        r = self.runner.trigger_workflow(
            self.parent_workflow,
            user_id=self.user.pk,
            trigger_params={"__nesting_depth__": 3},
        )

        # P1 在 trigger phase就被 dispatch, 应直接 FAILED
        p1 = WorkflowNodeExecution.objects.get(execution_id=r.execution_id, node_key="P1")
        self.assertEqual(p1.status, STATUS_FAILED)
        self.assertIn("E0410", p1.error_message or "")