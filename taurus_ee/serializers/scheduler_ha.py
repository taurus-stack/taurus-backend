"""taurus_ee/serializers/scheduler_ha.py — 调度 HA / 告警 / 统一双轨（ScriptTask + Schedule） EE 专属 Serializer.

Open Core 迁移原则（M2.3）：
  · 基础 Schedule/ScriptTask 4 个 Serializer（Schedule* / ScriptTask* 4 类）永久留在
    taurus.serializers（CE 需要 Celery Beat 单实例调度 + ScriptTask Basic）。
  · 以下 Serializer 仅 EE 使用（SCHEDULE_HA_CLUSTER / SCHEDULE_ALERT_RETRY /
    SCRIPT_TASK_UNIFIED 相关字段），迁入 taurus_ee；taurus.serializers 侧用 Thin
    Wrapper 保持引用链、路由 basename/URL 不变。
"""
from __future__ import annotations

from rest_framework import serializers

from taurus.models import ScheduleExecution, ScriptTaskExecution


# ===========================================================================
# 1. SCHEDULE_HA_CLUSTER / SCHEDULE_ALERT_RETRY — 执行记录"HA 增强字段"
# ===========================================================================
class ScheduleExecutionHASerializer(serializers.ModelSerializer):
    """Schedule execution 记录的 EE HA 视角：带 retry/alert 字段（CE 不展示）."""

    schedule_name = serializers.CharField(source="schedule.name", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    # EE 专属：HA retry / 告警元数据（在 CE ScheduleExecution 表中即便缺列，也用
    # SerializerMethodField 兜底返回 None，避免 ORM 侧炸）
    retry_count = serializers.SerializerMethodField()
    last_alert_sent_at = serializers.DateTimeField(read_only=True, default=None, allow_null=True)
    ha_node_id = serializers.CharField(read_only=True, default=None, allow_null=True)
    is_failover = serializers.SerializerMethodField()

    class Meta:
        from taurus.models import ScheduleExecution as _SE  # noqa: N814

        model = _SE
        fields = [
            "id",
            "schedule",
            "schedule_name",
            "status",
            "status_display",
            "start_time",
            "end_time",
            "retry_count",
            "last_alert_sent_at",
            "ha_node_id",
            "is_failover",
            "error_message",
            "create_datetime",
        ]
        read_only_fields = fields

    def get_retry_count(self, obj) -> int:
        return int(getattr(obj, "retry_count", 0) or 0)

    def get_is_failover(self, obj) -> bool:
        return bool(getattr(obj, "is_failover", False))


class ScriptTaskExecutionUnifiedSerializer(serializers.ModelSerializer):
    """ScriptTask + Schedule 的 EE 统一视角 Serializer（SCRIPT_TASK_UNIFIED）.

    聚合 ScriptTaskExecution / ScheduleExecution 并标注 source_type，
    供前端「统一调度列表」页合并渲染。
    """

    source_type = serializers.CharField(read_only=True, default="script_task")
    name = serializers.SerializerMethodField()
    owner_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        from taurus.models import ScriptTaskExecution as _ST  # noqa: N814

        model = _ST
        fields = [
            "id",
            "source_type",
            "name",
            "task",
            "status",
            "status_display",
            "start_time",
            "end_time",
            "owner_name",
            "error_message",
            "create_datetime",
        ]
        read_only_fields = fields

    def get_name(self, obj) -> str:
        task = getattr(obj, "task", None)
        return str(getattr(task, "name", "")) if task is not None else ""

    def get_owner_name(self, obj) -> str:
        task = getattr(obj, "task", None)
        creator = getattr(task, "creator", None)
        return getattr(creator, "username", "") if creator is not None else ""


# ===========================================================================
# 2. SCHEDULE_ALERT_RETRY — 告警触发规则 / 失败重试规则（Request Serializer）
# ===========================================================================
class SchedulerAlertRuleSerializer(serializers.Serializer):
    """ScriptTask / Schedule 级「失败告警 + 自动重试」配置（EE 专属 action payload）."""

    # Alert 相关
    enable_alert = serializers.BooleanField(default=True)
    alert_channels = serializers.ListField(
        child=serializers.ChoiceField(choices=["email", "webhook", "in_app"]),
        default=["in_app"],
    )
    alert_receiver_ids = serializers.ListField(child=serializers.IntegerField(), default=[])
    webhook_url = serializers.URLField(required=False, allow_blank=True, default="")

    # Retry 相关
    max_retries = serializers.IntegerField(min_value=0, max_value=10, default=0)
    retry_interval_seconds = serializers.IntegerField(min_value=0, max_value=86400, default=30)

    def validate(self, attrs):
        if attrs["max_retries"] > 0 and attrs["retry_interval_seconds"] <= 0:
            raise serializers.ValidationError("启用重试时 retry_interval_seconds 必须 > 0")
        if attrs["enable_alert"] and not attrs["alert_channels"]:
            raise serializers.ValidationError("启用告警时至少指定 1 个 alert_channels")
        return attrs


# ===========================================================================
# 3. SCRIPT_TASK_UNIFIED — 「双轨统一视图」请求 / 响应 DTO
# ===========================================================================
class UnifiedScheduleListRequestSerializer(serializers.Serializer):
    """ScriptTask + Schedule 合并列表（EE 专属 unified_list action）入参."""

    SOURCE_CHOICES = ["script_task", "schedule", "all"]
    STATUS_CHOICES = ["success", "failed", "running", "pending", "all"]

    source = serializers.ChoiceField(choices=SOURCE_CHOICES, default="all")
    status = serializers.ChoiceField(choices=STATUS_CHOICES, default="all")
    keyword = serializers.CharField(allow_blank=True, default="", max_length=128)
    page = serializers.IntegerField(min_value=1, default=1)
    page_size = serializers.IntegerField(min_value=1, max_value=200, default=20)


class UnifiedScheduleStatsSerializer(serializers.Serializer):
    """统一调度中心 KPI（ScriptTask × Schedule 合并）."""

    total_tasks = serializers.IntegerField()
    enabled_tasks = serializers.IntegerField()
    last_24h_success = serializers.IntegerField()
    last_24h_failed = serializers.IntegerField()
    running_now = serializers.IntegerField()
    next_1h_due = serializers.IntegerField()
    pending_approval = serializers.IntegerField(default=0)  # 审批中（脚本审批 EE 依赖）


# ===========================================================================
# 4. SCHEDULE_ALERT_RETRY — 对外 DTO：失败告警 Event（供 scheduler_alert_service publish）
# ===========================================================================
class SchedulerAlertEventSerializer(serializers.Serializer):
    """调度失败/重试事件."""

    source = serializers.ChoiceField(choices=["schedule", "script_task"])
    source_id = serializers.IntegerField()
    execution_id = serializers.IntegerField(allow_null=True, default=None)
    task_name = serializers.CharField(max_length=256)
    failure_reason = serializers.CharField(allow_blank=True, default="")
    retry_index = serializers.IntegerField(min_value=0, default=0)
    max_retries = serializers.IntegerField(min_value=0, default=0)
    trigger_time = serializers.DateTimeField(format="iso-8601")


__all__ = [
    "ScheduleExecutionHASerializer",
    "ScriptTaskExecutionUnifiedSerializer",
    "SchedulerAlertRuleSerializer",
    "UnifiedScheduleListRequestSerializer",
    "UnifiedScheduleStatsSerializer",
    "SchedulerAlertEventSerializer",
]
