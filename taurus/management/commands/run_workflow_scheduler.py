"""
Taurus Workflow Scheduler - independent的WorkflowAdvanceDaemon process

职责:
  1. 周期性扫描 RUNNING Status的 WorkflowExecution, 调用 advance_workflow() AdvanceWorkflow
  2. 周期性扫描卡在 PENDING Status的 OpsExecution 僵尸任务, re-派发Execution

设计Target:
  - 解耦WorkflowAdvance与User面访问(不再DependencyUserRefresh面)
  - independent于 Django Web Process, 作为后台Daemon processrun
  - 与Frontend _light_advance_running_executions 并存, 形成双重保障
  - 复用 runner.py 已Implemented的指数退避机制, Avoid过度Polling

start方式:
  python manage.py run_workflow_scheduler --interval 3 --batch-size 50

Optional:simultaneously作为 OpsExecution 僵尸任务清扫server(--ops-reap-interval Config)
"""
from __future__ import annotations

import logging
import signal
import sys
import time
from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Taurus Workflow Scheduler - 独立推进工作流 + 清扫僵尸 PENDING 任务"

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=float,
            default=3.0,
            help="工作流推进 tick 间隔秒数（默认 3.0）",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="每个 tick 最多推进的 WorkflowExecution 数量（默认 50）",
        )
        parser.add_argument(
            "--ops-reap-interval",
            type=float,
            default=30.0,
            help="OpsExecution 僵尸清扫 tick 间隔秒数（默认 30.0）",
        )
        parser.add_argument(
            "--ops-pending-max-age",
            type=float,
            default=15.0,
            help="OpsExecution PENDING 最大存活时间秒数，超过则重新派发（默认 15.0）",
        )
        parser.add_argument(
            "--node-sync-interval",
            type=float,
            default=10.0,
            help="WorkflowNodeExecution 状态同步 tick 间隔秒数（默认 10.0）",
        )
        parser.add_argument(
            "--daemon",
            action="store_true",
            default=True,
            help="以守护模式持续运行（默认开启）",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            default=False,
            help="只执行一个 tick 后退出（用于调试）",
        )

    def handle(self, *args, **options):
        self.interval = max(0.5, float(options["interval"]))
        self.batch_size = max(1, int(options["batch_size"]))
        self.ops_reap_interval = max(1.0, float(options["ops_reap_interval"]))
        self.ops_pending_max_age = max(5.0, float(options["ops_pending_max_age"]))
        self.node_sync_interval = max(1.0, float(options.get("node_sync_interval", 10.0)))
        self.run_once = bool(options["once"])

        self._running = True
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._shutdown)

        logger.info(
            "[WorkflowScheduler] 启动 | interval=%.1fs batch_size=%d "
            "ops_reap_interval=%.1fs ops_pending_max_age=%.1fs run_once=%s pid=%d",
            self.interval, self.batch_size,
            self.ops_reap_interval, self.ops_pending_max_age,
            self.run_once, __import__("os").getpid(),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"[WorkflowScheduler] 启动 | interval={self.interval}s "
                f"| batch_size={self.batch_size} "
                f"| ops_reap_interval={self.ops_reap_interval}s "
                f"| ops_pending_max_age={self.ops_pending_max_age}s"
            )
        )

        total_ticks = 0
        total_advances = 0
        total_ops_reaped = 0
        total_nodes_synced = 0
        last_ops_reap = 0.0
        last_node_sync = 0.0

        while self._running:
            tick_start = time.monotonic()
            total_ticks += 1
            tick_num = total_ticks

            # --- phase 1:Advance RUNNING Workflow ---
            logger.info("[WorkflowScheduler] tick #%d 开始 | AdvanceWorkflowphase", tick_num)
            advanced = self._advance_running_workflows()
            total_advances += advanced
            logger.info(
                "[WorkflowScheduler] tick #%d WorkflowAdvance完成 | advanced=%d cumulative=%d",
                tick_num, advanced, total_advances,
            )

            # --- phase 2:定期清扫僵尸 OpsExecution ---
            now_mono = time.monotonic()
            reap_due = (now_mono - last_ops_reap) >= self.ops_reap_interval
            logger.info(
                "[WorkflowScheduler] tick #%d 僵尸清扫check | reap_due=%s "
                "elapsed_since_last_reap=%.1fs",
                tick_num, reap_due,
                now_mono - last_ops_reap,
            )
            if reap_due:
                reaped = self._reap_zombie_ops_executions()
                total_ops_reaped += reaped
                last_ops_reap = now_mono
                logger.info(
                    "[WorkflowScheduler] tick #%d 僵尸清扫完成 | reaped=%d cumulative=%d",
                    tick_num, reaped, total_ops_reaped,
                )

            # --- phase 3:定期同步卡住的 RUNNING WorkflowNodeExecution ---
            node_sync_due = (now_mono - last_node_sync) >= self.node_sync_interval
            if node_sync_due:
                synced = self._sync_stale_workflow_nodes()
                total_nodes_synced += synced
                last_node_sync = now_mono
                logger.info(
                    "[WorkflowScheduler] tick #%d 节.Status同步完成 | synced=%d cumulative=%d",
                    tick_num, synced, total_nodes_synced,
                )

            elapsed = time.monotonic() - tick_start
            if elapsed < self.interval:
                sleep_sec = self.interval - elapsed
                logger.info(
                    "[WorkflowScheduler] tick #%d 完成 elapsed=%.3fs | 休眠 %.3fs",
                    tick_num, elapsed, sleep_sec,
                )
                time.sleep(sleep_sec)
            else:
                logger.info(
                    "[WorkflowScheduler] tick #%d 完成 elapsed=%.3fs (timeout, Skip休眠)",
                    tick_num, elapsed,
                )

            if self.run_once:
                logger.info("[WorkflowScheduler] --once 模式，退出")
                break

            # HeartbeatLog(每 20 tick 一次)
            if total_ticks % 20 == 0:
                self.stdout.write(
                    self.style.MIGRATE_LABEL(
                        f"[WorkflowScheduler] ♥ 心跳 | ticks={total_ticks} "
                        f"| advances={total_advances} | ops_reaped={total_ops_reaped} "
                        f"| nodes_synced={total_nodes_synced}"
                    )
                )

        logger.info(
            "[WorkflowScheduler] 已停止 | ticks=%d advances=%d ops_reaped=%d nodes_synced=%d",
            total_ticks, total_advances, total_ops_reaped, total_nodes_synced,
        )
        self.stdout.write(
            self.style.WARNING(
                f"[WorkflowScheduler] 已停止 | ticks={total_ticks} "
                f"| advances={total_advances} | ops_reaped={total_ops_reaped} "
                f"| nodes_synced={total_nodes_synced}"
            )
        )

    # ------------------------------------------------------------------
    def _advance_running_workflows(self) -> int:
        """扫描 RUNNING Status的 WorkflowExecution, 逐 调用 advance_workflow().

        Returns: 本轮SuccessAdvance的 execution 数量.
        """
        from taurus.models import WorkflowExecution
        from taurus.workflow.engine.runner import WorkflowRunner

        # Branch A:Query RUNNING Executionlist
        try:
            running_executions = list(
                WorkflowExecution.objects.filter(status=1)
                .order_by("pk")
                .values_list("pk", flat=True)[: self.batch_size]
            )
        except Exception as exc:
            logger.exception(
                "[WorkflowScheduler] 查询 RUNNING 执行列表失败: %s", exc,
            )
            return 0

        logger.info(
            "[WorkflowScheduler] 查到 %d 个 RUNNING WorkflowExecution (batch_size=%d)",
            len(running_executions), self.batch_size,
        )

        # Branch B:无 RUNNING Execution, 直接return
        if not running_executions:
            logger.info("[WorkflowScheduler] 无 RUNNING 执行，跳过推进")
            return 0

        runner = WorkflowRunner()
        advanced = 0
        finished_count = 0
        failed_count = 0

        for exec_id in running_executions:
            if not self._running:
                logger.info("[WorkflowScheduler] 收到停止信号，中断推进剩余 %d 个执行", len(running_executions) - advanced)
                break

            logger.info(
                "[WorkflowScheduler] ▶ 推进 execution#%d 开始",
                exec_id,
            )
            try:
                with transaction.atomic():
                    tick = runner.advance_workflow(execution_id=exec_id)
                advanced += 1

                if tick.finished:
                    finished_count += 1
                    logger.info(
                        "[WorkflowScheduler] ✔ execution#%d Completed | polled=%d "
                        "completed=%d dispatched=%d skipped=%d",
                        exec_id, tick.polled, tick.newly_completed,
                        tick.newly_dispatched, tick.newly_skipped,
                    )
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"[WorkflowScheduler] ✓ execution#{exec_id} Completed "
                            f"(polled={tick.polled}, completed={tick.newly_completed})"
                        )
                    )
                else:
                    logger.info(
                        "[WorkflowScheduler] execution#%d Advance完成 (未结束) | polled=%d "
                        "completed=%d dispatched=%d skipped=%d",
                        exec_id, tick.polled, tick.newly_completed,
                        tick.newly_dispatched, tick.newly_skipped,
                    )
            except Exception as exc:
                failed_count += 1
                logger.warning(
                    "[WorkflowScheduler] ✘ advance execution#%d Failed | error=%s: %s",
                    exec_id, type(exc).__name__, exc, exc_info=True,
                )

        logger.info(
            "[WorkflowScheduler] 推进阶段结束 | total=%d advanced=%d "
            "finished=%d failed=%d",
            len(running_executions), advanced, finished_count, failed_count,
        )
        return advanced

    # ------------------------------------------------------------------
    def _reap_zombie_ops_executions(self) -> int:
        """扫描卡在 PENDING Status的僵尸 OpsExecution, re-派发Execution.

        判定Standard:OpsExecution.status == 0 (PENDING) 且 create_datetime
        距今超过 ops_pending_max_age 秒.这些任务从未actually被 executor Execution过.

        Returns: 本轮Successre-派发的 OpsExecution 数量.
        """
        from taurus.models import OpsExecution

        now = timezone.now()
        cutoff = now - timedelta(seconds=self.ops_pending_max_age)

        # Branch A:Query僵尸 OpsExecution
        try:
            zombie_ops = list(
                OpsExecution.objects.filter(
                    status=0,  # PENDING
                    create_datetime__lt=cutoff,
                )
                .order_by("create_datetime")
                .values_list("pk", "execution_id", "create_datetime")[: self.batch_size]
            )
        except Exception as exc:
            logger.exception(
                "[WorkflowScheduler] 查询僵尸 OpsExecution 失败: %s", exc,
            )
            return 0

        logger.info(
            "[WorkflowScheduler] 僵尸查询完成 | found=%d cutoff=%s batch_size=%d",
            len(zombie_ops), cutoff.isoformat(), self.batch_size,
        )

        # Branch B:无僵尸, 直接return
        if not zombie_ops:
            logger.info("[WorkflowScheduler] 无僵尸 OpsExecution，跳过清扫")
            return 0

        self.stdout.write(
            self.style.WARNING(
                f"[WorkflowScheduler] 发现 {len(zombie_ops)} 个僵尸 PENDING OpsExecution "
                f"(age > {self.ops_pending_max_age}s)，开始重新派发..."
            )
        )

        reaped = 0
        skipped = 0
        failed = 0

        for pk, exec_id, created_at in zombie_ops:
            if not self._running:
                logger.info(
                    "[WorkflowScheduler] 收到停止信号，中断清扫剩余 %d 个僵尸",
                    len(zombie_ops) - reaped,
                )
                break

            try:
                ops = OpsExecution.objects.select_for_update().filter(pk=pk).first()
                # Branch C:DB 行已被其他ProcessUpdate(Status不再Yes PENDING 或已被Delete)
                if ops is None or ops.status != 0:
                    skipped += 1
                    logger.info(
                        "[WorkflowScheduler] OpsExecution pk=%d id=%s 已被其他进程更新 "
                        "(status=%s)，跳过",
                        pk, exec_id, "DELETED" if ops is None else str(ops.status),
                    )
                    continue

                age_sec = (now - (ops.create_datetime or now)).total_seconds()
                logger.info(
                    "[WorkflowScheduler] ▶ 重新派发僵尸 OpsExecution pk=%d id=%s "
                    "type=%s host_id=%s age=%.1fs",
                    pk, exec_id, ops.execution_type,
                    str(ops.host_id) if ops.host else "N/A", age_sec,
                )

                # Branch D:走 _resubmit_ops_execution 重派发
                self._resubmit_ops_execution(ops)
                reaped += 1
            except Exception as exc:
                failed += 1
                logger.warning(
                    "[WorkflowScheduler] ✘ 重新派发 OpsExecution pk=%d id=%s 失败 "
                    "| error=%s: %s",
                    pk, exec_id, type(exc).__name__, exc, exc_info=True,
                )

        logger.info(
            "[WorkflowScheduler] 僵尸清扫结束 | total=%d reaped=%d "
            "skipped=%d failed=%d",
            len(zombie_ops), reaped, skipped, failed,
        )
        return reaped

    # ------------------------------------------------------------------
    def _sync_stale_workflow_nodes(self) -> int:
        """扫描 WorkflowNodeExecution 卡在 RUNNING 但其 OpsExecution Completed的僵尸节..

        判定Standard:
        - WorkflowNodeExecution.status == RUNNING (1)
        - 对应 OpsExecution.status 为Terminal state (SUCCESS=2, FAILED=3, ABORTED=4)
        - 且 OpsExecution.finished_at 距今超过 5 秒(Avoid竞态)

        这些节.本应在 OpsExecution 完成时被 _advance_workflow_for_ops_execution
        回推, 但因 WebSocket 断开, line程池Exception等原因卡住.本method作为安全网兜底.

        Returns: 本轮Success同步的节.数.
        """
        from taurus.models import OpsExecution
        from taurus.workflow.models import WorkflowNodeExecution
        from taurus.workflow.engine.schemas import (
            STATUS_CANCELLED,
            STATUS_FAILED,
            STATUS_RUNNING,
            STATUS_SUCCESS,
        )

        now = timezone.now()

        try:
            stale_rows = list(
                WorkflowNodeExecution.objects.filter(
                    status=STATUS_RUNNING,
                    node_type__in=("command", "script"),
                )
                .exclude(adapter_state={})
                .only("id", "execution_id", "node_key", "dispatch_id", "adapter_state")
                .order_by("id")[: self.batch_size]
            )
        except Exception as exc:
            logger.exception(
                "[WorkflowScheduler] 查询僵死 RUNNING 节点失败: %s", exc,
            )
            return 0

        if not stale_rows:
            logger.debug("[WorkflowScheduler] 无僵死 RUNNING 节点，跳过同步")
            return 0

        logger.info(
            "[WorkflowScheduler] 发现 %d 个 RUNNING 状态节点，开始检查对应 OpsExecution",
            len(stale_rows),
        )

        synced = 0
        ops_ids_to_check = set()
        row_map: dict[str, list[WorkflowNodeExecution]] = {}

        for row in stale_rows:
            if not isinstance(row.adapter_state, dict):
                continue
            ops_id = row.adapter_state.get("ops_execution_id")
            if ops_id:
                ops_ids_to_check.add(str(ops_id))
                row_map.setdefault(str(ops_id), []).append(row)

        if not ops_ids_to_check:
            return 0

        try:
            ops_list = list(
                OpsExecution.objects.filter(execution_id__in=ops_ids_to_check)
                .only("execution_id", "status", "exit_code", "finished_at", "error_message")
            )
        except Exception as exc:
            logger.exception(
                "[WorkflowScheduler] 批量查询 OpsExecution 失败: %s", exc,
            )
            return 0

        ops_by_id = {str(o.execution_id): o for o in ops_list}

        from taurus.workflow.units.ops_execution import _OPS_ABORTED, _OPS_FAILED, _OPS_SUCCESS

        for ops_id, rows in row_map.items():
            if not self._running:
                break

            ops = ops_by_id.get(ops_id)
            if ops is None:
                continue

            if ops.status in (0, 1):
                continue

            if ops.finished_at and (now - ops.finished_at).total_seconds() < 5:
                continue

            target_status = None
            if ops.status == _OPS_SUCCESS:
                target_status = STATUS_SUCCESS
            elif ops.status == _OPS_FAILED:
                target_status = STATUS_FAILED
            elif ops.status == _OPS_ABORTED:
                target_status = STATUS_CANCELLED
            else:
                continue

            for row in rows:
                try:
                    with transaction.atomic():
                        locked_row = WorkflowNodeExecution.objects.select_for_update().filter(
                            pk=row.pk, status=STATUS_RUNNING,
                        ).first()
                        if locked_row is None:
                            continue
                        locked_row.status = target_status
                        locked_row.finished_at = now
                        locked_row.exit_code = ops.exit_code
                        locked_row.error_message = (
                            ops.error_message or ""
                            if target_status != STATUS_SUCCESS
                            else None
                        )
                        locked_row.save(update_fields=[
                            "status", "finished_at", "exit_code",
                            "error_message", "update_datetime",
                        ])
                        synced += 1
                        logger.warning(
                            "[WorkflowScheduler] ✔ 同步僵死节点 pk=%d node_key=%s "
                            "RUNNING→%s ops_id=%s ops_status=%d",
                            row.pk, row.node_key,
                            {2: "SUCCESS", 3: "FAILED", 5: "CANCELLED"}[target_status],
                            ops_id, ops.status,
                        )
                except Exception as exc:
                    logger.warning(
                        "[WorkflowScheduler] ✘ 同步僵死节点 pk=%d 失败 ops_id=%s error=%s: %s",
                        row.pk, ops_id, type(exc).__name__, exc,
                    )

        if synced > 0:
            logger.info(
                "[WorkflowScheduler] 僵死节点同步完成 | synced=%d", synced,
            )
        return synced

    # ------------------------------------------------------------------
    @staticmethod
    def _resubmit_ops_execution(ops: Any) -> None:
        """Attempt将 PENDING 的 OpsExecution re-Commit到 executor Execution.

        优先走 ws Event loop的 submit_execution 链路, Failed则直接同步Execution.
        """
        now = timezone.now()

        # phase 1:Attempt submit_execution(ws 异步链路)
        logger.info(
            "[WorkflowScheduler] _resubmit_ops_execution pk=%d id=%s type=%s "
            "| 阶段1: 尝试 submit_execution",
            ops.pk, ops.execution_id, ops.execution_type,
        )
        submitted = False
        try:
            from taurus.websocket_async import (
                _execute_ops_async,
                get_event_loop,
                submit_execution,
            )

            loop = get_event_loop()
            if loop is None:
                logger.info(
                    "[WorkflowScheduler] _resubmit pk=%d id=%s | ws event_loop 为 None，"
                    "跳过 submit_execution",
                    ops.pk, ops.execution_id,
                )
            elif loop.is_closed():
                logger.info(
                    "[WorkflowScheduler] _resubmit pk=%d id=%s | ws event_loop 已关闭，"
                    "跳过 submit_execution",
                    ops.pk, ops.execution_id,
                )
            else:
                logger.info(
                    "[WorkflowScheduler] _resubmit pk=%d id=%s | ws event_loop 可用，"
                    "调用 submit_execution",
                    ops.pk, ops.execution_id,
                )
                submitted = submit_execution(
                    ops.execution_id, _execute_ops_async(ops.execution_id)
                )
                logger.info(
                    "[WorkflowScheduler] _resubmit pk=%d id=%s | submit_execution 返回=%s",
                    ops.pk, ops.execution_id, submitted,
                )
        except Exception as exc:
            logger.debug(
                "[WorkflowScheduler] _resubmit pk=%d id=%s | submit_execution 异常: %s: %s",
                ops.pk, ops.execution_id, type(exc).__name__, exc,
            )

        # Branch A:submit_execution Success
        if submitted:
            logger.info(
                "[WorkflowScheduler] ✔ OpsExecution pk=%d id=%s 重新派发成功 (submit_execution)",
                ops.pk, ops.execution_id,
            )
            return

        # Branch B:submit_execution Failed, 进入phase 2(同步兜底Execution)
        logger.info(
            "[WorkflowScheduler] submit_execution 失败，进入阶段2: 后台同步执行 "
            "pk=%d id=%s",
            ops.pk, ops.execution_id,
        )
        try:
            import asyncio
            import concurrent.futures
            import threading

            ops.status = 1  # RUNNING
            ops.started_at = ops.started_at or now
            ops.save(update_fields=["status", "started_at", "update_datetime"])
            logger.info(
                "[WorkflowScheduler] pk=%d id=%s 状态已更新为 RUNNING，"
                "启动后台线程执行",
                ops.pk, ops.execution_id,
            )

            def _run_sync():
                try:
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        loop = None
                    if loop and loop.is_running():
                        logger.info(
                            "[WorkflowScheduler] _run_sync id=%s | 检测到 running loop，"
                            "用 ThreadPoolExecutor 包装",
                            ops.execution_id,
                        )
                        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                            return pool.submit(
                                asyncio.run,
                                _execute_ops_async(ops.execution_id),
                            ).result(
                                timeout=(ops.timeout_seconds or 300) + 300
                            )
                    else:
                        logger.info(
                            "[WorkflowScheduler] _run_sync id=%s | 无 running loop，"
                            "直接 asyncio.run 执行",
                            ops.execution_id,
                        )
                        return asyncio.run(_execute_ops_async(ops.execution_id))
                except Exception as exc:
                    logger.exception(
                        "[WorkflowScheduler] ✘ _run_sync id=%s 异常: %s: %s",
                        ops.execution_id, type(exc).__name__, exc,
                    )
                    try:
                        ops.status = 0  # Revert到 PENDING
                        ops.save(update_fields=["status", "update_datetime"])
                        logger.info(
                            "[WorkflowScheduler] id=%s 状态已回退为 PENDING，"
                            "等待下一轮调度重试",
                            ops.execution_id,
                        )
                    except Exception:
                        logger.warning(
                            "[WorkflowScheduler] id=%s 状态回退 PENDING 失败: %s",
                            ops.execution_id, exc,
                        )

            t = threading.Thread(
                target=_run_sync,
                name=f"wf-scheduler-ops-{ops.execution_id[:8]}",
                daemon=True,
            )
            t.start()
            logger.info(
                "[WorkflowScheduler] ✔ OpsExecution pk=%d id=%s 已转为后台同步执行 "
                "| thread=%s timeout=%ds",
                ops.pk, ops.execution_id,
                t.name, (ops.timeout_seconds or 300) + 300,
            )
        except Exception as exc:
            logger.error(
                "[WorkflowScheduler] ✘ OpsExecution pk=%d id=%s 同步执行启动失败 "
                "| error=%s: %s",
                ops.pk, ops.execution_id, type(exc).__name__, exc,
            )

    def _shutdown(self, signum, frame):
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        logger.info(
            "[WorkflowScheduler] 收到 %s，正在停止... signum=%d",
            sig_name, signum,
        )
        self.stdout.write(
            self.style.WARNING(f"[WorkflowScheduler] 收到 {sig_name}，正在停止...")
        )
        self._running = False