"""Wait node adapter.

Two modes (pick one):
  - delay_seconds:  Fixed delay (seconds), dispatch computes wake_at = now() + delay_seconds
  - daily_time:      Fixed daily time (HH:mm:ss, e.g. "02:00:00"), computes next occurrence
                     If the time has already passed when reaching the node, process according to expired_strategy:
                       execute_now -> execute immediately
                       skip        -> skip this node
                       fail        -> workflow fails
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..engine.base_adapter import ExecutableUnit
from ..engine.context import WorkflowContext
from ..engine.registry import register_unit_adapter
from ..engine.schemas import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    RenderedNodeConfig,
    UnitOutput,
    ValidationResult,
)

logger = logging.getLogger(__name__)

_MAX_DELAY_SECONDS = 365 * 24 * 3600
_EXPIRED_STRATEGY_VALUES = {"execute_now", "skip", "fail"}


def _parse_daily_time(value: str) -> tuple[int, int, int] | None:
    """Parse "HH:mm" 或 "HH:mm:ss" Format, return (hour, minute, second)."""
    if not value or not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) == 2:
        parts = parts + ["0"]
    if len(parts) != 3:
        return None
    try:
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        if 0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59:
            return (h, m, s)
    except (ValueError, TypeError):
        return None
    return None


def _compute_next_daily_wake_at(daily_time: str, now: datetime) -> datetime:
    """compute下一  daily_time corresponding时刻.

    if今天该时刻已过, return明天的;No则return今天的.
    """
    hms = _parse_daily_time(daily_time)
    if hms is None:
        raise ValueError(f"daily_time format invalid: {daily_time}")
    hour, minute, second = hms

    wake = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if wake <= now:
        wake = wake + timedelta(days=1)
    return wake


@register_unit_adapter("wait")
class WaitAdapter(ExecutableUnit):
    node_type = "wait"
    display_name = "Wait"
    category = "control"
    requires_host = False
    is_asynchronous_human = False

    def validate_config(self, params: dict, *, secrets_mask=None) -> ValidationResult:
        r = ValidationResult()
        wait_mode = params.get("wait_mode", "delay")
        delay = params.get("delay_seconds")
        daily_time = params.get("daily_time")

        if isinstance(delay, str) and delay.strip() == "":
            delay = None
        if isinstance(daily_time, str) and daily_time.strip() == "":
            daily_time = None

        if wait_mode == "delay":
            daily_time = None
        elif wait_mode == "daily_time":
            delay = None
        else:
            r.add_error("/params/wait_mode", f"E0201: Invalid wait_mode, must be delay/daily_time")
            return r

        if wait_mode == "daily_time":
            if daily_time is None:
                r.add_error("/params/daily_time", "E0201: daily_time required, format HH:mm:ss")
            elif _parse_daily_time(str(daily_time)) is None:
                r.add_error("/params/daily_time", "E0201: daily_time format invalid, HH:mm:ss (e.g. 02:00:00)")

            expired_strategy = params.get("expired_strategy", "execute_now")
            if expired_strategy not in _EXPIRED_STRATEGY_VALUES:
                r.add_error(
                    "/params/expired_strategy",
                    f"E0201: expired_strategy must be in {_EXPIRED_STRATEGY_VALUES}",
                )
            if r.errors:
                return r

        elif wait_mode == "delay":
            if delay is None:
                r.add_error("/params/delay_seconds", "E0201: delay_seconds is required")
            else:
                if isinstance(delay, bool):
                    r.add_error("/params/delay_seconds", "E0201: delay_seconds must be numeric")
                elif not isinstance(delay, (int, float)):
                    try:
                        delay = float(str(delay))
                    except (TypeError, ValueError):
                        r.add_error("/params/delay_seconds", "E0201: delay_seconds must be numeric")
                        delay = None
                if delay is not None:
                    if delay <= 0:
                        r.add_error("/params/delay_seconds", "E0201: delay_seconds must be > 0")
                    elif delay > _MAX_DELAY_SECONDS:
                        r.add_error("/params/delay_seconds", f"E0201: delay_seconds must not exceed {_MAX_DELAY_SECONDS} seconds (1 year)")

        return r

    def validate_and_render(
        self,
        params: dict[str, Any],
        context: WorkflowContext,
        *,
        secrets_mask: list[str] | None = None,
    ) -> dict[str, Any]:
        return context.render_structure(params)

    def dispatch(self, cfg: RenderedNodeConfig) -> UnitOutput:
        params = cfg.params
        wait_mode = params.get("wait_mode", "delay")
        delay = params.get("delay_seconds")
        daily_time = params.get("daily_time")
        expired_strategy = params.get("expired_strategy", "execute_now")

        if isinstance(delay, str) and delay.strip() == "":
            delay = None
        if isinstance(daily_time, str) and daily_time.strip() == "":
            daily_time = None

        if wait_mode == "delay":
            daily_time = None
        elif wait_mode == "daily_time":
            delay = None

        now = timezone.now()
        now_utc = now.replace(tzinfo=dt_timezone.utc) if now.tzinfo is None else now.astimezone(dt_timezone.utc)

        if wait_mode == "daily_time":
            hms = _parse_daily_time(str(daily_time or ""))
            if hms is None:
                return UnitOutput(
                    status=STATUS_FAILED,
                    error_message="E0901: daily_time format invalid",
                    exit_code=1,
                )
            hour, minute, second = hms
            today_scheduled = now_utc.replace(hour=hour, minute=minute, second=second, microsecond=0)
            if today_scheduled <= now_utc:
                if expired_strategy == "execute_now":
                    return UnitOutput(
                        status=STATUS_SUCCESS,
                        output={"scheduled_time": today_scheduled.isoformat(), "expired": True, "expired_strategy": "execute_now"},
                        exit_code=0,
                        summary="Target time passed, executing now",
                    )
                elif expired_strategy == "skip":
                    return UnitOutput(
                        status=STATUS_SUCCESS,
                        output={"scheduled_time": today_scheduled.isoformat(), "expired": True, "expired_strategy": "skip", "skipped": True},
                        exit_code=0,
                        summary="Target time passed, skipping this node",
                    )
                else:
                    return UnitOutput(
                        status=STATUS_FAILED,
                        error_message=f"E0901: Target time {today_scheduled.isoformat()} passed, expired_strategy=fail",
                        exit_code=1,
                    )

            wake_at = _compute_next_daily_wake_at(str(daily_time), now_utc)
            return UnitOutput(
                status=STATUS_RUNNING,
                adapter_state={"wake_at": wake_at.isoformat(), "wait_mode": "daily_time", "daily_time": str(daily_time)},
                output={"wake_at": wake_at.isoformat(), "daily_time": str(daily_time)},
                summary=f"Waiting daily at {daily_time}",
            )

        elif delay is not None:
            try:
                delay_val = float(delay)
                wake_at = timezone.now() + timedelta(seconds=delay_val)
            except (TypeError, ValueError) as exc:
                return UnitOutput(
                    status=STATUS_FAILED,
                    error_message=f"E0901: delay_seconds calculation failed: {exc}",
                    exit_code=1,
                )
            wake_utc = wake_at.replace(tzinfo=dt_timezone.utc) if wake_at.tzinfo is None else wake_at.astimezone(dt_timezone.utc)
            if wake_utc <= now_utc:
                return UnitOutput(
                    status=STATUS_SUCCESS,
                    output={"waited": False, "reason": "Target time passed, no need to wait"},
                    exit_code=0,
                    summary="Target time passed, skipping wait",
                )
            return UnitOutput(
                status=STATUS_RUNNING,
                adapter_state={"wake_at": wake_at.isoformat(), "wait_mode": "delay"},
                output={"wake_at": wake_at.isoformat(), "delay_seconds": float(delay)},
                summary=f"Waiting until {wake_at.isoformat()}",
            )

        else:
            return UnitOutput(
                status=STATUS_FAILED,
                error_message="E0901: No wait parameters specified",
                exit_code=1,
            )

    def poll(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        if not adapter_state:
            return UnitOutput(status=STATUS_FAILED, error_message="E0902: adapter_state missing", exit_code=1)

        wake_at_str = adapter_state.get("wake_at")
        if not wake_at_str:
            return UnitOutput(status=STATUS_FAILED, error_message="E0902: adapter_state missing wake_at", exit_code=1)

        wake_at = parse_datetime(wake_at_str)
        if wake_at is None:
            return UnitOutput(status=STATUS_FAILED, error_message="E0902: wake_at parse failed", exit_code=1)

        now = timezone.now()
        now_utc = now.replace(tzinfo=dt_timezone.utc) if now.tzinfo is None else now.astimezone(dt_timezone.utc)
        wake_utc = wake_at.replace(tzinfo=dt_timezone.utc) if wake_at.tzinfo is None else wake_at.astimezone(dt_timezone.utc)

        if now_utc >= wake_utc:
            elapsed = (now_utc - wake_utc).total_seconds()
            return UnitOutput(
                status=STATUS_SUCCESS,
                output={
                    "wake_at": wake_at_str,
                    "woke_at": now.isoformat(),
                    "overtime_seconds": max(0.0, elapsed),
                },
                exit_code=0,
                summary="Wait completed",
            )

        remaining = (wake_utc - now_utc).total_seconds()
        return UnitOutput(
            status=STATUS_RUNNING,
            adapter_state=adapter_state,
            output={"wake_at": wake_at_str, "remaining_seconds": remaining},
            summary=f"Waiting, {remaining:.0f}s remaining",
        )

    def cancel(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        return UnitOutput(
            status=STATUS_CANCELLED,
            output={"cancelled": True, "reason": "Wait cancelled"},
            summary="Wait cancelled",
        )