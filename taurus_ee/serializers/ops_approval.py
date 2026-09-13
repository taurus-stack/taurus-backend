"""taurus_ee.serializers.ops_approval — M2.6 Ops 执行审批 Serializers (EE 专属).

承接 `taurus.serializers` 中原 4 个 OpsExecutionApproval*Serializer 的真实实现体，
taurus 侧保留同名 Thin Wrapper `class X(_EE*): pass` 以兼容既有 imports。
"""
from __future__ import annotations

from dvadmin.utils.serializers import CustomModelSerializer
from rest_framework import serializers

from taurus.models import OpsExecutionApproval


class _EEOpsExecutionApprovalSerializer(CustomModelSerializer):
    """Execution task approval serializer"""
    host_name = serializers.CharField(source='host.host_name', read_only=True)
    host_ip = serializers.CharField(source='host.host_ip', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    approval_mode_display = serializers.CharField(source='get_approval_mode_display', read_only=True, default='')
    execution_type_display = serializers.CharField(source='get_execution_type_display', read_only=True, default='')
    source_type_display = serializers.CharField(source='get_source_type_display', read_only=True, default='')
    submitter_username = serializers.CharField(source='submitter.username', read_only=True, default='')
    approver_username = serializers.CharField(source='approver.username', read_only=True, default='')
    ops_execution_id = serializers.CharField(source='ops_execution.execution_id', read_only=True, default='')
    batch_hosts = serializers.SerializerMethodField()

    def get_batch_hosts(self, obj):
        if not obj.batch_id or not obj.target_hosts_count or obj.target_hosts_count <= 1:
            return []
        siblings = OpsExecutionApproval.objects.filter(
            batch_id=obj.batch_id
        ).select_related('host').values_list(
            'host__host_name', 'host__host_ip'
        ).distinct()
        return [
            {'host_name': hn or '', 'host_ip': hip or ''}
            for hn, hip in siblings
        ]

    class Meta:
        model = OpsExecutionApproval
        fields = [
            'id', 'batch_id', 'status', 'status_display',
            'source_type', 'source_type_display', 'related_name', 'related_desc',
            'submitter', 'submitter_name', 'submitter_username', 'submit_desc',
            'approver', 'approver_name', 'approver_username', 'approve_reason', 'approve_time',
            'approval_mode', 'approval_mode_display',
            'candidate_approvers', 'approval_records',
            'finish_time',
            'host', 'host_name', 'host_ip',
            'execution_type', 'execution_type_display',
            'command', 'use_shell',
            'script_type', 'script_content', 'args',
            'working_directory', 'timeout_seconds', 'environment',
            'merge_streams', 'load_profile', 'privileged', 'su_user',
            'exec_mode', 'concurrency', 'fail_strategy',
            'pilot_count', 'pilot_success_rate', 'auto_notify',
            'target_hosts_count', 'batch_hosts',
            'ops_execution', 'ops_execution_id',
            'create_datetime', 'update_datetime', 'creator', 'modifier',
        ]
        read_only_fields = [
            'id', 'status', 'source_type', 'submitter', 'submitter_name',
            'approver', 'approver_name', 'approve_reason', 'approve_time',
            'approval_records',
            'finish_time', 'ops_execution',
            'create_datetime', 'update_datetime', 'creator', 'modifier',
        ]


class _EEOpsExecutionApprovalListSerializer(CustomModelSerializer):
    """Execution task approval list serializer"""
    host_name = serializers.CharField(source='host.host_name', read_only=True)
    host_ip = serializers.CharField(source='host.host_ip', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    approval_mode_display = serializers.CharField(source='get_approval_mode_display', read_only=True, default='')
    execution_type_display = serializers.CharField(source='get_execution_type_display', read_only=True, default='')
    source_type_display = serializers.CharField(source='get_source_type_display', read_only=True, default='')
    submitter_username = serializers.CharField(source='submitter.username', read_only=True, default='')
    approver_username = serializers.CharField(source='approver.username', read_only=True, default='')

    class Meta:
        model = OpsExecutionApproval
        fields = [
            'id', 'batch_id', 'status', 'status_display',
            'source_type', 'source_type_display', 'related_name', 'related_desc',
            'submitter_name', 'submitter_username', 'submit_desc',
            'approver_name', 'approver_username', 'approve_reason', 'approve_time',
            'approval_mode', 'approval_mode_display',
            'candidate_approvers',
            'finish_time',
            'host', 'host_name', 'host_ip',
            'execution_type', 'execution_type_display',
            'command', 'script_type', 'working_directory', 'timeout_seconds',
            'merge_streams', 'load_profile', 'privileged', 'su_user',
            'fail_strategy', 'pilot_count', 'pilot_success_rate', 'auto_notify',
            'ops_execution',
            'create_datetime',
        ]


class _EEOpsExecutionApprovalCreateSerializer(CustomModelSerializer):
    """Execution task approval create serializer"""

    class Meta:
        model = OpsExecutionApproval
        fields = [
            'id', 'batch_id', 'status',
            'submit_desc',
            'approval_mode', 'candidate_approvers',
            'host',
            'execution_type', 'command', 'use_shell',
            'script_type', 'script_content', 'args',
            'working_directory', 'timeout_seconds', 'environment',
            'merge_streams', 'load_profile', 'privileged', 'su_user',
            'exec_mode', 'concurrency', 'fail_strategy',
            'pilot_count', 'pilot_success_rate', 'auto_notify',
            'target_hosts_count',
            'create_datetime', 'update_datetime', 'creator', 'modifier',
        ]
        read_only_fields = ['id', 'status', 'create_datetime', 'update_datetime', 'creator', 'modifier']


class _EEOpsExecutionApprovalActionSerializer(serializers.Serializer):
    """Approval operation request serializer (approve/reject/delegate/add-sign 共用 reason 字段)"""
    reason = serializers.CharField(required=False, allow_blank=True, default='', help_text="Approval comment")
