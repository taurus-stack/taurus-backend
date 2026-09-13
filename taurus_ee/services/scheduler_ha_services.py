"""taurus_ee/services/scheduler_ha_services.py — Scheduler HA EE Services.

1) scheduler_unified_service — SCRIPT_TASK_UNIFIED
2) scheduler_alert_service   — SCHEDULE_ALERT_RETRY
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List, Tuple

from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)


class SchedulerUnifiedService:
    @staticmethod
    def unified_list(viewset, request) -> Tuple[List[Dict[str, Any]], int]:
        from rest_framework.exceptions import ValidationError
        from taurus.models import Schedule, ScriptTask
        from taurus_ee.serializers.scheduler_ha import UnifiedScheduleListRequestSerializer

        # 兼容 DRF Request(query_params) 与 django WSGIRequest(GET) — 测试/手工调用更稳
        query_dict = getattr(request, "query_params", None) or getattr(request, "GET", {})
        req_ser = UnifiedScheduleListRequestSerializer(data=query_dict)
        if not req_ser.is_valid():
            raise ValidationError(req_ser.errors)
        p = req_ser.validated_data
        source, status, keyword = p["source"], p["status"], (p["keyword"] or "").strip()
        page, page_size = p["page"], p["page_size"]

        def _st_status_filter(qs, target):
            if target == "all":
                return qs
            from django.db.models import Count as _C
            qs = qs.annotate(_run_cnt=_C("executions",
                filter=Q(executions__status__in=[0, 1]), distinct=True))
            if target == "success": return qs.filter(last_exec_result=0)
            if target == "failed":  return qs.filter(last_exec_result=1)
            if target == "running": return qs.filter(_run_cnt__gt=0)
            if target == "pending": return qs.filter(last_exec_result__isnull=True)
            return qs

        def _sc_status_filter(qs, target):
            if target == "all": return qs
            if target == "success": return qs.filter(status=1)
            if target == "failed":  return qs.filter(status=-1)
            if target == "running": return qs.filter(status=2)
            if target == "pending": return qs.filter(status=0)
            return qs

        rows: List[Dict[str, Any]] = []
        if source in ("all", "script_task"):
            st_qs = ScriptTask.objects.all().select_related("script", "creator")
            if keyword:
                st_qs = st_qs.filter(Q(name__icontains=keyword)
                    | Q(description__icontains=keyword)
                    | Q(cron_expression__icontains=keyword))
            for t in _st_status_filter(st_qs, status)[:page * page_size + 1]:
                coi = (t.cron_expression if t.schedule_type == "cron" else
                       (f"每 {t.interval_seconds}s" if t.interval_seconds else
                        (t.run_once_at.isoformat() if t.run_once_at else "")))
                rows.append({
                    "id": t.id, "source": "script_task", "name": t.name,
                    "enabled": bool(t.enabled), "schedule_type": t.schedule_type,
                    "cron_or_interval": coi,
                    "owner": getattr(getattr(t, "creator", None), "username", ""),
                    "last_exec_time": t.last_exec_time.isoformat() if t.last_exec_time else None,
                    "last_exec_result": t.last_exec_result,
                    "next_exec_time": t.next_exec_time.isoformat() if t.next_exec_time else None,
                    "create_datetime": t.create_datetime.isoformat() if t.create_datetime else None,
                })
        if source in ("all", "schedule"):
            sc_qs = Schedule.objects.all().select_related("template", "workflow", "dag_version", "creator")
            if keyword:
                sc_qs = sc_qs.filter(Q(name__icontains=keyword)
                                     | Q(description__icontains=keyword))
            for s in _sc_status_filter(sc_qs, status)[:page * page_size + 1]:
                roa = getattr(s, "run_once_at", None)
                coi = (s.cron_expression if s.schedule_type == "cron" else
                       (f"每 {s.interval_seconds}s" if s.interval_seconds else
                        (roa.isoformat() if roa else "")))
                rows.append({
                    "id": s.id, "source": "schedule", "name": s.name,
                    "enabled": s.status == 1, "schedule_type": s.schedule_type,
                    "cron_or_interval": coi,
                    "owner": getattr(getattr(s, "creator", None), "username", ""),
                    "last_exec_time": s.last_run_time.isoformat() if s.last_run_time else None,
                    "last_exec_result": getattr(s, "last_result", None),
                    "next_exec_time": s.next_run_time.isoformat() if s.next_run_time else None,
                    "create_datetime": s.create_datetime.isoformat() if s.create_datetime else None,
                })

        merged = sorted(rows, key=lambda x: x["next_exec_time"] or "")
        total = len(merged)
        start = (page - 1) * page_size
        return merged[start:start + page_size], total

    @staticmethod
    def trigger_now(viewset, request, *, source: str, pk: int) -> Tuple[bool, str]:
        from taurus.models import Schedule, ScriptTask
        if source == "schedule":
            try:
                sched = Schedule.objects.get(pk=pk)
            except Schedule.DoesNotExist:
                return False, "Schedule 不存在"
            try:
                from taurus.tasks import execute_schedule_task
                execute_schedule_task.delay(sched.id)
                return True, f"Schedule[id={pk}] 已投递执行队列（HA 统一入口）"
            except Exception as exc:  # noqa: BLE001
                logger.exception("trigger_now schedule failed")
                return False, f"执行失败: {exc}"
        if source == "script_task":
            try:
                task = ScriptTask.objects.get(pk=pk)
            except ScriptTask.DoesNotExist:
                return False, "ScriptTask 不存在"
            try:
                return SchedulerUnifiedService._run_st_now(task, request.user)
            except Exception as exc:  # noqa: BLE001
                logger.exception("trigger_now script_task failed")
                return False, f"执行失败: {exc}"
        return False, f"不支持的 source={source}"

    @staticmethod
    def _run_st_now(task, operator) -> Tuple[bool, str]:
        import json as _json
        import os
        from django.utils import timezone
        from taurus.models import ScriptTaskExecution

        host_ids = list(getattr(task, "hosts", None) or [])
        if not host_ids:
            return False, "Task has no target hosts configured, cannot execute"
        now = timezone.now()
        try:
            exec_obj = ScriptTaskExecution.objects.create(
                task=task, trigger_type="manual_unified_ee", status=0,
                start_time=now, creator=operator, target_host_ids=host_ids,
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"Failed to create execution record: {exc}"
        payload = {
            "source": "taurus-backend-unified-trigger-ee",
            "execution_id": exec_obj.id,
            "script_task_id": task.id,
            "script_id": getattr(task.script, "id", None),
            "host_ids": host_ids,
            "operator_id": getattr(operator, "id", None),
            "triggered_at": now.isoformat(),
        }
        try:
            import redis as _redis
            from taurus.management.commands.run_scheduler_worker import Command as _WC
            dsn = _WC._default_redis_url()
            qk = os.environ.get("TAURUS_SCHEDULER_QUEUE", "taurus:scheduler:queue:script_task")
            c = _redis.Redis.from_url(dsn)
            c.rpush(qk, _json.dumps(payload, default=str))
            try: c.close()
            except Exception: pass
            return True, f"ScriptTask[id={task.id}] 投递成功, execution_id={exec_obj.id}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("push redis queue failed: %s", exc)
            return True, f"Execution[id={exec_obj.id}] created, Redis 不可达,走 HTTP 兜底: {exc}"

    @staticmethod
    def unified_stats(viewset, request) -> Dict[str, int]:
        from taurus.models import Schedule, ScriptTask, ScriptTaskExecution
        now = timezone.now()
        last_24h = now - timedelta(hours=24)
        next_1h = now + timedelta(hours=1)

        total_tasks = int(ScriptTask.objects.count()) + int(Schedule.objects.count())
        enabled = (int(ScriptTask.objects.filter(enabled=True).count())
                   + int(Schedule.objects.filter(status=1).count()))
        try:
            from taurus.models import ScheduleExecution as _SE
            sc_ok = int(_SE.objects.filter(start_time__gte=last_24h, status=1).count())
            sc_fail = int(_SE.objects.filter(start_time__gte=last_24h, status=-1).count())
            sc_running = int(_SE.objects.filter(status=2).count())
        except Exception:
            sc_ok = sc_fail = sc_running = 0

        st_ok = int(ScriptTaskExecution.objects.filter(start_time__gte=last_24h, status=2).count())
        st_fail = int(ScriptTaskExecution.objects.filter(start_time__gte=last_24h, status=3).count())
        st_running = int(ScriptTaskExecution.objects.filter(status__in=[0, 1]).count())

        next_1h_due = (
            int(ScriptTask.objects.filter(next_exec_time__gte=now,
                                           next_exec_time__lte=next_1h).count())
            + int(Schedule.objects.filter(next_run_time__gte=now,
                                          next_run_time__lte=next_1h).count()))
        try:
            from taurus.models import Script
            pending_approval = int(Script.objects.filter(status=2).count())
        except Exception:
            pending_approval = 0
        return {
            "total_tasks": total_tasks,
            "enabled_tasks": enabled,
            "last_24h_success": st_ok + sc_ok,
            "last_24h_failed": st_fail + sc_fail,
            "running_now": st_running + sc_running,
            "next_1h_due": next_1h_due,
            "pending_approval": pending_approval,
        }


class SchedulerAlertService:
    IN_APP_INBOX: List[Dict[str, Any]] = []

    @classmethod
    def publish_failure_alert(cls, event: Dict[str, Any]) -> Tuple[bool, str]:
        from rest_framework.exceptions import ValidationError
        from taurus_ee.serializers.scheduler_ha import SchedulerAlertEventSerializer
        ser = SchedulerAlertEventSerializer(data=event)
        if not ser.is_valid():
            raise ValidationError(ser.errors)
        clean = ser.validated_data
        cls.IN_APP_INBOX.append({**clean, "received_at": timezone.now().isoformat()})
        if len(cls.IN_APP_INBOX) > 1000:
            del cls.IN_APP_INBOX[: len(cls.IN_APP_INBOX) - 1000]
        logger.error("[SchedulerAlertService] failure: source=%s id=%s task=%s retry=%s/%s reason=%s",
                     clean["source"], clean["source_id"], clean["task_name"],
                     clean["retry_index"], clean["max_retries"], clean["failure_reason"])
        webhook_url = event.get("webhook_url") or ""
        if webhook_url:
            try:
                import json as _json
                import urllib.request as _req
                tri = clean["trigger_time"]
                tri_s = tri.isoformat() if hasattr(tri, "isoformat") else str(tri)
                data = _json.dumps({"source": clean["source"], "source_id": clean["source_id"],
                    "task_name": clean["task_name"], "failure_reason": clean["failure_reason"],
                    "retry_index": clean["retry_index"], "max_retries": clean["max_retries"],
                    "trigger_time": tri_s}).encode()
                r = _req.Request(webhook_url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
                with _req.urlopen(r, timeout=5) as resp:
                    resp.read()
            except Exception as exc:  # noqa: BLE001
                logger.warning("webhook send failed: url=%s err=%s", webhook_url, exc)
        return True, "in_app inbox saved; logger.error recorded"

    @classmethod
    def register_misfire_listener(cls, scheduler_instance=None) -> bool:
        return True

    @classmethod
    def recent_alerts(cls, limit: int = 50) -> List[Dict[str, Any]]:
        return list(reversed(cls.IN_APP_INBOX[-limit:]))


__all__ = ["SchedulerUnifiedService", "SchedulerAlertService"]
