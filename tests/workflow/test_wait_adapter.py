"""WaitAdapter Unit test.

覆盖两种模式:delay / daily_time.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone

import pytest
from django.utils import timezone


@pytest.fixture()
def wait_adapter():
    import taurus.workflow.units.wait  # noqa: F401
    from taurus.workflow.engine.registry import get_registry
    return get_registry().instantiate("wait")


# ──────────────────── Class Attributes ────────────────────

class TestWaitClassAttrs:
    def test_node_type(self, wait_adapter):
        assert wait_adapter.node_type == "wait"

    def test_display_name(self, wait_adapter):
        assert wait_adapter.display_name == "等待"

    def test_category_control(self, wait_adapter):
        assert wait_adapter.category == "control"

    def test_requires_host_false(self, wait_adapter):
        assert wait_adapter.requires_host is False

    def test_registered_in_registry(self):
        from taurus.workflow.engine.registry import get_registry
        reg = get_registry()
        assert reg.instantiate("wait") is not None


# ──────────────────── validate_config ────────────────────

class TestWaitValidateConfig:
    def test_delay_seconds_valid(self, wait_adapter):
        r = wait_adapter.validate_config({"wait_mode": "delay", "delay_seconds": 60})
        assert r.ok

    def test_delay_seconds_zero_invalid(self, wait_adapter):
        r = wait_adapter.validate_config({"wait_mode": "delay", "delay_seconds": 0})
        assert not r.ok

    def test_delay_seconds_negative_invalid(self, wait_adapter):
        r = wait_adapter.validate_config({"wait_mode": "delay", "delay_seconds": -1})
        assert not r.ok

    def test_delay_seconds_too_large(self, wait_adapter):
        r = wait_adapter.validate_config({"wait_mode": "delay", "delay_seconds": 366 * 24 * 3600})
        assert not r.ok

    def test_delay_seconds_string_invalid(self, wait_adapter):
        r = wait_adapter.validate_config({"wait_mode": "delay", "delay_seconds": "abc"})
        assert not r.ok

    def test_delay_seconds_missing(self, wait_adapter):
        r = wait_adapter.validate_config({"wait_mode": "delay"})
        assert not r.ok

    def test_delay_seconds_bool_invalid(self, wait_adapter):
        r = wait_adapter.validate_config({"wait_mode": "delay", "delay_seconds": True})
        assert not r.ok

    def test_wait_mode_invalid(self, wait_adapter):
        r = wait_adapter.validate_config({"wait_mode": "unknown"})
        assert not r.ok

    def test_daily_time_valid(self, wait_adapter):
        r = wait_adapter.validate_config({"wait_mode": "daily_time", "daily_time": "02:00:00"})
        assert r.ok

    def test_daily_time_valid_hhmm_backward_compat(self, wait_adapter):
        r = wait_adapter.validate_config({"wait_mode": "daily_time", "daily_time": "02:00"})
        assert r.ok

    def test_daily_time_missing(self, wait_adapter):
        r = wait_adapter.validate_config({"wait_mode": "daily_time"})
        assert not r.ok

    def test_daily_time_invalid_format(self, wait_adapter):
        r = wait_adapter.validate_config({"wait_mode": "daily_time", "daily_time": "25:00"})
        assert not r.ok

    def test_daily_time_wrong_format(self, wait_adapter):
        r = wait_adapter.validate_config({"wait_mode": "daily_time", "daily_time": "2am"})
        assert not r.ok

    def test_daily_time_expired_strategy_valid(self, wait_adapter):
        for s in ["execute_now", "skip", "fail"]:
            r = wait_adapter.validate_config({"wait_mode": "daily_time", "daily_time": "02:00:00", "expired_strategy": s})
            assert r.ok, f"strategy={s} should be valid"

    def test_daily_time_expired_strategy_invalid(self, wait_adapter):
        r = wait_adapter.validate_config({"wait_mode": "daily_time", "daily_time": "02:00:00", "expired_strategy": "unknown"})
        assert not r.ok

    def test_no_mode_at_all_defaults_delay(self, wait_adapter):
        r = wait_adapter.validate_config({"delay_seconds": 60})
        assert r.ok

    def test_no_mode_no_params(self, wait_adapter):
        r = wait_adapter.validate_config({})
        assert not r.ok


# ──────────────────── dispatch ────────────────────

class TestWaitDispatch:
    def test_delay_success(self, wait_adapter, make_cfg):
        from taurus.workflow.engine.schemas import STATUS_RUNNING
        cfg = make_cfg(params={"wait_mode": "delay", "delay_seconds": 5})
        out = wait_adapter.dispatch(cfg)
        assert out.status == STATUS_RUNNING
        assert out.adapter_state is not None
        assert "wake_at" in out.adapter_state

    def test_delay_already_past(self, wait_adapter, make_cfg):
        from taurus.workflow.engine.schemas import STATUS_SUCCESS
        cfg = make_cfg(params={"wait_mode": "delay", "delay_seconds": -0.001})
        out = wait_adapter.dispatch(cfg)
        assert out.status == STATUS_SUCCESS
        assert out.output.get("waited") is False

    def test_daily_time_future(self, wait_adapter, make_cfg):
        from taurus.workflow.engine.schemas import STATUS_RUNNING
        now = timezone.now()
        future_hour = (now.hour + 3) % 24
        if future_hour <= now.hour:
            future_hour = now.hour + 1
        daily = f"{future_hour:02d}:30:00"
        cfg = make_cfg(params={"wait_mode": "daily_time", "daily_time": daily, "expired_strategy": "execute_now"})
        out = wait_adapter.dispatch(cfg)
        assert out.status == STATUS_RUNNING
        assert out.adapter_state is not None
        assert out.adapter_state.get("wait_mode") == "daily_time"

    def test_daily_time_past_execute_now(self, wait_adapter, make_cfg):
        from taurus.workflow.engine.schemas import STATUS_SUCCESS
        now = timezone.now()
        past_hour = (now.hour - 3) % 24
        daily = f"{past_hour:02d}:30:00"
        cfg = make_cfg(params={"wait_mode": "daily_time", "daily_time": daily, "expired_strategy": "execute_now"})
        out = wait_adapter.dispatch(cfg)
        assert out.status == STATUS_SUCCESS
        assert out.output.get("expired") is True
        assert out.output.get("expired_strategy") == "execute_now"

    def test_daily_time_past_skip(self, wait_adapter, make_cfg):
        from taurus.workflow.engine.schemas import STATUS_SUCCESS
        now = timezone.now()
        past_hour = (now.hour - 3) % 24
        daily = f"{past_hour:02d}:30:00"
        cfg = make_cfg(params={"wait_mode": "daily_time", "daily_time": daily, "expired_strategy": "skip"})
        out = wait_adapter.dispatch(cfg)
        assert out.status == STATUS_SUCCESS
        assert out.output.get("skipped") is True

    def test_daily_time_past_fail(self, wait_adapter, make_cfg):
        from taurus.workflow.engine.schemas import STATUS_FAILED
        now = timezone.now()
        past_hour = (now.hour - 3) % 24
        daily = f"{past_hour:02d}:30:00"
        cfg = make_cfg(params={"wait_mode": "daily_time", "daily_time": daily, "expired_strategy": "fail"})
        out = wait_adapter.dispatch(cfg)
        assert out.status == STATUS_FAILED

    def test_daily_time_invalid_format(self, wait_adapter, make_cfg):
        from taurus.workflow.engine.schemas import STATUS_FAILED
        cfg = make_cfg(params={"wait_mode": "daily_time", "daily_time": "invalid"})
        out = wait_adapter.dispatch(cfg)
        assert out.status == STATUS_FAILED

    def test_no_params(self, wait_adapter, make_cfg):
        from taurus.workflow.engine.schemas import STATUS_FAILED
        cfg = make_cfg(params={"wait_mode": "delay"})
        out = wait_adapter.dispatch(cfg)
        assert out.status == STATUS_FAILED

    def test_daily_time_default_strategy_is_execute_now(self, wait_adapter, make_cfg):
        from taurus.workflow.engine.schemas import STATUS_SUCCESS
        now = timezone.now()
        past_hour = (now.hour - 3) % 24
        daily = f"{past_hour:02d}:30:00"
        cfg = make_cfg(params={"wait_mode": "daily_time", "daily_time": daily})
        out = wait_adapter.dispatch(cfg)
        assert out.status == STATUS_SUCCESS
        assert out.output.get("expired_strategy") == "execute_now"


# ──────────────────── poll ────────────────────

class TestWaitPoll:
    def test_poll_delay_passed(self, wait_adapter, make_cfg):
        from taurus.workflow.engine.schemas import STATUS_RUNNING, STATUS_SUCCESS
        cfg = make_cfg(params={"wait_mode": "delay", "delay_seconds": 0.01})
        out1 = wait_adapter.dispatch(cfg)
        assert out1.status == STATUS_RUNNING
        import time
        time.sleep(0.05)
        out2 = wait_adapter.poll(cfg, out1.adapter_state)
        assert out2.status == STATUS_SUCCESS

    def test_poll_delay_not_yet(self, wait_adapter, make_cfg):
        from taurus.workflow.engine.schemas import STATUS_RUNNING
        cfg = make_cfg(params={"wait_mode": "delay", "delay_seconds": 3600})
        out1 = wait_adapter.dispatch(cfg)
        assert out1.status == STATUS_RUNNING
        out2 = wait_adapter.poll(cfg, out1.adapter_state)
        assert out2.status == STATUS_RUNNING
        remaining = out2.output.get("remaining_seconds")
        assert remaining is not None
        assert remaining > 0

    def test_poll_missing_state(self, wait_adapter, make_cfg):
        from taurus.workflow.engine.schemas import STATUS_FAILED
        cfg = make_cfg(params={"wait_mode": "delay", "delay_seconds": 10})
        out = wait_adapter.poll(cfg, None)
        assert out.status == STATUS_FAILED

    def test_poll_missing_wake_at(self, wait_adapter, make_cfg):
        from taurus.workflow.engine.schemas import STATUS_FAILED
        cfg = make_cfg(params={"wait_mode": "delay", "delay_seconds": 10})
        out = wait_adapter.poll(cfg, {"wait_mode": "delay"})
        assert out.status == STATUS_FAILED


# ──────────────────── cancel ────────────────────

class TestWaitCancel:
    def test_cancel_returns_cancelled(self, wait_adapter, make_cfg):
        from taurus.workflow.engine.schemas import STATUS_CANCELLED
        cfg = make_cfg(params={"wait_mode": "delay", "delay_seconds": 10})
        out = wait_adapter.cancel(cfg, {"wake_at": "2099-01-01T00:00:00"})
        assert out.status == STATUS_CANCELLED

    def test_cancel_no_state(self, wait_adapter, make_cfg):
        from taurus.workflow.engine.schemas import STATUS_CANCELLED
        cfg = make_cfg(params={"wait_mode": "delay", "delay_seconds": 10})
        out = wait_adapter.cancel(cfg, None)
        assert out.status == STATUS_CANCELLED


# ──────────────────── _compute_next_daily_wake_at ────────────────────

class TestComputeNextDailyWakeAt:
    def test_before_target_time(self):
        from taurus.workflow.units.wait import _compute_next_daily_wake_at
        now = datetime(2026, 8, 20, 1, 0, 0, tzinfo=dt_timezone.utc)
        result = _compute_next_daily_wake_at("02:00:00", now)
        assert result.hour == 2
        assert result.minute == 0
        assert result.second == 0
        assert result.day == 20

    def test_after_target_time(self):
        from taurus.workflow.units.wait import _compute_next_daily_wake_at
        now = datetime(2026, 8, 20, 3, 0, 0, tzinfo=dt_timezone.utc)
        result = _compute_next_daily_wake_at("02:00:00", now)
        assert result.hour == 2
        assert result.minute == 0
        assert result.second == 0
        assert result.day == 21

    def test_exactly_at_target_time(self):
        from taurus.workflow.units.wait import _compute_next_daily_wake_at
        now = datetime(2026, 8, 20, 2, 0, 0, tzinfo=dt_timezone.utc)
        result = _compute_next_daily_wake_at("02:00:00", now)
        assert result.day == 21

    def test_with_seconds(self):
        from taurus.workflow.units.wait import _compute_next_daily_wake_at
        now = datetime(2026, 8, 20, 1, 0, 0, tzinfo=dt_timezone.utc)
        result = _compute_next_daily_wake_at("02:00:30", now)
        assert result.hour == 2
        assert result.minute == 0
        assert result.second == 30
        assert result.day == 20

    def test_midnight_crossover(self):
        from taurus.workflow.units.wait import _compute_next_daily_wake_at
        now = datetime(2026, 8, 31, 23, 30, 0, tzinfo=dt_timezone.utc)
        result = _compute_next_daily_wake_at("02:00:00", now)
        assert result.month == 9
        assert result.day == 1
        assert result.hour == 2


# ──────────────────── _parse_daily_time ────────────────────

class TestParseDailyTime:
    def test_valid_hhmmss(self):
        from taurus.workflow.units.wait import _parse_daily_time
        assert _parse_daily_time("02:00:00") == (2, 0, 0)
        assert _parse_daily_time("23:59:59") == (23, 59, 59)
        assert _parse_daily_time("00:00:00") == (0, 0, 0)
        assert _parse_daily_time("12:30:45") == (12, 30, 45)

    def test_valid_hhmm_backward_compat(self):
        from taurus.workflow.units.wait import _parse_daily_time
        assert _parse_daily_time("02:00") == (2, 0, 0)
        assert _parse_daily_time("23:59") == (23, 59, 0)
        assert _parse_daily_time("00:00") == (0, 0, 0)

    def test_invalid(self):
        from taurus.workflow.units.wait import _parse_daily_time
        assert _parse_daily_time("25:00") is None
        assert _parse_daily_time("12:60:00") is None
        assert _parse_daily_time("12:00:60") is None
        assert _parse_daily_time("abc") is None
        assert _parse_daily_time("") is None
        assert _parse_daily_time(None) is None
        assert _parse_daily_time("12:00:00:00") is None
