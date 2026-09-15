"""taurus_ee.serializers.supervisor_program — M2.4 Supervisor 程序管理高级 Serializers.

迁移原则（与 M2.1/M2.2 一致）：
  · Models 永远停在 taurus.models.py，单 Schema 策略，这里只复写 Serializer。
  · 相较 CE 原版薄封装：在 EE 版本中保留字段兼容，同时增加：
      - preview_hosts / apply_to_hosts / batch_create / batch_create_command 等动作 Request Ser
      - PolicyMatchPreviewSerializer / ProgramUpgradeRolloutKpiSerializer 等 EE 专属 KPI/预览 Ser
  · Thin Wrapper 模式：taurus.serializers.N 同名类 pass 继承本文件对应类。
"""
from __future__ import annotations

from typing import Any, Dict, List

from rest_framework import serializers

from taurus.models import (
    Host,
    ProgramCommand,
    ProgramHostBinding,
    ProgramInstallConfig,
    ProgramInstallPolicy,
    ProgramInstallTemplate,
)
from dvadmin.utils.serializers import CustomModelSerializer
from dvadmin.utils.validator import CustomUniqueValidator


# ==========================================================================
# ProgramInstallTemplate (EE: F_PROGRAM_INSTALL_TEMPLATE)
# ==========================================================================
class _EEProgramInstallTemplateSerializer(CustomModelSerializer):
    """Program installtemplate Serializer — EE 版额外附带 bound_hosts_count."""
    bound_hosts_count = serializers.SerializerMethodField()

    def get_bound_hosts_count(self, obj) -> int:
        return obj.host_bindings.count()

    class Meta:
        model = ProgramInstallTemplate
        fields = "__all__"
        read_only_fields = ["id", "create_datetime", "update_datetime"]


class _EEProgramInstallTemplateCreateSerializer(CustomModelSerializer):
    name = serializers.CharField(
        max_length=255,
        validators=[CustomUniqueValidator(
            queryset=ProgramInstallTemplate.objects.all(),
            message="Template name must be unique",
        )],
    )

    class Meta:
        model = ProgramInstallTemplate
        fields = "__all__"
        read_only_fields = ["id", "create_datetime", "update_datetime"]


class _EEProgramInstallTemplateUpdateSerializer(CustomModelSerializer):
    class Meta:
        model = ProgramInstallTemplate
        fields = "__all__"
        read_only_fields = ["id", "create_datetime", "update_datetime", "creator"]


# ==========================================================================
# ProgramHostBinding (EE: F_PROGRAM_HOST_BINDING)
# ==========================================================================
class _EEProgramHostBindingSerializer(CustomModelSerializer):
    host_name = serializers.CharField(source="host.host_name", read_only=True)
    host_ip = serializers.CharField(source="host.host_ip", read_only=True)
    template_name = serializers.CharField(source="template.name", read_only=True)
    program_name = serializers.CharField(source="template.program_name", read_only=True)
    template_version = serializers.CharField(source="template.version", read_only=True)

    class Meta:
        model = ProgramHostBinding
        fields = "__all__"
        read_only_fields = ["id", "create_datetime", "update_datetime"]


class _EEProgramHostBindingCreateSerializer(CustomModelSerializer):
    class Meta:
        model = ProgramHostBinding
        fields = "__all__"
        read_only_fields = ["id", "installed", "create_datetime", "update_datetime"]


class _EEProgramHostBindingUpdateSerializer(CustomModelSerializer):
    class Meta:
        model = ProgramHostBinding
        fields = "__all__"
        read_only_fields = ["id", "host", "template", "create_datetime", "update_datetime", "creator"]


# ==========================================================================
# ProgramInstallConfig (EE: F_PROGRAM_INSTALL_CONFIG)
# ==========================================================================
class _EEProgramInstallConfigSerializer(CustomModelSerializer):
    host_name = serializers.CharField(source="host.host_name", read_only=True)
    host_ip = serializers.CharField(source="host.host_ip", read_only=True)

    class Meta:
        model = ProgramInstallConfig
        fields = "__all__"
        read_only_fields = ["id", "create_datetime", "update_datetime"]


class _EEProgramInstallConfigCreateSerializer(CustomModelSerializer):
    class Meta:
        model = ProgramInstallConfig
        fields = "__all__"
        read_only_fields = ["id", "installed", "create_datetime", "update_datetime"]


class _EEProgramInstallConfigUpdateSerializer(CustomModelSerializer):
    class Meta:
        model = ProgramInstallConfig
        fields = "__all__"
        read_only_fields = ["id", "installed", "create_datetime", "update_datetime", "creator"]


# ==========================================================================
# ProgramCommand (EE 版: 基础 CRUD 共用 CE, batch_create F_PROGRAM_COMMAND_BATCH 走 Gate)
# ==========================================================================
class _EEProgramCommandSerializer(CustomModelSerializer):
    host_name = serializers.CharField(source="host.host_name", read_only=True)
    host_ip = serializers.CharField(source="host.host_ip", read_only=True)
    action_display = serializers.CharField(source="get_action_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = ProgramCommand
        fields = "__all__"
        read_only_fields = ["id", "create_datetime", "update_datetime"]


class _EEProgramCommandCreateSerializer(CustomModelSerializer):
    class Meta:
        model = ProgramCommand
        fields = "__all__"
        read_only_fields = [
            "id", "status", "result_message",
            "executed_at", "create_datetime", "update_datetime",
        ]


class _EEProgramCommandUpdateSerializer(CustomModelSerializer):
    class Meta:
        model = ProgramCommand
        fields = "__all__"
        read_only_fields = [
            "id", "host", "program_name", "action",
            "create_datetime", "update_datetime", "creator",
        ]


# ==========================================================================
# ProgramInstallPolicy (EE: F_PROGRAM_INSTALL_POLICY)
# ==========================================================================
class _EEProgramInstallPolicySerializer(CustomModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = ProgramInstallPolicy
        fields = "__all__"
        read_only_fields = [
            "id", "matched_hosts_count", "applied_hosts_count",
            "create_datetime", "update_datetime",
        ]


class _EEProgramInstallPolicyCreateSerializer(CustomModelSerializer):
    class Meta:
        model = ProgramInstallPolicy
        fields = "__all__"
        read_only_fields = [
            "id", "matched_hosts_count", "applied_hosts_count",
            "create_datetime", "update_datetime",
        ]


class _EEProgramInstallPolicyUpdateSerializer(CustomModelSerializer):
    class Meta:
        model = ProgramInstallPolicy
        fields = "__all__"
        read_only_fields = [
            "id", "matched_hosts_count", "applied_hosts_count",
            "create_datetime", "update_datetime", "creator",
        ]


# ==========================================================================
# --------------  EE 专属：Request / KPI / Preview DTOs  -----------------
# ==========================================================================
class _EEApplyTemplateToHostsRequestSerializer(serializers.Serializer):
    """ScheduleViewSet.apply_to_hosts EE 入口合法 body."""
    host_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1)
    auto_install = serializers.BooleanField(default=False)


class _EEBatchInstallConfigRequestSerializer(serializers.Serializer):
    """ProgramInstallConfigViewSet.batch_create EE 入口合法 body."""
    host_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1)
    template_id = serializers.IntegerField(required=False, default=None)
    program_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    version = serializers.CharField(max_length=50, required=False, allow_blank=True)
    config = serializers.DictField(required=False, default=dict)
    auto_start = serializers.BooleanField(required=False, default=True)
    user = serializers.CharField(max_length=50, required=False, allow_blank=True)
    group = serializers.CharField(max_length=50, required=False, allow_blank=True)


class _EEBatchProgramCommandRequestSerializer(serializers.Serializer):
    """ProgramCommandViewSet.batch_create EE Gate 合法 body（len(host_ids) > 1）."""
    host_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1)
    program_name = serializers.CharField(max_length=100)
    action = serializers.ChoiceField(
        choices=["install", "upgrade", "start", "stop", "restart", "remove"])
    target_version = serializers.CharField(max_length=50, required=False, allow_blank=True)
    config = serializers.DictField(required=False, default=dict)


class _EEPolicyUpgradeRequestSerializer(serializers.Serializer):
    """ProgramInstallPolicyViewSet.upgrade_version EE 入口 body."""
    version = serializers.CharField(max_length=50, min_length=1)


# --- 对外导出：15 Program*(×3) + 4 EE Request DTOs = 19 classnames ---
__all__ = [
    "_EEProgramInstallTemplateSerializer",
    "_EEProgramInstallTemplateCreateSerializer",
    "_EEProgramInstallTemplateUpdateSerializer",
    "_EEProgramHostBindingSerializer",
    "_EEProgramHostBindingCreateSerializer",
    "_EEProgramHostBindingUpdateSerializer",
    "_EEProgramInstallConfigSerializer",
    "_EEProgramInstallConfigCreateSerializer",
    "_EEProgramInstallConfigUpdateSerializer",
    "_EEProgramCommandSerializer",
    "_EEProgramCommandCreateSerializer",
    "_EEProgramCommandUpdateSerializer",
    "_EEProgramInstallPolicySerializer",
    "_EEProgramInstallPolicyCreateSerializer",
    "_EEProgramInstallPolicyUpdateSerializer",
    "_EEApplyTemplateToHostsRequestSerializer",
    "_EEBatchInstallConfigRequestSerializer",
    "_EEBatchProgramCommandRequestSerializer",
    "_EEPolicyUpgradeRequestSerializer",
]
