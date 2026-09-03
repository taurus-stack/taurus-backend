"""Unit tests for taurus.workflow.engine.schemas
Validates RenderedNodeConfig, UnitOutput, ValidationResult dataclass invariants.
"""
from __future__ import annotations

import pytest

from taurus.workflow.engine.schemas import (
    NO_HOST_SENTINEL,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    TERMINAL_STATUSES,
    RenderedNodeConfig,
    UnitOutput,
    ValidationResult,
    is_terminal_status,
)


class TestStatusConstants:
    def test_status_values_are_stable(self):
        """Spec §2.1 强制:Status值 0~5 global固定, 不可自Definitionscale out."""
        assert STATUS_PENDING == 0
        assert STATUS_RUNNING == 1
        assert STATUS_SUCCESS == 2
        assert STATUS_FAILED == 3
        assert STATUS_SKIPPED == 4
        assert STATUS_CANCELLED == 5

    def test_terminal_status_set(self):
        assert TERMINAL_STATUSES == {2, 3, 4, 5}
        for s in TERMINAL_STATUSES:
            assert is_terminal_status(s) is True
        assert is_terminal_status(STATUS_PENDING) is False
        assert is_terminal_status(STATUS_RUNNING) is False


class TestValidationResult:
    def test_success_factory_has_empty_errors(self):
        r = ValidationResult.success()
        assert r.ok is True
        assert r.errors is None
        assert r.warnings is None

    def test_add_error_sets_ok_false(self):
        r = ValidationResult.success()
        r.add_error("/params/url", "missing")
        assert r.ok is False
        assert r.errors == {"/params/url": ["missing"]}

    def test_multiple_errors_on_same_field_accumulate(self):
        r = ValidationResult.success()
        r.add_error("/params/x", "too short")
        r.add_error("/params/x", "bad pattern")
        r.add_error("/params/y", "empty")
        assert r.errors == {
            "/params/x": ["too short", "bad pattern"],
            "/params/y": ["empty"],
        }


class TestUnitOutput:
    def test_minimal_construction(self):
        o = UnitOutput(status=STATUS_SUCCESS, output={})
        assert o.status == STATUS_SUCCESS
        assert o.exit_code is None
        assert o.adapter_state is None
        assert o.output_refs is None

    def test_output_requires_dict(self):
        # output mustYes dict(后续Interpolate会做.pathReference, list/None 会炸)
        with pytest.raises((TypeError, AssertionError)):
            UnitOutput(status=STATUS_SUCCESS, output=None)  # type: ignore[arg-type]

    def test_invalid_status_rejected(self):
        with pytest.raises(ValueError):
            UnitOutput(status=99, output={})  # type: ignore[arg-type]

    def test_terminal_detection_on_output(self):
        o_success = UnitOutput(status=STATUS_SUCCESS, output={})
        o_running = UnitOutput(status=STATUS_RUNNING, output={})
        assert o_success.is_terminal is True
        assert o_running.is_terminal is False


class TestRenderedNodeConfig:
    def test_required_fields(self):
        cfg = RenderedNodeConfig(
            execution_id="exec-uuid",
            node_key="node_abc",
            node_name="Step 1",
            host_id=NO_HOST_SENTINEL,
            dispatch_id="did-001",
            attempt_no=1,
            user_id=42,
            global_timeout_sec=300,
            secrets_mask=["params.password"],
            params={"url": "https://x"},
            triggered_at="2026-01-01T00:00:00+00:00",
        )
        assert cfg.dispatch_id == "did-001"
        assert cfg.params == {"url": "https://x"}

    def test_attempt_no_starts_from_1(self):
        # attempt_no cannotYes 0(Idempotency key hash Conflict风险)
        with pytest.raises(ValueError):
            RenderedNodeConfig(
                execution_id="e",
                node_key="n",
                node_name="n",
                host_id=NO_HOST_SENTINEL,
                dispatch_id="d",
                attempt_no=0,
                user_id=1,
                global_timeout_sec=0,
                secrets_mask=[],
                params={},
                triggered_at="2026",
            )

    def test_negative_timeout_forbidden(self):
        with pytest.raises(ValueError):
            RenderedNodeConfig(
                execution_id="e",
                node_key="n",
                node_name="n",
                host_id=NO_HOST_SENTINEL,
                dispatch_id="d",
                attempt_no=1,
                user_id=1,
                global_timeout_sec=-1,
                secrets_mask=[],
                params={},
                triggered_at="2026",
            )