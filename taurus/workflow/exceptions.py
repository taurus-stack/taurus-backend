"""Business exception classes for WorkflowEngine.

Divided into different modules to avoid circular dependencies when referenced by submodules:
- WorkflowValidationError  # Raised by DAGValidator (can also be unified-caught by upper layer)
- WorkflowRunnerError       # Raised by WorkflowRunner (config missing/database exception/invalid status)
- ExecutableUnitError       # Unified parent class at adapter level (optional)
"""
from __future__ import annotations

__all__ = [
    "ExecutableUnitError",
    "WorkflowRunnerError",
    "WorkflowValidationError",
]


class WorkflowValidationError(ValueError):
    """Workflow DAG/structure validation failed."""


class WorkflowRunnerError(RuntimeError):
    """Business error at WorkflowRunner orchestration layer.

    Typical prefix convention (aligned with error code E3xxx for easy log grep):
    - E3001~E3039 trigger_workflow phase
    - E3040~E3059 cancel_workflow phase
    - E3100~E3109 advance_workflow entry (no dag_version / execution not found)
    - E3110~E3119 poll phase
    - E3120~E3139 dispatch / validate_config / Render phase
    - E3200+    finalization/cleanup phase
    """


class ExecutableUnitError(RuntimeError):
    """Unified exception at Adapter/ExecutableUnit level (optional base class)."""