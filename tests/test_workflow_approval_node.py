"""WorkflowApprovalNode 节.componentUnit test.

覆盖:
- Model CRUD 与约束
- SerializationserverField与 display method
- API ViewSet 的 CRUD, filter, search
"""
from __future__ import annotations

import pytest

from django.contrib.auth import get_user_model

pytestmark = pytest.mark.django_db

User = get_user_model()


# ===================== Fixtures =====================


@pytest.fixture()
def user(db):
    return User.objects.create_user(
        username="test_creator", password="x" * 8
    )


@pytest.fixture()
def rule(db, user):
    from taurus.models import WorkflowApprovalRule
    return WorkflowApprovalRule.objects.create(
        name="测试规则",
        description="测试规则描述",
        priority=10,
        is_active=True,
        creator=user,
    )


@pytest.fixture()
def node(db, rule, user):
    from taurus.models import WorkflowApprovalNode
    return WorkflowApprovalNode.objects.create(
        rule=rule,
        node_name="一级审核",
        approver_type="category_reviewer",
        approver_config={},
        approval_mode="any",
        step_order=1,
        creator=user,
    )


@pytest.fixture()
def api_client(user, db):
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=user)
    return client


# ===================== Model Tests =====================


class TestWorkflowApprovalNodeModel:
    """WorkflowApprovalNode Model CRUD 与约束test."""

    def test_create_node_minimal(self, rule, user):
        from taurus.models import WorkflowApprovalNode
        node = WorkflowApprovalNode.objects.create(
            rule=rule,
            node_name="一级审核",
            approver_type="category_reviewer",
        )
        assert node.id is not None
        assert node.approval_mode == "any"
        assert node.step_order == 1
        assert node.approver_config == {}

    def test_create_node_all_approver_types(self, rule, user):
        from taurus.models import WorkflowApprovalNode
        for approver_type, _label in WorkflowApprovalNode.APPROVER_TYPE_CHOICES:
            node = WorkflowApprovalNode.objects.create(
                rule=rule,
                node_name=f"节点-{approver_type}",
                approver_type=approver_type,
                approver_config=self._default_config(approver_type),
                step_order=1,
            )
            assert node.approver_type == approver_type
            assert node.get_approver_type_display() in dict(
                WorkflowApprovalNode.APPROVER_TYPE_CHOICES
            ).values()

    def test_create_node_all_approval_modes(self, rule, user):
        from taurus.models import WorkflowApprovalNode
        for mode, _label in WorkflowApprovalNode.APPROVAL_MODE_CHOICES:
            node = WorkflowApprovalNode.objects.create(
                rule=rule,
                node_name=f"节点-{mode}",
                approver_type="category_reviewer",
                approval_mode=mode,
                step_order=1,
            )
            assert node.approval_mode == mode
            assert node.get_approval_mode_display() in dict(
                WorkflowApprovalNode.APPROVAL_MODE_CHOICES
            ).values()

    def test_str(self, node, rule):
        assert str(node) == f"{rule.name} - {node.node_name}"

    def test_ordering_by_step_order(self, rule, user):
        from taurus.models import WorkflowApprovalNode
        WorkflowApprovalNode.objects.create(
            rule=rule, node_name="节点3", approver_type="category_reviewer", step_order=3
        )
        WorkflowApprovalNode.objects.create(
            rule=rule, node_name="节点1", approver_type="category_reviewer", step_order=1
        )
        WorkflowApprovalNode.objects.create(
            rule=rule, node_name="节点2", approver_type="category_reviewer", step_order=2
        )
        nodes = list(rule.nodes.all())
        assert [n.step_order for n in nodes] == [1, 2, 3]

    def test_ordering_by_step_order_then_id(self, rule, user):
        from taurus.models import WorkflowApprovalNode
        n1 = WorkflowApprovalNode.objects.create(
            rule=rule, node_name="A", approver_type="category_reviewer", step_order=1
        )
        n2 = WorkflowApprovalNode.objects.create(
            rule=rule, node_name="B", approver_type="category_reviewer", step_order=1
        )
        nodes = list(rule.nodes.filter(step_order=1))
        assert nodes[0].id <= nodes[1].id

    def test_rule_cascade_delete(self, rule, node):
        from taurus.models import WorkflowApprovalNode
        node_count_before = WorkflowApprovalNode.objects.count()
        rule.delete()
        assert WorkflowApprovalNode.objects.count() == node_count_before - 1

    def test_node_cascade_delete_rule_unchanged(self, node, rule):
        from taurus.models import WorkflowApprovalRule
        node.delete()
        assert WorkflowApprovalRule.objects.filter(id=rule.id).exists()

    def test_approver_config_defaults(self, rule, user):
        from taurus.models import WorkflowApprovalNode
        node = WorkflowApprovalNode.objects.create(
            rule=rule, node_name="节点", approver_type="category_reviewer"
        )
        assert node.approver_config == {}

    def test_approver_config_specific_users(self, rule, user):
        from taurus.models import WorkflowApprovalNode
        cfg = {"user_ids": [1, 2, 3]}
        node = WorkflowApprovalNode.objects.create(
            rule=rule,
            node_name="指定用户节点",
            approver_type="specific_users",
            approver_config=cfg,
        )
        assert node.approver_config == cfg

    def test_approver_config_role(self, rule, user):
        from taurus.models import WorkflowApprovalNode
        cfg = {"role_codes": ["dba", "security"]}
        node = WorkflowApprovalNode.objects.create(
            rule=rule,
            node_name="角色节点",
            approver_type="role",
            approver_config=cfg,
        )
        assert node.approver_config == cfg

    def test_approver_config_submitter_manager(self, rule, user):
        from taurus.models import WorkflowApprovalNode
        cfg = {"levels": 1}
        node = WorkflowApprovalNode.objects.create(
            rule=rule,
            node_name="主管节点",
            approver_type="submitter_manager",
            approver_config=cfg,
        )
        assert node.approver_config == cfg

    def test_node_name_max_length(self, rule, user):
        from taurus.models import WorkflowApprovalNode
        long_name = "长" * 100
        node = WorkflowApprovalNode.objects.create(
            rule=rule,
            node_name=long_name,
            approver_type="category_reviewer",
        )
        assert node.node_name == long_name

    def test_multiple_nodes_under_rule(self, rule, user):
        from taurus.models import WorkflowApprovalNode
        WorkflowApprovalNode.objects.create(
            rule=rule, node_name="节点A", approver_type="category_reviewer", step_order=1
        )
        WorkflowApprovalNode.objects.create(
            rule=rule, node_name="节点B", approver_type="specific_users", step_order=2
        )
        assert rule.nodes.count() == 2

    @staticmethod
    def _default_config(approver_type: str) -> dict:
        configs = {
            "category_reviewer": {},
            "specific_users": {"user_ids": [1]},
            "role": {"role_codes": ["dev"]},
            "submitter_manager": {"levels": 1},
        }
        return configs.get(approver_type, {})


# ===================== Serializer Tests =====================


class TestWorkflowApprovalNodeSerializer:
    """WorkflowApprovalNodeSerializer Serializationservertest."""

    def test_serializer_includes_display_fields(self, node):
        from taurus.serializers import WorkflowApprovalNodeSerializer
        serializer = WorkflowApprovalNodeSerializer(node)
        data = serializer.data
        assert "approver_type_display" in data
        assert "approval_mode_display" in data
        assert data["approver_type_display"] == "分类审核人"
        assert data["approval_mode_display"] == "或签"

    def test_serializer_readonly_fields(self, node):
        from taurus.serializers import WorkflowApprovalNodeSerializer
        serializer = WorkflowApprovalNodeSerializer(node)
        data = serializer.data
        assert "id" in data
        assert "create_datetime" in data
        assert "update_datetime" in data
        assert "creator_name" in data

    def test_serializer_all_approver_types_display(self, rule, user):
        from taurus.models import WorkflowApprovalNode
        from taurus.serializers import WorkflowApprovalNodeSerializer
        display_map = dict(WorkflowApprovalNode.APPROVER_TYPE_CHOICES)
        for approver_type, expected_label in display_map.items():
            node = WorkflowApprovalNode.objects.create(
                rule=rule,
                node_name=f"节点-{approver_type}",
                approver_type=approver_type,
                approver_config=self._default_config(approver_type),
                step_order=1,
            )
            data = WorkflowApprovalNodeSerializer(node).data
            assert data["approver_type_display"] == expected_label

    def test_serializer_all_approval_modes_display(self, rule, user):
        from taurus.models import WorkflowApprovalNode
        from taurus.serializers import WorkflowApprovalNodeSerializer
        display_map = dict(WorkflowApprovalNode.APPROVAL_MODE_CHOICES)
        for mode, expected_label in display_map.items():
            node = WorkflowApprovalNode.objects.create(
                rule=rule,
                node_name=f"节点-{mode}",
                approver_type="category_reviewer",
                approval_mode=mode,
                step_order=1,
            )
            data = WorkflowApprovalNodeSerializer(node).data
            assert data["approval_mode_display"] == expected_label

    def test_serializer_create_valid(self, rule, user):
        from taurus.serializers import WorkflowApprovalNodeSerializer
        data = {
            "rule": rule.id,
            "node_name": "测试节点",
            "approver_type": "category_reviewer",
            "approval_mode": "any",
            "step_order": 5,
        }
        serializer = WorkflowApprovalNodeSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        node = serializer.save()
        assert node.node_name == "测试节点"
        assert node.step_order == 5

    def test_serializer_create_missing_node_name(self, rule, user):
        from taurus.serializers import WorkflowApprovalNodeSerializer
        data = {
            "rule": rule.id,
            "approver_type": "category_reviewer",
        }
        serializer = WorkflowApprovalNodeSerializer(data=data)
        assert not serializer.is_valid()
        assert "节点名称" in serializer.errors

    def test_serializer_create_missing_approver_type(self, rule, user):
        from taurus.serializers import WorkflowApprovalNodeSerializer
        data = {
            "rule": rule.id,
            "node_name": "测试节点",
        }
        serializer = WorkflowApprovalNodeSerializer(data=data)
        assert not serializer.is_valid()
        assert "审核人类型" in serializer.errors

    def test_serializer_create_invalid_approver_type(self, rule, user):
        from taurus.serializers import WorkflowApprovalNodeSerializer
        data = {
            "rule": rule.id,
            "node_name": "测试节点",
            "approver_type": "invalid_type",
        }
        serializer = WorkflowApprovalNodeSerializer(data=data)
        assert not serializer.is_valid()

    def test_serializer_create_invalid_approval_mode(self, rule, user):
        from taurus.serializers import WorkflowApprovalNodeSerializer
        data = {
            "rule": rule.id,
            "node_name": "测试节点",
            "approver_type": "category_reviewer",
            "approval_mode": "invalid_mode",
        }
        serializer = WorkflowApprovalNodeSerializer(data=data)
        assert not serializer.is_valid()

    def test_serializer_update(self, node):
        from taurus.serializers import WorkflowApprovalNodeSerializer
        data = {"node_name": "更新后的节点名", "step_order": 10}
        serializer = WorkflowApprovalNodeSerializer(node, data=data, partial=True)
        assert serializer.is_valid(), serializer.errors
        updated = serializer.save()
        assert updated.node_name == "更新后的节点名"
        assert updated.step_order == 10

    @staticmethod
    def _default_config(approver_type: str) -> dict:
        configs = {
            "category_reviewer": {},
            "specific_users": {"user_ids": [1]},
            "role": {"role_codes": ["dev"]},
            "submitter_manager": {"levels": 1},
        }
        return configs.get(approver_type, {})


# ===================== API ViewSet Tests =====================


class TestWorkflowApprovalNodeAPI:
    """WorkflowApprovalRuleNodeViewSet API Integration test."""

    def test_list_nodes(self, api_client, rule, node):
        from taurus.models import WorkflowApprovalNode
        WorkflowApprovalNode.objects.create(
            rule=rule,
            node_name="第二个节点",
            approver_type="specific_users",
            step_order=2,
        )
        response = api_client.get("/api/taurus/workflow-approval-node/")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 2000
        results = data["data"]
        assert len(results) >= 2

    def test_list_nodes_filter_by_rule(self, api_client, rule, node):
        response = api_client.get(f"/api/taurus/workflow-approval-node/?rule={rule.id}")
        assert response.status_code == 200
        data = response.json()
        for item in data["data"]:
            assert item["rule"] == rule.id

    def test_list_nodes_filter_by_approver_type(self, api_client, rule, node):
        response = api_client.get(
            "/api/taurus/workflow-approval-node/?approver_type=category_reviewer"
        )
        assert response.status_code == 200
        data = response.json()
        for item in data["data"]:
            assert item["approver_type"] == "category_reviewer"

    def test_list_nodes_search_by_name(self, api_client, rule, node):
        response = api_client.get("/api/taurus/workflow-approval-node/?search=一级审核")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) >= 1

    def test_create_node(self, api_client, rule):
        response = api_client.post(
            "/api/taurus/workflow-approval-node/",
            data={
                "rule": rule.id,
                "node_name": "API创建节点",
                "approver_type": "category_reviewer",
                "approval_mode": "all",
                "step_order": 3,
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 2000
        assert data["data"]["node_name"] == "API创建节点"
        assert data["data"]["approver_type_display"] == "分类审核人"
        assert data["data"]["approval_mode_display"] == "会签"

    def test_retrieve_node(self, api_client, node):
        response = api_client.get(f"/api/taurus/workflow-approval-node/{node.id}/")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 2000
        assert data["data"]["id"] == node.id
        assert data["data"]["node_name"] == "一级审核"

    def test_update_node(self, api_client, node):
        response = api_client.put(
            f"/api/taurus/workflow-approval-node/{node.id}/",
            data={
                "rule": node.rule.id,
                "node_name": "API更新节点名",
                "approver_type": node.approver_type,
                "approval_mode": node.approval_mode,
                "step_order": 10,
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 2000
        assert data["data"]["node_name"] == "API更新节点名"
        assert data["data"]["step_order"] == 10

    def test_partial_update_node(self, api_client, node):
        response = api_client.patch(
            f"/api/taurus/workflow-approval-node/{node.id}/",
            data={"step_order": 99},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 2000
        assert data["data"]["step_order"] == 99

    def test_delete_node(self, api_client, node):
        node_id = node.id
        response = api_client.delete(f"/api/taurus/workflow-approval-node/{node_id}/")
        assert response.status_code == 200
        from taurus.models import WorkflowApprovalNode
        assert not WorkflowApprovalNode.objects.filter(id=node_id).exists()

    def test_list_nodes_ordering(self, api_client, rule, node):
        from taurus.models import WorkflowApprovalNode
        WorkflowApprovalNode.objects.create(
            rule=rule, node_name="Z节点", approver_type="category_reviewer", step_order=10
        )
        WorkflowApprovalNode.objects.create(
            rule=rule, node_name="A节点", approver_type="category_reviewer", step_order=1
        )
        response = api_client.get("/api/taurus/workflow-approval-node/?ordering=step_order")
        assert response.status_code == 200
        data = response.json()
        results = data["data"]
        step_orders = [r["step_order"] for r in results]
        assert step_orders == sorted(step_orders)

    def test_create_node_with_specific_users_config(self, api_client, rule):
        response = api_client.post(
            "/api/taurus/workflow-approval-node/",
            data={
                "rule": rule.id,
                "node_name": "指定用户审核",
                "approver_type": "specific_users",
                "approver_config": {"user_ids": [1, 2, 3]},
                "approval_mode": "any",
                "step_order": 1,
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 2000
        assert data["data"]["approver_config"] == {"user_ids": [1, 2, 3]}

    def test_create_node_with_role_config(self, api_client, rule):
        response = api_client.post(
            "/api/taurus/workflow-approval-node/",
            data={
                "rule": rule.id,
                "node_name": "角色审核",
                "approver_type": "role",
                "approver_config": {"role_codes": ["dba", "security"]},
                "approval_mode": "first",
                "step_order": 2,
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 2000
        assert data["data"]["approver_config"] == {"role_codes": ["dba", "security"]}
        assert data["data"]["approval_mode_display"] == "先签"