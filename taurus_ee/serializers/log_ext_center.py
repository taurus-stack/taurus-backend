"""taurus_ee log-center / ext-center serializers for M2.5.

All classes here are the physical implementations that taurus.serializers.* Thin Wrappers
inherit from. Pattern:
    - Serializers for *real* CRUD models (HostLog / LogCommand / ContactLead / TaskCenterItem)
      keep their original fields/Meta exactly as taurus.serializers had them.
    - 6 extension center "empty" VS placeholders (KnowledgeBase / InspectionCenter /
      ToolsCenter / BackupRestoreCenter / DownloadCenter) share ONE tiny placeholder
      serializer because the 6 VS never actually process payloads in M2.5.
"""
from __future__ import annotations

from rest_framework import serializers
from dvadmin.utils.serializers import CustomModelSerializer

from taurus.models import (  # noqa: F401  (Meta.model 用到，统一显式导入)
    HostLog,
    LogCommand,
    ContactLead,
)


# =========================================================================
# HostLog — 2 Ser
# =========================================================================
class _EEHostLogSerializer(CustomModelSerializer):
    """EE 侧 HostLog 基础 Ser (与 taurus.serializers:HostLogSerializer 字段 100% 对齐)"""
    host_name = serializers.CharField(source='host.host_name', read_only=True)
    host_ip = serializers.CharField(source='host.host_ip', read_only=True)
    log_level_display = serializers.CharField(source='get_log_level_display', read_only=True)

    class Meta:
        model = HostLog
        fields = '__all__'
        read_only_fields = ['id', 'create_datetime', 'update_datetime', 'creator', 'modifier']


class _EEHostLogReceiveSerializer(serializers.Serializer):
    """Supervisor log 上报 request Ser（EE 实现，保持字段一致）"""
    host_id = serializers.UUIDField(help_text="Host ID")
    logs = serializers.ListField(child=serializers.DictField(), help_text="Log list")
    signature = serializers.CharField(required=False, allow_null=True, allow_blank=True, default='')
    nonce = serializers.CharField(required=False, allow_null=True, allow_blank=True, default='')
    timestamp_int = serializers.IntegerField(required=False, allow_null=True, default=None)


# =========================================================================
# LogCommand — 3 Ser
# =========================================================================
class _EELogCommandSerializer(CustomModelSerializer):
    host_name = serializers.CharField(source='host.host_name', read_only=True)
    host_ip = serializers.CharField(source='host.host_ip', read_only=True)
    action_display = serializers.CharField(source='get_action_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = LogCommand
        fields = '__all__'
        read_only_fields = ['id', 'create_datetime', 'update_datetime', 'creator', 'modifier',
                            'dispatched', 'dispatched_at', 'status', 'result_message', 'executed_at']


class _EELogCommandCreateSerializer(CustomModelSerializer):
    class Meta:
        model = LogCommand
        fields = '__all__'
        read_only_fields = ['id', 'create_datetime', 'update_datetime', 'creator', 'modifier',
                            'dispatched', 'dispatched_at', 'status', 'result_message', 'executed_at']


class _EELogCommandUpdateSerializer(CustomModelSerializer):
    """Update Ser — 允许仅改动 action/description 等可写字段"""
    class Meta:
        model = LogCommand
        fields = '__all__'
        read_only_fields = ['id', 'create_datetime', 'update_datetime', 'creator', 'modifier',
                            'dispatched', 'dispatched_at', 'status', 'result_message', 'executed_at']


# =========================================================================
# TaskCenter — 1 unified entry Item Ser (EE 保持字段一致)
# =========================================================================
class _EETaskCenterItemSerializer(serializers.Serializer):
    """EE 侧 TaskCenter 聚合条目 DTO Ser"""
    item_type = serializers.CharField(read_only=True)
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True, allow_null=True)
    target_name = serializers.CharField(read_only=True)
    schedule_type = serializers.CharField(read_only=True)
    schedule_type_display = serializers.CharField(read_only=True)
    cron_expression = serializers.CharField(read_only=True, allow_null=True)
    interval_seconds = serializers.IntegerField(read_only=True, allow_null=True)
    run_once_at = serializers.DateTimeField(read_only=True, allow_null=True)
    status = serializers.IntegerField(read_only=True)
    status_display = serializers.CharField(read_only=True)
    last_exec_time = serializers.DateTimeField(read_only=True, allow_null=True)
    next_exec_time = serializers.DateTimeField(read_only=True, allow_null=True)
    last_exec_result = serializers.CharField(read_only=True, allow_null=True)
    last_exec_result_display = serializers.CharField(read_only=True, allow_null=True)
    exec_count = serializers.IntegerField(read_only=True)
    creator_name = serializers.CharField(read_only=True)
    create_datetime = serializers.DateTimeField(read_only=True)
    script_id = serializers.IntegerField(read_only=True, allow_null=True)


# =========================================================================
# ContactLead — 1 Ser
# =========================================================================
class _EEContactLeadSerializer(CustomModelSerializer):
    """EE 侧 ContactLead Ser — 保持 taurus 版本字段完全一致"""
    name = serializers.CharField(max_length=64, required=True, allow_blank=False, help_text="联系人姓名")
    company = serializers.CharField(max_length=128, required=False, allow_blank=True, default='')
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True, default='',
                                  help_text="联系电话 (国际区号可选)")
    email = serializers.EmailField(max_length=128, required=True, help_text="邮箱")
    scale = serializers.ChoiceField(choices=['startup', 'small', 'mid', 'large', 'enterprise'],
                                    default='small', required=False)
    message = serializers.CharField(required=False, allow_blank=True, default='', max_length=4000)

    class Meta:
        model = ContactLead
        fields = ('id', 'name', 'company', 'phone', 'email', 'scale', 'message',
                  'source', 'ip', 'user_agent', 'create_datetime')
        read_only_fields = ('id', 'source', 'ip', 'user_agent', 'create_datetime')


# =========================================================================
# 6 扩展中心 Thin-Wrapper 占位 Ser（M2.5：FeatureCode 空壳 UI，无需模型）
# =========================================================================
class _EEKnowledgeBasePlaceholderSerializer(CustomModelSerializer):
    """知识库占位 Ser（M2.5 空壳）"""
    class Meta:
        model = ContactLead  # 仅借用一个存在的 model，用于 CustomModelSerializer Meta 合法；实际不用
        fields = ('id',)
        read_only_fields = ('id',)


class _EEInspectionCenterPlaceholderSerializer(_EEKnowledgeBasePlaceholderSerializer):
    """巡检中心占位 Ser（M2.5 空壳）"""
    pass


class _EEToolsCenterPlaceholderSerializer(_EEKnowledgeBasePlaceholderSerializer):
    """工具中心占位 Ser（M2.5 空壳）"""
    pass


class _EEBackupRestoreCenterPlaceholderSerializer(_EEKnowledgeBasePlaceholderSerializer):
    """备份恢复中心占位 Ser（M2.5 空壳）"""
    pass


class _EEDownloadCenterPlaceholderSerializer(_EEKnowledgeBasePlaceholderSerializer):
    """客户端打包下载中心占位 Ser（M2.5 空壳）"""
    pass
