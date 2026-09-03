"""ExecutableUnit Standard S2 Unified data structures.

Three mandatory schemas:
1. RenderedNodeConfig  — Adapter input (post-render + idempotency key)
2. UnitOutput           — Adapter output (status machine + output + polling status)
3. ValidationResult     — Validation return (field_ptr → errors map)
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any


# ========================================================================
# S2.1 Status machine — global constants, no adapter may define its own
# ========================================================================
STATUS_PENDING: int = 0      # Used by engine, adapter dispatch return will not return to pending
STATUS_RUNNING: int = 1      # Running
STATUS_SUCCESS: int = 2      # Terminal state: success
STATUS_FAILED: int = 3       # Terminal state: business/execution failed
STATUS_SKIPPED: int = 4      # Terminal state: condition edge not hit, skip (used by engine)
STATUS_CANCELLED: int = 5    # Terminal state: user/engine cancel

TERMINAL_STATUSES: frozenset[int] = frozenset(
    {STATUS_SUCCESS, STATUS_FAILED, STATUS_SKIPPED, STATUS_CANCELLED}
)

VALID_STATUSES: frozenset[int] = frozenset(
    {STATUS_PENDING, STATUS_RUNNING} | TERMINAL_STATUSES
)


def is_terminal_status(status: int) -> bool:
    return status in TERMINAL_STATUSES


# Placeholder host_id for hostless scenarios (Approval/childWorkflow etc. nodes with requires_host=False)
NO_HOST_SENTINEL: str = "__NO_HOST__"


# ========================================================================
# §2.3 UnifiedOutput
# ========================================================================
@dataclass
class UnitOutput:
    status: int
    output: dict[str, Any] = field(default_factory=dict)

    exit_code: int | None = None
    summary: str | None = None
    error_message: str | None = None

    output_refs: list[dict[str, Any]] | None = None
    adapter_state: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(
                f"UnitOutput.status={self.status!r} invalid; "
                f"must be one of {sorted(VALID_STATUSES)} (§2.1)"
            )
        if not isinstance(self.output, dict):
            raise TypeError("UnitOutput.output must be a dict (§2.3)")

    @property
    def is_terminal(self) -> bool:
        return is_terminal_status(self.status)


# ========================================================================
# S3.1 Validation results
# ========================================================================
@dataclass
class ValidationResult:
    ok: bool = True
    errors: dict[str, list[str]] | None = None
    warnings: dict[str, list[str]] | None = None

    @staticmethod
    def success() -> "ValidationResult":
        return ValidationResult(ok=True)

    def add_error(self, field_ptr: str, message: str) -> None:
        self.ok = False
        if self.errors is None:
            self.errors = {}
        self.errors.setdefault(field_ptr, []).append(message)

    def add_warning(self, field_ptr: str, message: str) -> None:
        if self.warnings is None:
            self.warnings = {}
        self.warnings.setdefault(field_ptr, []).append(message)

    def merge(self, other: "ValidationResult") -> None:
        """Merge another validation result (for multi-level child validation)."""
        if not other.ok:
            self.ok = False
        for k, msgs in (other.errors or {}).items():
            if self.errors is None:
                self.errors = {}
            self.errors.setdefault(k, []).extend(msgs)
        for k, msgs in (other.warnings or {}).items():
            if self.warnings is None:
                self.warnings = {}
            self.warnings.setdefault(k, []).extend(msgs)


# ========================================================================
# S2.2 Post-render node input
# ========================================================================
@dataclass
class RenderedNodeConfig:
    """Immutable config passed from engine to adapter after interpolation is completed before dispatch."""

    # Engine-injected fields (immutable, adapter must not modify)
    execution_id: str
    node_key: str
    node_name: str
    host_id: str
    dispatch_id: str
    attempt_no: int
    user_id: int
    global_timeout_sec: int
    secrets_mask: list[str]
    params: dict[str, Any]
    triggered_at: str

    def __post_init__(self) -> None:
        if self.attempt_no < 1:
            raise ValueError("attempt_no must be >= 1 (spec S2.2)")
        if self.global_timeout_sec < 0:
            raise ValueError("global_timeout_sec must be >= 0 (0 means unlimited)")
        if not isinstance(self.params, dict):
            raise TypeError("params must be a dict")

    # ---- Backward-compatible aliases (legacy code may mistakenly write timeout_sec / timeout; canonical field is still global_timeout_sec) ----
    @property
    def timeout_sec(self) -> int:
        return self.global_timeout_sec

    @property
    def timeout(self) -> int:
        return self.global_timeout_sec

    # ---- Simultaneously allow assigning to old names, for easy early code migration ----
    @timeout_sec.setter
    def timeout_sec(self, val: int) -> None:
        object.__setattr__(self, "global_timeout_sec", int(val or 0))

    @timeout.setter
    def timeout(self, val: int) -> None:
        object.__setattr__(self, "global_timeout_sec", int(val or 0))

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)