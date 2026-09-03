"""S3 Abstract base class ExecutableUnit.

Any task that can be orchestrated by DAG workflow must inherit this class and implement all 5 required methods.
Optional hooks (default impl provides safe empty implementation): on_before_dispatch / on_after_finish /
get_metrics_labels / inject_external_event.
"""
from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any

from .schemas import RenderedNodeConfig, UnitOutput, ValidationResult

if TYPE_CHECKING:  # Avoid circular import
    from .context import WorkflowContext


_REQUIRED_CLASS_ATTRS = (
    "node_type",
    "display_name",
    "requires_host",
    "is_asynchronous_human",
)


class ExecutableUnit(abc.ABC):
    """Unified interface for orchestratable units.

    Required class attributes:
    - node_type: str            — Unique identifier (lowercase snake_case, e.g. "script" / "http_callback")
    - display_name: str         — Frontend display name
    - requires_host: bool       — True means must bind to a host to run; False can run cross-host or hostless
    - is_asynchronous_human: bool — True means requires human interaction (approval/callback), must implement inject_external_event
    """

    # Class attribute signature (each concrete child class must assign, otherwise registry will reject registration)
    node_type: str
    display_name: str
    requires_host: bool
    is_asynchronous_human: bool

    # ================================================================ MUST methods
    @abc.abstractmethod
    def validate_config(
        self,
        params: dict[str, Any],
        *,
        secrets_mask: list[str] | None = None,
    ) -> ValidationResult:
        """**Definition-time** config check (called on frontend save / workflow release).

        Can only do static checks based on params itself, cannot depend on real external environment
        (Script ID existence, Host availability, etc. runtime uncertainties — these should be reported as
        E0400 config errors or deferred to dispatch-time E2/E3 errors).
        """

    @abc.abstractmethod
    def validate_and_render(
        self,
        params: dict[str, Any],
        context: "WorkflowContext",
        *,
        secrets_mask: list[str] | None = None,
    ) -> dict[str, Any]:
        """**Pre-execution**: first complete `context.render_structure(params)` interpolation, then do pre-execution check.
        Returns the final rendered params (if interpolation error is raised, it's treated as E04xx config error, no retry).
        """

    @abc.abstractmethod
    def dispatch(self, cfg: RenderedNodeConfig) -> UnitOutput:
        """Initiate execution. Must not block waiting for results.

        - For extremely short synchronous tasks: directly return SUCCESS/FAILED terminal state
        - For async tasks: return RUNNING, and write the cross-call status (pid/job_id, etc.) to adapter_state
        - **Must be idempotent**: calling same cfg.dispatch_id N times = only actually executes once
        """

    @abc.abstractmethod
    def poll(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        """Poll latest status of a RUNNING task. Strictly must not block with sleep."""

    @abc.abstractmethod
    def cancel(
        self,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
    ) -> UnitOutput:
        """Cancel a task. Idempotency: cancelling an already-terminal task must not raise an error, return the original terminal state output."""

    # ====================================================== OPTIONAL hooks (default implementation: safe no-op)
    def on_before_dispatch(self, cfg: RenderedNodeConfig) -> None:
        """Pre-dispatch audit hook. Default: empty implementation. Exceptions raised are swallowed by engine and written as alerts, not affecting actual execution."""

    def on_after_finish(self, cfg: RenderedNodeConfig, output: UnitOutput) -> None:
        """Post-terminal-state side-effect hook (e.g. script execution count +1, write metrics, send notifications).
        Exceptions are swallowed, cannot affect the node's terminal state value.
        """

    def get_metrics_labels(self, cfg: RenderedNodeConfig) -> dict[str, str]:
        """Prometheus metric labels (default: {node_type}). Label values must be low-cardinality, strictly no UUIDs/free text."""
        return {"node_type": self.node_type}

    def inject_external_event(
        self,
        *,
        cfg: RenderedNodeConfig,
        adapter_state: dict[str, Any] | None,
        event: dict[str, Any],
    ) -> UnitOutput:
        """Only for is_asynchronous_human=True nodes: process human/callback event injection.
        Default: human nodes raise NotImplementedError, non-human nodes raise ValueError.
        """
        if not self.is_asynchronous_human:
            raise ValueError(
                f"inject_external_event can only be called on human asynchronous nodes (is_asynchronous_human=True), "
                f"current node node_type={self.node_type!r}"
            )
        raise NotImplementedError(
            f"node_type={self.node_type!r} is a human asynchronous node, must override inject_external_event()"
        )

    # ================================================================ helpers
    @classmethod
    def _ensure_class_attrs(cls) -> list[str]:
        """Return list of missing required class attributes (checked during registry registration)."""
        missing: list[str] = []
        for attr in _REQUIRED_CLASS_ATTRS:
            val = getattr(cls, attr, None)
            if val is None:
                missing.append(attr)
            # bool-typed attributes should not be None/str
            if attr in ("requires_host", "is_asynchronous_human") and not isinstance(val, bool):
                missing.append(f"{attr}(must be bool)")
        return missing