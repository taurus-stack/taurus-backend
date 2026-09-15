"""Script approval serializers (EE only).
Classes 5 个: ScriptApproveSer, ScriptApprovalRuleSer, ScriptApprovalNodeSer,
ScriptApprovalInstanceSer, ScriptApprovalNodeExecutionSer。
"""
from rest_framework import serializers
from dvadmin.utils.serializers import CustomModelSerializer
from taurus.models import (
    ScriptApprove,
    ScriptApprovalRule,
    ScriptApprovalNode,
    ScriptApprovalInstance,
    ScriptApprovalNodeExecution,
)


# ---------------------------------------------------------------------------
# 基础审批提交（简单 submitter/approver 流程）
# ---------------------------------------------------------------------------
class ScriptApproveSerializer(CustomModelSerializer):
    script_name = serializers.CharField(source='script.name', read_only=True)
    script_type = serializers.CharField(source='script.script_type', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    risk_level_display = serializers.CharField(source='get_risk_level_display', read_only=True)
    submitter_name_display = serializers.CharField(
        source='submitter.username', read_only=True, default=''
    )
    approver_name_display = serializers.CharField(
        source='approver.username', read_only=True, default=''
    )

    class Meta:
        model = ScriptApprove
        fields = [
            'id', 'script', 'script_name', 'script_type', 'script_version',
            'risk_level', 'risk_level_display', 'risk_points',
            'status', 'status_display', 'submitter', 'submitter_name_display',
            'submitter_name', 'submit_desc',
            'approver', 'approver_name_display', 'approver_name',
            'approve_time', 'approve_reason', 'create_datetime',
        ]


# ---------------------------------------------------------------------------
# 规则驱动的多级审批（ApprovalRule + Node + Instance + NodeExecution）
# ---------------------------------------------------------------------------
class ScriptApprovalRuleSerializer(CustomModelSerializer):
    node_count = serializers.SerializerMethodField()

    class Meta:
        model = ScriptApprovalRule
        fields = [
            'id', 'name', 'description', 'condition_groups',
            'priority', 'is_active', 'node_count',
            'creator_name', 'create_datetime', 'update_datetime',
        ]
        read_only_fields = ['id', 'create_datetime', 'update_datetime']

    def get_node_count(self, obj):
        return obj.nodes.count()


class ScriptApprovalNodeSerializer(CustomModelSerializer):
    approver_type_display = serializers.CharField(
        source='get_approver_type_display', read_only=True
    )
    approval_mode_display = serializers.CharField(
        source='get_approval_mode_display', read_only=True
    )

    class Meta:
        model = ScriptApprovalNode
        fields = [
            'id', 'rule', 'node_name', 'approver_type', 'approver_type_display',
            'approver_config', 'approval_mode', 'approval_mode_display',
            'step_order', 'create_datetime',
        ]
        read_only_fields = ['id', 'create_datetime']


class ScriptApprovalInstanceSerializer(CustomModelSerializer):
    script_name = serializers.CharField(source='script.name', read_only=True)
    script_type = serializers.CharField(source='script.script_type', read_only=True)
    script_content = serializers.CharField(source='script.content', read_only=True, default='')
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    risk_level_display = serializers.SerializerMethodField()
    risk_points = serializers.SerializerMethodField()
    submitter_name_display = serializers.CharField(
        source='submitter.username', read_only=True, default=''
    )
    current_node = serializers.SerializerMethodField()
    total_nodes = serializers.SerializerMethodField()
    node_executions = serializers.SerializerMethodField()

    class Meta:
        model = ScriptApprovalInstance
        fields = [
            'id', 'script', 'script_name', 'script_type', 'script_content', 'script_version',
            'rule', 'rule_name', 'status', 'status_display',
            'current_node_index', 'current_node', 'total_nodes',
            'submitter', 'submitter_name_display', 'submitter_name', 'submit_desc',
            'risk_level', 'risk_level_display', 'risk_points',
            'finish_time', 'creator_name', 'create_datetime',
            'node_executions',
        ]
        read_only_fields = ['id', 'create_datetime', 'finish_time']

    def get_risk_points(self, obj):
        saved_points = obj.risk_points or []
        if saved_points:
            return saved_points
        script = getattr(obj, 'script', None)
        if not script:
            return saved_points
        try:
            from taurus.script_checker import ScriptCheckService
            result = ScriptCheckService.check(
                script.content or '',
                script.script_type or ''
            )
            points = []
            for issue in result.issues:
                severity_prefix = {
                    'error': '[High]', 'warning': '[Warning]',
                    'info': '[Info]', 'style': '[Style]'
                }.get(issue.severity, '')
                line_info = f'Line {issue.line}' if issue.line else ''
                msg_parts = [p for p in [severity_prefix, line_info, issue.message] if p]
                desc = ' '.join(msg_parts)
                if issue.fix_suggestion and issue.severity == 'error':
                    desc += f' (Suggestion: {issue.fix_suggestion})'
                points.append(desc)
            if points and not saved_points:
                try:
                    obj.risk_points = points
                    result.calculate_risk_level()
                    obj.risk_level = result.risk_level
                    obj.save(update_fields=['risk_points', 'risk_level'])
                except Exception:  # noqa: BLE001
                    pass
            return points
        except Exception:  # noqa: BLE001
            return saved_points

    def get_risk_level_display(self, obj):
        risk_map = {'low': 'Low', 'medium': 'Medium', 'high': 'High'}
        return risk_map.get(obj.risk_level, obj.risk_level or '')

    def get_current_node(self, obj):
        nodes = list(obj.node_executions.order_by('step_order'))
        if nodes and obj.current_node_index < len(nodes):
            return nodes[obj.current_node_index].node_name
        return ''

    def get_total_nodes(self, obj):
        return obj.node_executions.count()

    def get_node_executions(self, obj):
        nodes = obj.node_executions.order_by('step_order')
        return ScriptApprovalNodeExecutionSerializer(nodes, many=True).data


class ScriptApprovalNodeExecutionSerializer(CustomModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    approver_type_display = serializers.SerializerMethodField()
    approval_mode_display = serializers.SerializerMethodField()

    class Meta:
        model = ScriptApprovalNodeExecution
        fields = [
            'id', 'instance', 'node_name', 'approver_type', 'approver_type_display',
            'approver_config', 'approval_mode', 'approval_mode_display',
            'step_order', 'candidate_approvers', 'approval_records',
            'delegate_records', 'add_sign_records',
            'status', 'status_display', 'finish_time', 'create_datetime',
        ]
        read_only_fields = ['id', 'create_datetime', 'finish_time']

    def get_approver_type_display(self, obj):
        type_map = {
            'category_reviewer': 'Category reviewer',
            'specific_users': 'Specific users',
            'role': 'Specific role',
            'submitter_manager': "Submitter's manager",
        }
        return type_map.get(obj.approver_type, obj.approver_type)

    def get_approval_mode_display(self, obj):
        mode_map = {'any': 'Anyone', 'all': 'All must sign', 'first': 'First sign'}
        return mode_map.get(obj.approval_mode, obj.approval_mode)
