"""
Taurus Scheduler Worker - Consumes dispatch tasks from Redis queue
Runs as an independent process (python manage.py run_scheduler_worker)
Responsibilities:
  1. BRPOP to fetch tasks from Redis queue
  2. Assemble execution context from Script / ScriptTask
  3. Create OpsExecution record for each target host (reuses existing execution chain)
  4. Optionally: directly call executor execution via Taurus SDK (gRPC)
  5. Write back ScriptTaskExecution success/failure status

Design target: independent from Django Web / uWSGI/Gunicorn process, decoupled deployment
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
import traceback
import uuid
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse

import redis

from django.core.management.base import BaseCommand
from django.utils import timezone

from taurus.utils import map_script_type

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Taurus Scheduler Worker - Consumes Redis queue and triggers ScriptTask into actual script execution"

    def add_arguments(self, parser):
        parser.add_argument(
            "--queue",
            default=os.environ.get("TAURUS_SCHEDULER_QUEUE", "taurus:scheduler:queue:script_task"),
            help="Redis queue key (ScriptTask)",
        )
        parser.add_argument(
            "--workflow-queue",
            default=os.environ.get("TAURUS_SCHEDULER_WORKFLOW_QUEUE", "taurus:scheduler:queue:workflow"),
            help="Redis workflow queue key",
        )
        parser.add_argument(
            "--redis-url",
            default=os.environ.get("REDIS_URL_SCHEDULER") or self._default_redis_url(),
            help="Redis connection URL",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=int(os.environ.get("WORKER_CONCURRENCY", "4")),
            help="Number of concurrent worker threads for task processing",
        )
        parser.add_argument(
            "--poll-timeout",
            type=int,
            default=5,
            help="BRPOP timeout in seconds (used when prefetch=1)",
        )
        parser.add_argument(
            "--prefetch",
            type=int,
            default=int(os.environ.get("WORKER_PREFETCH", "1")),
            help="Number of tasks to prefetch from queue at a time (uses LPOP batch read when >1)",
        )
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=float(os.environ.get("WORKER_POLL_INTERVAL", "0.0")),
            help="Poll interval in seconds when queue is empty (used when prefetch>1)",
        )

    @staticmethod
    def _default_redis_url() -> str:
        """Derive Redis URL from settings (shared with channels/cache),
        if derived URL has no password, automatically inject REDIS_PASSWORD / DATABASE_PASSWORD."""
        from django.conf import settings
        loc = getattr(settings, "CACHES", {}).get("redis", {}).get("LOCATION", None)
        if isinstance(loc, str) and loc.startswith("redis://"):
            url = loc.replace("redis://", "redis://").rstrip("/") + "/2"
        elif isinstance(loc, list) and loc:
            url = loc[0] + "/2"
        else:
            url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/2")
        return Command._ensure_redis_password(url)

    @staticmethod
    def _ensure_redis_password(url: str) -> str:
        """If URL has no password, try to add password by priority:
        1. Environment variable REDIS_PASSWORD
        2. Environment variable DATABASE_PASSWORD
        3. Django settings.DATABASE_PASSWORD (hardcoded default in conf/env.py)
        4. Django settings.CACHES redis-related PASSWORD (if exists)"""
        try:
            parsed = urlparse(url)
            if parsed.password or ":" in (parsed.netloc.split("@")[0] if "@" in parsed.netloc else ""):
                if parsed.password:
                    return url
            pwd = (
                os.environ.get("REDIS_PASSWORD")
                or os.environ.get("DATABASE_PASSWORD")
            )
            if not pwd:
                try:
                    from django.conf import settings as dj_settings
                    pwd = getattr(dj_settings, "DATABASE_PASSWORD", None)
                    if not pwd:
                        cache_opts = getattr(dj_settings, "CACHES", {}).get("redis", {})
                        pwd = cache_opts.get("OPTIONS", {}).get("PASSWORD") or cache_opts.get("PASSWORD")
                except Exception:
                    pwd = None
            if not pwd:
                return url
            if "@" in parsed.netloc:
                user_part, host_part = parsed.netloc.split("@", 1)
                if ":" in user_part:
                    user = user_part.split(":", 1)[0]
                else:
                    user = user_part or ""
                new_netloc = f"{user}:{pwd}@{host_part}"
            else:
                new_netloc = f":{pwd}@{parsed.netloc}"
            return urlunparse(parsed._replace(netloc=new_netloc))
        except Exception:
            return url

    @staticmethod
    def _mask_redis_url(url: str) -> str:
        """Mask Redis URL for log display (replace password with ***)"""
        try:
            parsed = urlparse(url)
            if not parsed.password:
                return url
            if "@" not in parsed.netloc:
                return url
            user_part, host_part = parsed.netloc.split("@", 1)
            if ":" in user_part:
                user = user_part.split(":", 1)[0]
            else:
                user = ""
            masked_netloc = f"{user}:***@{host_part}" if user else f":***@{host_part}"
            return urlunparse(parsed._replace(netloc=masked_netloc))
        except Exception:
            return re.sub(r":([^:/@]*?)@", ":***@", url, count=1)

    def handle(self, *args, **options):
        self.queue_key = options["queue"]
        self.workflow_queue_key = options.get("workflow_queue")
        raw_url = options["redis_url"]
        self.redis_url = Command._ensure_redis_password(raw_url)
        self.concurrency = max(1, options["workers"])
        self.poll_timeout = options["poll_timeout"]
        self.prefetch = max(1, options["prefetch"])
        self.poll_interval = max(0.0, options["poll_interval"])
        self._reconnect_fail_total = 0

        self._running = True
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._shutdown)

        masked_url = Command._mask_redis_url(self.redis_url)
        logger.info(
            "scheduler_worker.started",
            extra={
                "queue": self.queue_key,
                "redis": masked_url,
                "concurrency": self.concurrency,
                "prefetch": self.prefetch,
                "poll_interval": self.poll_interval,
            },
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"[SchedulerWorker] started queue={self.queue_key} "
                f"redis={masked_url} "
                f"concurrency={self.concurrency} prefetch={self.prefetch} "
                f"poll_interval={self.poll_interval}s"
            )
        )

        self.redis = redis.Redis.from_url(
            self.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=15,
            health_check_interval=30,
        )

        try:
            self.redis.ping()
            self.stdout.write(self.style.SUCCESS("[SchedulerWorker] Redis connection verified successfully ✓"))
            try:
                qlen = self.redis.llen(self.queue_key)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[SchedulerWorker] ScriptTask queue {self.queue_key} current length at startup = {qlen}"
                        + (" (scheduler may not have pushed yet, or already consumed by another worker)" if qlen == 0 else "")
                    )
                )
                if self.workflow_queue_key:
                    wqlen = self.redis.llen(self.workflow_queue_key)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"[SchedulerWorker] Workflow queue {self.workflow_queue_key} current length at startup = {wqlen}"
                        )
                    )
            except Exception:
                pass
        except Exception as e:
            self.stdout.write(
                self.style.WARNING(
                    f"[SchedulerWorker] Initial Redis connection failed: {e}. "
                    "Will keep retrying in main loop. Please check --redis-url / REDIS_URL_SCHEDULER / "
                    "REDIS_PASSWORD / DATABASE_PASSWORD environment variables."
                )
            )

        # Use asyncio + thread pool for concurrent processing
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        self._process_loop()

    # ---------------- Main loop ----------------
    def _process_loop(self) -> None:
        semaphore = asyncio.Semaphore(self.concurrency)
        last_heartbeat = time.time()
        total_consumed = 0
        queues = [self.queue_key]
        if self.workflow_queue_key and self.workflow_queue_key != self.queue_key:
            queues.append(self.workflow_queue_key)

        async def _handle_one(payload_raw: str):
            async with semaphore:
                try:
                    await self._process_single_payload(payload_raw)
                except Exception as e:
                    logger.exception("worker.task_fatal_error", extra={"error": str(e)})

        async def _handle_batch(payloads: list[str]):
            tasks = [_handle_one(raw) for raw in payloads]
            await asyncio.gather(*tasks, return_exceptions=True)

        while self._running:
            try:
                if self.prefetch == 1:
                    result = self.redis.brpop(queues, timeout=self.poll_timeout)
                    if result is None:
                        continue
                    _, raw = result
                    queue_name = result[0]
                    label = "Workflow" if queue_name == self.workflow_queue_key else "ScriptTask"
                    remaining = self.redis.llen(queue_name)
                    self.stdout.write(
                        self.style.MIGRATE_HEADING(
                            f"[SchedulerWorker] ↓ Fetched 1 {label} task (queue {queue_name} remaining ~{remaining})"
                        )
                    )
                    self._loop.run_until_complete(_handle_one(raw))
                    total_consumed += 1
                else:
                    fetched = []
                    for q in queues:
                        pipe = self.redis.pipeline(transaction=False)
                        for _ in range(self.prefetch):
                            pipe.lpop(q)
                        fetched.extend([r for r in pipe.execute() if r is not None])
                    if not fetched:
                        if self.poll_interval > 0:
                            time.sleep(self.poll_interval)
                        else:
                            time.sleep(0.01)
                        now = time.time()
                        if now - last_heartbeat >= 60:
                            try:
                                qlen = self.redis.llen(self.queue_key)
                            except Exception:
                                qlen = -1
                            wqlen = -1
                            if self.workflow_queue_key:
                                try:
                                    wqlen = self.redis.llen(self.workflow_queue_key)
                                except Exception:
                                    pass
                            self.stdout.write(
                                self.style.MIGRATE_LABEL(
                                    f"[SchedulerWorker] ♥ Heartbeat running for {int(now - last_heartbeat + 60)}s "
                                    f"| Total consumed {total_consumed} tasks "
                                    f"| ScriptTask queue {self.queue_key} current length {qlen}"
                                    + (f" | Workflow queue {self.workflow_queue_key} current length {wqlen}" if self.workflow_queue_key else "")
                                )
                            )
                            last_heartbeat = now
                        continue
                    self.stdout.write(
                        self.style.MIGRATE_HEADING(
                            f"[SchedulerWorker] ↓ Fetched {len(fetched)} tasks (prefetch={self.prefetch})"
                        )
                    )
                    self._loop.run_until_complete(_handle_batch(fetched))
                    total_consumed += len(fetched)
            except redis.ConnectionError as e:
                self._reconnect_fail_total += 1
                logger.warning("worker.redis_disconnect", extra={"error": str(e), "total_fails": self._reconnect_fail_total})
                self.stdout.write(
                    self.style.WARNING(
                        f"[SchedulerWorker] ⚠ Redis disconnected: {str(e)[:100]} (total failures {self._reconnect_fail_total})"
                    )
                )
                time.sleep(2)
                self._reconnect_redis()
            except Exception as e:
                logger.exception("worker.loop_error", extra={"error": str(e)})
                self.stdout.write(
                    self.style.ERROR(
                        f"[SchedulerWorker] ✗ Main loop exception: {type(e).__name__}: {str(e)[:150]}"
                    )
                )
                time.sleep(1)

        logger.info("scheduler_worker.stopped")
        self.stdout.write(self.style.WARNING("[SchedulerWorker] stopped"))

    def _reconnect_redis(self) -> None:
        masked_url = Command._mask_redis_url(self.redis_url)
        for i in range(5):
            try:
                self.redis = redis.Redis.from_url(
                    self.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=5,
                    socket_timeout=15,
                    health_check_interval=30,
                )
                self.redis.ping()
                logger.info("worker.redis_reconnected", extra={"after_fails": self._reconnect_fail_total})
                if self._reconnect_fail_total >= 5:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"[SchedulerWorker] ✓ Redis reconnected successfully "
                            f"(total failures {self._reconnect_fail_total})"
                        )
                    )
                self._reconnect_fail_total = 0
                return
            except Exception as e:
                self._reconnect_fail_total += 1
                logger.warning("worker.redis_reconnect_wait", extra={
                    "error": str(e), "attempt": i + 1, "total_fails": self._reconnect_fail_total,
                })
                time.sleep(2)

        if self._reconnect_fail_total % 10 == 0:
            self.stdout.write(
                self.style.ERROR(
                    f"[SchedulerWorker] ✗ Redis persistently unreachable (total failures {self._reconnect_fail_total})\n"
                    f"  Current URL = {masked_url}\n"
                    f"  Suggested checks:\n"
                    f"    1. Is Redis running? (redis-cli ping)\n"
                    f"    2. Is the password correct? (REDIS_PASSWORD / DATABASE_PASSWORD env var or --redis-url)\n"
                    f"    3. Target database number (worker uses {self.redis_url.rsplit('/', 1)[-1] or 'default db'})\n"
                    f"    4. Is REDIS_URL_SCHEDULER configured in launch.json (if started via VSCode)?"
                )
            )

    # ---------------- Task processing ----------------
    async def _process_single_payload(self, raw: str) -> None:
        """Process a dispatch message (Django ORM sync operations go to thread, avoid SynchronousOnlyOperation)"""
        await asyncio.to_thread(self._process_single_payload_sync, raw)

    def _process_single_payload_sync(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.error("worker.invalid_json", extra={"raw": raw[:500]})
            self.stdout.write(
                self.style.ERROR("[SchedulerWorker] ✗ Invalid JSON, discarded: " + raw[:100])
            )
            return

        target_type = payload.get("target_type") or "script_task"

        if target_type == "workflow":
            self._process_workflow_payload_sync(payload)
        else:
            self._process_script_task_payload_sync(payload)

    def _process_workflow_payload_sync(self, payload: dict) -> None:
        wf_info = payload.get("workflow") or {}
        exec_info = payload.get("execution") or {}
        wf_id = int(wf_info.get("id") or 0)
        execution_id = int(exec_info.get("id") or 0)
        trigger_type = exec_info.get("trigger_type") or payload.get("trigger_type") or "schedule"

        if not wf_id:
            logger.error("worker.no_workflow_id", extra={"payload_summary": {"wf": wf_info, "exec": exec_info}})
            self.stdout.write(
                self.style.ERROR("[SchedulerWorker] ✗ payload missing workflow.id, discarded")
            )
            return

        logger.info("worker.processing_workflow",
                    extra={"workflow_id": wf_id, "execution_id": execution_id, "trigger": trigger_type})
        self.stdout.write(
            self.style.MIGRATE_LABEL(
                f"[SchedulerWorker] → Processing Workflow id={wf_id} "
                f"execution_id={execution_id} trigger={trigger_type} "
                f"name={(wf_info.get('name') or '')[:40]}"
            )
        )

        try:
            from taurus.models import Workflow
            try:
                workflow = Workflow.objects.get(id=wf_id)
            except Workflow.DoesNotExist:
                logger.error("worker.workflow_not_found", extra={"workflow_id": wf_id})
                self._mark_workflow_execution(execution_id, "fail", error=f"Workflow {wf_id} not found")
                self.stdout.write(
                    self.style.ERROR(f"[SchedulerWorker] ✗ workflow_id={wf_id} not found")
                )
                return

            if workflow.status == 2:
                self._mark_workflow_execution(execution_id, "fail", error="Workflow pending approval")
                self.stdout.write(
                    self.style.ERROR(f"[SchedulerWorker] ✗ workflow_id={wf_id} workflow is pending approval, cannot execute")
                )
                return
            if workflow.status in (1, 3):
                self._mark_workflow_execution(execution_id, "fail", error="Workflow disabled or archived")
                self.stdout.write(
                    self.style.ERROR(f"[SchedulerWorker] ✗ workflow_id={wf_id} workflow is disabled or archived, cannot execute")
                )
                return

            from taurus.workflow.engine.runner import WorkflowRunner, WorkflowRunnerError
            runner = WorkflowRunner()
            try:
                trigger_result = runner.trigger_workflow(
                    workflow,
                    trigger_type=trigger_type,
                    user_id=wf_info.get("creator_id"),
                )
            except WorkflowRunnerError as e:
                logger.error("worker.workflow_trigger_error", extra={"workflow_id": wf_id, "error": str(e)})
                self._mark_workflow_execution(execution_id, "fail", error=str(e))
                self.stdout.write(
                    self.style.ERROR(f"[SchedulerWorker] ✗ workflow_id={wf_id} trigger failed: {str(e)[:150]}")
                )
                return

            result_data = {
                "execution_id": trigger_result.execution_id,
                "dag_version_id": trigger_result.dag_version_id,
                "initial_runnables": trigger_result.initial_runnables,
            }
            self._mark_workflow_execution(execution_id, "success", result=result_data)

            workflow.last_exec_time = timezone.now()
            workflow.last_exec_result = "success"
            workflow.save(update_fields=["last_exec_time", "last_exec_result"])

            logger.info("worker.workflow_dispatched",
                        extra={"workflow_id": wf_id, "execution_id": execution_id,
                               "wf_execution_id": trigger_result.execution_id})
            self.stdout.write(
                self.style.SUCCESS(
                    f"[SchedulerWorker] ✓ Workflow id={wf_id} triggered successfully"
                    + (f" | execution_id={execution_id}" if execution_id else "")
                    + f" | workflow_execution_id={trigger_result.execution_id}"
                )
            )

        except Exception as e:
            logger.exception("worker.workflow_process_error",
                             extra={"workflow_id": wf_id, "execution_id": execution_id, "error": str(e)})
            self._mark_workflow_execution(execution_id, "fail",
                                          error=f"Worker internal error: {str(e)}\n{traceback.format_exc()[:1000]}")
            self.stdout.write(
                self.style.ERROR(
                    f"[SchedulerWorker] ✗ workflow_id={wf_id} processing exception "
                    f"{type(e).__name__}: {str(e)[:150]}"
                )
            )

    def _process_script_task_payload_sync(self, payload: dict) -> None:
        task_info = payload.get("task") or {}
        exec_info = payload.get("execution") or {}
        task_id = int(task_info.get("id") or 0)
        execution_id = int(exec_info.get("id") or 0)
        trigger_type = exec_info.get("trigger_type") or payload.get("trigger_type") or "schedule"

        if not task_id:
            logger.error("worker.no_task_id", extra={"payload_summary": {
                "task": task_info, "exec": exec_info
            }})
            self.stdout.write(
                self.style.ERROR("[SchedulerWorker] ✗ payload missing task.id, discarded")
            )
            return

        logger.info("worker.processing",
                    extra={
                        "task_id": task_id, "execution_id": execution_id,
                        "trigger": trigger_type,
                        "task_name": task_info.get("name"),
                    })
        self.stdout.write(
            self.style.MIGRATE_LABEL(
                f"[SchedulerWorker] → Processing task_id={task_id} "
                f"execution_id={execution_id} trigger={trigger_type} "
                f"name={(task_info.get('name') or '')[:40]}"
            )
        )

        try:
            from taurus.models import ScriptTask, ScriptTaskExecution, Script, Host
            try:
                script_task = ScriptTask.objects.select_related("script").get(id=task_id)
            except ScriptTask.DoesNotExist:
                logger.error("worker.script_task_not_found", extra={"task_id": task_id})
                self._mark_execution(execution_id, "fail", error="ScriptTask not found")
                self.stdout.write(
                    self.style.ERROR(
                        f"[SchedulerWorker] ✗ task_id={task_id} not found (may be diagnostic/duplicate message, can be ignored)"
                    )
                )
                return

            script: Script = script_task.script
            if not script:
                self._mark_execution(execution_id, "fail", error="Associated script not found")
                self.stdout.write(
                    self.style.ERROR(f"[SchedulerWorker] ✗ task_id={task_id} associated script not found")
                )
                return

            if script.status == 2:
                self._mark_execution(execution_id, "fail", error="Script pending approval")
                self.stdout.write(
                    self.style.ERROR(f"[SchedulerWorker] ✗ task_id={task_id} script is pending approval, cannot execute")
                )
                return
            if script.status in (1, 3):
                self._mark_execution(execution_id, "fail", error="Script disabled or archived")
                self.stdout.write(
                    self.style.ERROR(f"[SchedulerWorker] ✗ task_id={task_id} script is disabled or archived, cannot execute")
                )
                return

            # Collect target hosts: ScriptTask.hosts supports multiple identifiers (host_uuid / host_ip / host_name / Host ID),
            # Query by priority to avoid missing due to frontend configured host_ip
            host_uuids = script_task.hosts or task_info.get("hosts") or []
            if isinstance(host_uuids, str):
                try:
                    host_uuids = json.loads(host_uuids)
                except Exception:
                    host_uuids = []
            host_refs = [str(x) for x in host_uuids if x]

            if not host_refs:
                self._mark_execution(execution_id, "fail",
                                     error="Task has no target hosts configured",
                                     executed_hosts=[])
                script_task.last_exec_result = "fail"
                script_task.save(update_fields=["last_exec_result"])
                return

            # Query by host_uuid / host_ip / host_name / pk in order
            from django.db.models import Q
            q = Q()
            for ref in host_refs:
                q |= Q(host_uuid__iexact=ref)
                q |= Q(host_ip=ref)
                q |= Q(host_name=ref)
                try:
                    pk = int(ref)
                    q |= Q(pk=pk)
                except (ValueError, TypeError):
                    pass
            hosts = list(Host.objects.filter(q, status=1).distinct())

            # Deduplicate by user original order (maintain order consistent with input)
            ref_to_host = {}
            for h in hosts:
                for ref in host_refs:
                    if (str(h.host_uuid).lower() == str(ref).lower()
                            or getattr(h, 'host_ip', None) == ref
                            or getattr(h, 'host_name', None) == ref
                            or str(h.pk) == str(ref)):
                        ref_to_host.setdefault(str(ref), h)
            ordered_hosts = []
            seen = set()
            for ref in host_refs:
                h = ref_to_host.get(str(ref))
                if h and h.pk not in seen:
                    ordered_hosts.append(h)
                    seen.add(h.pk)
            hosts = ordered_hosts

            if not hosts:
                self._mark_execution(execution_id, "fail",
                                     error="All target hosts not found or not approved (no Host with status=1 matched by host_uuid/host_ip/host_name/pk)",
                                     executed_hosts=host_refs)
                script_task.last_exec_result = "fail"
                script_task.save(update_fields=["last_exec_result"])
                self.stdout.write(
                    self.style.ERROR(
                        f"[SchedulerWorker] ✗ task_id={task_id} no valid target hosts found "
                        f"(refs={host_refs[:5]}{'...' if len(host_refs) > 5 else ''})"
                    )
                )
                return

            # Merge task parameters
            merged_envs = dict(script.script_envs or {})
            merged_envs.update(script_task.envs or {})
            merged_args = list(script_task.args or script.script_params or [])
            timeout = int(script_task.timeout or script.timeout or 300)

            # 2. Create OpsExecution execution record for each host (reuses existing WebSocket execution chain)
            executed_hosts_info = []
            success_count = 0
            fail_count = 0
            batch_id = f"st-{script_task.id}-{execution_id}-{int(time.time())}"

            from taurus.models import OpsExecution, settings as model_settings
            user_id = script_task.creator_id

            # === 配额校验：最大并发执行数 ===
            from taurus.editions.loader import check_quota as _check_quota
            _check_quota('max_concurrent_executions',
                         OpsExecution.objects.filter(status__in=[0, 1, 5]).count(), '并发执行任务')

            for host in hosts:
                try:
                    execution_uuid = str(uuid.uuid4())
                    OpsExecution.objects.create(
                        execution_id=execution_uuid,
                        batch_id=batch_id,
                        execution_type="script",
                        host=host,
                        user_id=user_id,
                        script_type=map_script_type(script.script_type),
                        script_content=script.content,
                        args=merged_args,
                        timeout_seconds=timeout,
                        environment=merged_envs,
                        status=0,  # Pending execution
                    )
                    executed_hosts_info.append({
                        "host_uuid": str(host.host_uuid),
                        "host_ip": host.host_ip,
                        "host_name": host.host_name,
                        "ops_execution_id": execution_uuid,
                        "status": "submitted",
                    })
                    success_count += 1
                except Exception as e:
                    fail_count += 1
                    logger.exception("worker.create_ops_exec_fail",
                                     extra={
                                         "task_id": task_id, "host": str(getattr(host, "host_uuid", "")),
                                         "error": str(e),
                                     })
                    executed_hosts_info.append({
                        "host_uuid": str(getattr(host, "host_uuid", "") or ""),
                        "host_ip": getattr(host, "host_ip", None),
                        "status": "fail",
                        "error": str(e),
                    })

            # 3. Mark ScriptTaskExecution result (ws/executor will asynchronously update OpsExecution)
            #    Mark as dispatch success first, per-host results queried by frontend via batch_id
            overall = "success" if success_count > 0 else "fail"
            # Convert UUID objects in host_refs to str (ensure JSON serializable)
            safe_hosts = [str(x) for x in host_refs]
            self._mark_execution(
                execution_id,
                overall,
                result={
                    "batch_id": batch_id,
                    "script_id": script.id,
                    "script_name": script.name,
                    "dispatched_hosts": success_count,
                    "failed_hosts": fail_count,
                    "hosts_detail": executed_hosts_info,
                    "trigger_source": trigger_type,
                },
                executed_hosts=safe_hosts,
            )

            # 4. Write back ScriptTask final execution result
            script_task.last_exec_result = overall
            script_task.last_exec_time = timezone.now()
            script_task.save(update_fields=["last_exec_result", "last_exec_time"])

            logger.info("worker.task_dispatched",
                        extra={
                            "task_id": task_id,
                            "execution_id": execution_id,
                            "batch_id": batch_id,
                            "success_count": success_count,
                            "fail_count": fail_count,
                        })
            self.stdout.write(
                self.style.SUCCESS(
                    f"[SchedulerWorker] ✓ task_id={task_id} dispatched successfully "
                    f"| execution_id={execution_id} | batch_id={batch_id} "
                    f"| hosts success={success_count} fail={fail_count}"
                    + (f" | script={script.name[:30]}" if script and script.name else "")
                )
            )

        except Exception as e:
            logger.exception("worker.process_error",
                             extra={
                                 "task_id": task_id, "execution_id": execution_id, "error": str(e),
                             })
            self._mark_execution(execution_id, "fail",
                                 error=f"Worker internal error: {str(e)}\n{traceback.format_exc()[:1000]}")
            self.stdout.write(
                self.style.ERROR(
                    f"[SchedulerWorker] ✗ task_id={task_id} processing exception "
                    f"{type(e).__name__}: {str(e)[:150]}"
                )
            )

    # ---------------- Helpers ----------------
    def _mark_execution(self, execution_id: int, status: str,
                        result: Optional[dict[str, Any]] = None,
                        error: Optional[str] = None,
                        executed_hosts: Optional[list[str]] = None) -> None:
        if not execution_id:
            return
        from taurus.models import ScriptTaskExecution
        try:
            rec = ScriptTaskExecution.objects.filter(id=execution_id).first()
            if rec is None:
                return
            rec.end_time = timezone.now()
            if rec.start_time:
                delta = rec.end_time - rec.start_time
                rec.duration = int(delta.total_seconds())
            rec.status = 2 if status == "success" else 3
            if result is not None:
                rec.result = result
            if error is not None:
                rec.error_message = error
            if executed_hosts is not None:
                rec.executed_hosts = executed_hosts
            rec.save(update_fields=["end_time", "duration", "status",
                                     "result", "error_message", "executed_hosts"])
        except Exception as e:
            logger.exception("worker.mark_execution_failed",
                             extra={"execution_id": execution_id, "error": str(e)})

    def _mark_workflow_execution(self, execution_id: int, status: str,
                                  result: Optional[dict[str, Any]] = None,
                                  error: Optional[str] = None) -> None:
        if not execution_id:
            return
        from taurus.models import ScheduleExecution
        try:
            rec = ScheduleExecution.objects.filter(id=execution_id).first()
            if rec is None:
                return
            rec.end_time = timezone.now()
            if rec.start_time:
                delta = rec.end_time - rec.start_time
                rec.duration = int(delta.total_seconds())
            rec.status = 2 if status == "success" else 3
            if result is not None:
                rec.result = result
            if error is not None:
                rec.error_message = error
            rec.save(update_fields=["end_time", "duration", "status",
                                     "result", "error_message"])
        except Exception as e:
            logger.exception("worker.mark_workflow_exec_failed",
                             extra={"execution_id": execution_id, "error": str(e)})

    def _shutdown(self, signum, frame):
        self.stdout.write(self.style.WARNING(f"[SchedulerWorker] signal {signum}, stopping..."))
        self._running = False