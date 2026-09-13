"""Workflow approval serializers (EE-only).

类列表（共 9 类）：
  1. WorkflowApproveSerializer                 — WorkflowApprove 模型 list/detail（兼容旧字段）
  2. WorkflowApproveCreateSerializer           — create 请求参数
  3. WorkflowApproveApproveSerializer          — approve 请求参数
  4. WorkflowApproveRejectSerializer           — reject 请求参数
  5. WorkflowApproveCompatSerializer           — 兼容旧前端 WorkflowApprove 中心（输出字段对齐旧版）
  6. WorkflowApprovalRuleSerializer            — 规则定义（L2295 版，带 node_count）
  7. WorkflowApprovalNodeSerializer            — 规则节点定义（L2312 版）
  8. WorkflowApprovalInstanceSerializer        — 规则驱动流程实例（L2331 版，含 current_node / node_count / approved_node_count）
  9. WorkflowApprovalNodeExecutionSerializer   — 实例节点执行（L2378 版，含 2 个 display getter）
"""
from __future__ import annotations

from rest_framework import serializers as _sz

from dvadmin.utils.serializers import CustomModelSerializer
from taurus.models import (
    WorkflowApprove,
    WorkflowApprovalRule,
    WorkflowApprovalNode,
    WorkflowApprovalInstance,
    WorkflowApprovalNodeExecution,
)

# 兼容老 WorkflowApproveCompatSerializer 在 taurus/serializers.py L1870 已定义过依赖
#   _wf_approve_drf_sz 指向 rest_framework.serializers，这里保留 alias 保持一致
_wf_approve_drf_sz = _sz
_WFApproveInstModel = WorkflowApprovalInstance


# 1~4 — WorkflowApprove 兼容系列
class WorkflowApproveSerializer(CustomModelSerializer):
    workflow_name = _sz.CharField(source='workflow.name', read_only=True)
    submitter_name_display = _sz.CharField(source='submitter.username', read_only=True, default='')
    approver_name_display = _sz.CharField(source='approver.username', read_only=True, default='')
    status_display = _sz.CharField(source='get_status_display', read_only=True)
    risk_level_display = _sz.CharField(source='get_risk_level_display', read_only=True)
    workflow_creator = _sz.CharField(source='workflow.creator.username', read_only=True, default='')
    workflow_auth_type = _sz.CharField(source='workflow.auth_type', read_only=True, default='')
    workflow_category_name = _sz.SerializerMethodField()

    class Meta:
        model = WorkflowApprove
        fields = [
            'id', 'workflow', 'workflow_name', 'workflow_version',
            'risk_level', 'risk_level_display', 'risk_points',
            'status', 'status_display',
            'submitter', 'submitter_name', 'submitter_name_display',
            'submit_desc',
            'approver', 'approver_name', 'approver_name_display',
            'approve_time', 'approve_reason',
            'workflow_creator', 'workflow_auth_type', 'workflow_category_name',
            'create_datetime', 'update_datetime', 'creator', 'modifier'
        ]
        read_only_fields = [
            'create_datetime', 'update_datetime', 'creator', 'modifier',
            'status_display', 'risk_level_display',
            'submitter_name_display', 'approver_name_display',
            'approve_time', 'approver', 'approver_name'
        ]

    def get_workflow_category_name(self, obj):
        if obj.workflow and obj.workflow.category:
            return obj.workflow.category.name
        return '-'


class WorkflowApproveCreateSerializer(CustomModelSerializer):
    class Meta:
        model = WorkflowApprove
        fields = [
            'workflow', 'workflow_version',
            'risk_level', 'risk_points', 'submit_desc',
        ]


class WorkflowApproveApproveSerializer(CustomModelSerializer):
    approve_reason = _sz.CharField(required=False, allow_blank=True, max_length=1000)

    class Meta:
        model = WorkflowApprove
        fields = ['approve_reason']


class WorkflowApproveRejectSerializer(CustomModelSerializer):
    approve_reason = _sz.CharField(required=True, allow_blank=False, max_length=1000,
                                    help_text='Rejection reason cannot be empty')

    class Meta:
        model = WorkflowApprove
        fields = ['approve_reason']


# 5 — Compat 版（WorkflowApproveViewSet 旧中心 list/detail 用）
class WorkflowApproveCompatSerializer(CustomModelSerializer):
    workflow_name = _wf_approve_drf_sz.CharField(source='workflow.name', read_only=True, default='')
    submitter_name_display = _wf_approve_drf_sz.CharField(source='submitter.username', read_only=True, default='')
    approver = _wf_approve_drf_sz.SerializerMethodField()
    approver_name = _wf_approve_drf_sz.SerializerMethodField()
    approver_name_display = _wf_approve_drf_sz.SerializerMethodField()
    approve_time = _wf_approve_drf_sz.SerializerMethodField()
    approve_reason = _wf_approve_drf_sz.SerializerMethodField()
    status_display = _wf_approve_drf_sz.SerializerMethodField()
    risk_level_display = _wf_approve_drf_sz.SerializerMethodField()
    workflow_creator = _wf_approve_drf_sz.CharField(source='workflow.creator.username', read_only=True, default='')
    workflow_auth_type = _wf_approve_drf_sz.CharField(source='workflow.auth_type', read_only=True, default='')
    workflow_category_name = _wf_approve_drf_sz.SerializerMethodField()
    creator = _wf_approve_drf_sz.SerializerMethodField()
    modifier = _wf_approve_drf_sz.SerializerMethodField()
    status = _wf_approve_drf_sz.SerializerMethodField()
    workflow = _wf_approve_drf_sz.SerializerMethodField()
    category_name = _wf_approve_drf_sz.SerializerMethodField()
    auth_type = _wf_approve_drf_sz.SerializerMethodField()
    candidate_approvers = _wf_approve_drf_sz.SerializerMethodField()

    class Meta:
        model = _WFApproveInstModel
        fields = [
            'id', 'workflow', 'workflow_name', 'workflow_version',
            'risk_level', 'risk_level_display', 'risk_points',
            'status', 'status_display',
            'submitter', 'submitter_name', 'submitter_name_display',
            'submit_desc',
            'approver', 'approver_name', 'approver_name_display',
            'approve_time', 'approve_reason',
            'workflow_creator', 'workflow_auth_type', 'workflow_category_name',
            'category_name', 'auth_type',
            'candidate_approvers',
            'create_datetime', 'update_datetime', 'creator', 'modifier',
        ]
        read_only_fields = ['id', 'create_datetime', 'update_datetime']

    # 兼容 getter 部分 — 因为 L1908 之后的 L1870 对应类还定义了几十个 get_* 方法，
    # 我们这里直接复用 taurs 侧已经存在的同名实现思路：通过 instance.node_executions 聚合得到
    # 最后节点动作记录，作为 approver/approve_time/approve_reason 显示。
    def _last_approve_record(self, obj):
        nodes = list(obj.node_executions.order_by('-step_order'))
        for node in nodes:
            for rec in reversed(node.approval_records or []):
                if rec.get('action') in ('approve', 'reject'):
                    return rec, node
        return None, None

    def get_approver(self, obj):
        rec, _ = self._last_approve_record(obj)
        return rec.get('user_id') if rec else None

    def get_approver_name(self, obj):
        rec, _ = self._last_approve_record(obj)
        return rec.get('username') if rec else ''

    def get_approver_name_display(self, obj):
        rec, _ = self._last_approve_record(obj)
        if not rec:
            return ''
        return rec.get('name') or rec.get('username') or ''

    def get_approve_time(self, obj):
        rec, node = self._last_approve_record(obj)
        if rec:
            return rec.get('operate_time')
        if node and getattr(node, 'finish_time', None):
            try:
                return node.finish_time.isoformat()
            except Exception:
                return str(node.finish_time)
        return None

    def get_approve_reason(self, obj):
        rec, _ = self._last_approve_record(obj)
        return rec.get('reason') if rec else ''

    def get_status_display(self, obj):
        mapping = {'pending': 'Pending', 'approving': 'Approving',
                   'approved': 'Approved', 'rejected': 'Rejected',
                   'revoked': 'Revoked', 'cancelled': 'Cancelled',
                   'completed': 'Completed'}
        return mapping.get(obj.status, obj.status or '-')

    def get_risk_level_display(self, obj):
        mapping = {'low': 'Low', 'medium': 'Medium', 'high': 'High'}
        return mapping.get(obj.risk_level, obj.risk_level or '-')

    def get_workflow_category_name(self, obj):
        if obj.workflow and getattr(obj.workflow, 'category', None):
            return obj.workflow.category.name or ''
        return ''

    def get_creator(self, obj):
        return getattr(obj.submitter, 'id', None)

    def get_modifier(self, obj):
        return getattr(obj.submitter, 'id', None)

    def get_status(self, obj):
        # 旧中心 status 与新实例对齐即可
        return obj.status

    def get_workflow(self, obj):
        return getattr(obj.workflow, 'id', None)

    def get_category_name(self, obj):
        return self.get_workflow_category_name(obj)

    def get_auth_type(self, obj):
        return getattr(obj.workflow, 'auth_type', None)

    def get_candidate_approvers(self, obj):
        nodes = list(obj.node_executions.order_by('step_order'))
        if not nodes:
            return []
        try:
            cur = nodes[getattr(obj, 'current_node_index', 0)]
        except Exception:
            cur = nodes[0]
        return getattr(cur, 'candidate_approvers', None) or []


# 6 — WorkflowApprovalRule
class WorkflowApprovalRuleSerializer(CustomModelSerializer):
    node_count = _sz.SerializerMethodField()

    class Meta:
        model = WorkflowApprovalRule
        fields = [
            'id', 'name', 'description', 'condition_groups',
            'priority', 'is_active', 'node_count',
            'creator_name', 'create_datetime', 'update_datetime',
        ]
        read_only_fields = ['id', 'create_datetime', 'update_datetime']

    def get_node_count(self, obj):
        return obj.nodes.count()


# 7 — WorkflowApprovalNode
class WorkflowApprovalNodeSerializer(CustomModelSerializer):
    approver_type_display = _sz.CharField(source='get_approver_type_display', read_only=True)
    approval_mode_display = _sz.CharField(source='get_approval_mode_display', read_only=True)

    class Meta:
        model = WorkflowApprovalNode
        fields = [
            'id', 'rule', 'node_name', 'approver_type', 'approver_type_display',
            'approver_config', 'approval_mode', 'approval_mode_display',
            'step_order', 'creator_name', 'create_datetime', 'update_datetime',
        ]
        read_only_fields = ['id', 'create_datetime', 'update_datetime']


# 8 — WorkflowApprovalInstance
class WorkflowApprovalInstanceSerializer(CustomModelSerializer):
    status_display = _sz.CharField(source='get_status_display', read_only=True)
    workflow_name = _sz.CharField(source='workflow.name', read_only=True)
    risk_level_display = _sz.SerializerMethodField()
    current_node = _sz.SerializerMethodField()
    node_count = _sz.SerializerMethodField()
    approved_node_count = _sz.SerializerMethodField()

    class Meta:
        model = WorkflowApprovalInstance
        fields = [
            'id', 'workflow', 'workflow_name', 'workflow_version',
            'rule', 'rule_name',
            'status', 'status_display', 'current_node_index',
            'submitter', 'submitter_name', 'submit_desc',
            'risk_level', 'risk_level_display', 'risk_points',
            'finish_time',
            'current_node', 'node_count', 'approved_node_count',
            'creator_name', 'create_datetime', 'update_datetime',
        ]
        read_only_fields = ['id', 'create_datetime', 'update_datetime']

    def get_risk_level_display(self, obj):
        mapping = {'low': 'Low', 'medium': 'Medium', 'high': 'High'}
        return mapping.get(obj.risk_level, obj.risk_level or '-')

    def get_current_node(self, obj):
        nodes = list(obj.node_executions.order_by('step_order'))
        if not nodes:
            return None
        if 0 <= obj.current_node_index < len(nodes):
            node = nodes[obj.current_node_index]
            return {
                'id': node.id,
                'node_name': node.node_name,
                'status': node.status,
            }
        return None

    def get_node_count(self, obj):
        return obj.node_executions.count()

    def get_approved_node_count(self, obj):
        return obj.node_executions.filter(status='approved').count()


# 9 — WorkflowApprovalNodeExecution
class WorkflowApprovalNodeExecutionSerializer(CustomModelSerializer):
    status_display = _sz.CharField(source='get_status_display', read_only=True)
    approver_type_display = _sz.SerializerMethodField()
    approval_mode_display = _sz.SerializerMethodField()

    class Meta:
        model = WorkflowApprovalNodeExecution
        fields = [
            'id', 'instance',
            'node_name', 'approver_type', 'approver_type_display',
            'approver_config', 'approval_mode', 'approval_mode_display',
            'step_order',
            'candidate_approvers', 'approval_records',
            'delegate_records', 'add_sign_records',
            'status', 'status_display', 'finish_time',
            'creator_name', 'create_datetime', 'update_datetime',
        ]
        read_only_fields = ['id', 'create_datetime', 'update_datetime']

    def get_approver_type_display(self, obj):
        mapping = {
            'category_reviewer': 'Category reviewer',
            'specific_users': 'Specific users',
            'role': 'Specific role',
            'submitter_manager': "Submitter's manager",
        }
        return mapping.get(obj.approver_type, obj.approver_type)

    def get_approval_mode_display(self, obj):
        mapping = {'any': 'Anyone', 'all': 'All must sign', 'first': 'First sign'}
        return mapping.get(obj.approval_mode, obj.approval_mode)
