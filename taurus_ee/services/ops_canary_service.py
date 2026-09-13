"""taurus_ee.services.ops_canary_service — M2.6 Ops 金丝雀灰度发布 Service (EE 专属).

三个核心算法：
  1. split_pilot_batch(hosts, pilot_count)   → 首批灰度 vs 剩余批次
  2. check_pilot_health(executions, success_rate, timeout_seconds) → 灰度执行后判定通过/失败/等待
  3. decide_final(pass_ratio, threshold_ratio, min_success) → 最终决策 proceed / abort / retry
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Tuple

from django.utils import timezone

logger = logging.getLogger(__name__)


class OpsCanaryService:
    """金丝雀灰度发布策略算法容器（纯静态类，无状态）."""

    # health check 返回值
    HEALTH_PASS = "pass"
    HEALTH_FAIL = "fail"
    HEALTH_WAITING = "waiting"

    # decide_final 返回值
    DECIDE_PROCEED = "proceed"
    DECIDE_ABORT = "abort"
    DECIDE_RETRY = "retry"

    @staticmethod
    def split_pilot_batch(
        hosts: Iterable[Any],
        pilot_count: int,
    ) -> Tuple[List[Any], List[Any]]:
        """把 hosts 分成 pilot（前 pilot_count 台）和 remaining（其余）.

        pilot_count <= 0 或 >= len(hosts) 时全部归入 remaining（等价于无灰度，全量执行）。
        """
        host_list = list(hosts or [])
        if not host_list:
            return [], []
        n = max(0, int(pilot_count or 0))
        if n <= 0 or n >= len(host_list):
            return [], host_list
        return host_list[:n], host_list[n:]

    @staticmethod
    def check_pilot_health(
        executions: Iterable[Any],
        success_rate: int,
        timeout_seconds: int = 600,
    ) -> Tuple[str, Dict[str, int]]:
        """检查灰度批次执行状态。

        executions 可以是 OpsExecution queryset 或任意提供 status 字段的对象列表。
        status mapping: 0 pending / 1 running / 2 success / 3 failed / 4 interrupted / 6 aborted

        返回 (decision, stats)：
          - decision ∈ {pass, fail, waiting}
          - stats = {total, success, failed, running, pending, interrupted}
        """
        stats = {
            "total": 0, "success": 0, "failed": 0,
            "running": 0, "pending": 0, "interrupted": 0,
        }
        for ex in executions or []:
            stats["total"] += 1
            try:
                s = ex.status if hasattr(ex, "status") else ex.get("status", 0)
            except Exception:  # noqa: BLE001
                s = 0
            if s == 2:
                stats["success"] += 1
            elif s in (3, 6):
                stats["failed"] += 1
            elif s == 1:
                stats["running"] += 1
            elif s == 4:
                stats["interrupted"] += 1
            else:
                stats["pending"] += 1

        total = stats["total"]
        if total == 0:
            return OpsCanaryService.HEALTH_WAITING, stats

        # 仍有 running / pending → 等待
        if stats["running"] + stats["pending"] > 0:
            return OpsCanaryService.HEALTH_WAITING, stats

        ratio = stats["success"] * 100.0 / total if total else 0
        threshold = max(0, min(100, int(success_rate or 100)))
        decision = OpsCanaryService.HEALTH_PASS if ratio >= threshold else OpsCanaryService.HEALTH_FAIL
        return decision, stats

    @staticmethod
    def decide_final(
        *,
        pass_ratio: float,
        threshold_ratio: float,
        min_success: int = 1,
        pilot_success: int = 0,
        pilot_total: int = 0,
    ) -> str:
        """最终决策：proceed（进入全量） / abort（终止） / retry（重试灰度）."""
        if pilot_total > 0 and pilot_success < min_success:
            return OpsCanaryService.DECIDE_RETRY
        if pass_ratio >= threshold_ratio:
            return OpsCanaryService.DECIDE_PROCEED
        return OpsCanaryService.DECIDE_ABORT

    @staticmethod
    def mark_executions(executions: Iterable[Any], *, aborted: bool = False) -> int:
        """兜底工具：批量更新 OpsExecution status → 6(aborted) 或保持不动。

        仅更新仍处于 pending(0)/running(1) 的记录，已终态(success/failed/interrupted)不动。
        返回更新条数。
        """
        updated = 0
        for ex in executions or []:
            try:
                if hasattr(ex, "status"):
                    cur = ex.status
                    if cur in (0, 1):
                        ex.status = 6 if aborted else cur
                        if aborted:
                            from django.utils import timezone as _tz
                            ex.update_datetime = _tz.now()
                            ex.save(update_fields=["status", "update_datetime"])
                            updated += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("[Canary] mark execution 失败: %s", exc)
        return updated
