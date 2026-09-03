"""Workflow DAG models (v2).

与 taurus/models.py 中Legacy linear workflow model(Workflow/WorkflowStep/WorkflowExecution/WorkflowStepExecution)
Coexist: use this module when workflow.workflow_mode='dag', use legacy path when ='linear'.

This module contains:
- WorkflowDAGVersion     — Immutable release snapshot of Workflow definition (nodes+edges frozen)
- WorkflowNodeExecution  — Execution instance at single node × single host granularity (§S1-02 spec 22 fields fully implemented)
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from dvadmin.utils.models import CoreModel

from taurus.workflow.engine.schemas import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
)

_table_prefix = settings.TABLE_PREFIX

# Reuse 0~5 status values from schemas (single source of truth, no custom numbers allowed here)
_NODE_STATUS_CHOICES = [
    (STATUS_PENDING, "Pending"),
    (STATUS_RUNNING, "Running"),
    (STATUS_SUCCESS, "Success"),
    (STATUS_FAILED, "Failed"),
    (STATUS_SKIPPED, "Skipped"),
    (STATUS_CANCELLED, "Cancelled"),
]

_FAIL_STRATEGY_CHOICES = [
    ("fail_fast", "Fail fast (default)"),
    ("continue", "Continue sibling nodes on failure"),
]

_TRIGGER_TYPE_CHOICES = [
    ("manual", "Manual trigger"),
    ("schedule", "Scheduled task"),
    ("api", "API call"),
    ("dryrun", "Dry run"),
]


class WorkflowDAGVersion(CoreModel):
    """Write one immutable snapshot on each 'Release' click.
    WorkflowExecution only references definition of specific version to avoid data inconsistency like 'running workflow DAG being edited by user'.
    """

    # Related to taurus.models.Workflow (string reference to avoid circular import)
    workflow = models.ForeignKey(
        "taurus.Workflow",
        on_delete=models.CASCADE,
        related_name="dag_versions",
        verbose_name="Belonging workflow",
        db_index=True,
    )
    version = models.PositiveIntegerField(
        verbose_name="Version number (auto-increment, scoped per workflow)",
        default=1,
        db_index=True,
    )
    # Actual frozen DAG definition snapshot - validate_dag(strict=True) on frontend publish to ensure validity
    definition = models.JSONField(
        verbose_name="DAG frozen definition (nodes + edges)",
        default=dict,
    )
    # Global env copied from snapshot (values at release moment; merged with trigger_params and secrets_mask during execution)
    global_envs = models.JSONField(
        verbose_name="Global environment variables snapshot",
        default=dict,
        blank=True,
    )
    release_note = models.CharField(
        max_length=500,
        verbose_name="ReleaseRemark",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Workflow DAG published version"
        app_label = "taurus"
        db_table = _table_prefix + "workflow_dag_version"
        constraints = [
            models.UniqueConstraint(
                fields=("workflow", "version"),
                name="uniq_wf_dag_version_pair",
            ),
        ]
        ordering = ("-workflow_id", "-version")

    def __str__(self) -> str:
        return f"Workflow#{self.workflow_id} v{self.version}"


class WorkflowNodeExecution(CoreModel):
    """§S1-02 22 fields fully implemented.

    Granularity = 1 node (node_key) × 1 host (host_id) × 1 attempt (attempt_no).
    Note: retrying N times on same node+host generates N rows (attempt_no incrementing), dispatch_id globally unique.
    """

    execution = models.ForeignKey(
        "taurus.WorkflowExecution",
        on_delete=models.CASCADE,
        related_name="node_executions",
        verbose_name="Belonging execution instance",
        db_index=True,
    )
    dag_version = models.ForeignKey(
        WorkflowDAGVersion,
        on_delete=models.CASCADE,
        related_name="node_executions",
        verbose_name="DAG VersionSnapshot",
        db_index=True,
    )
    # Node metadata - copied from definition.nodes (avoid mismatch after node edit)
    node_key = models.CharField(
        max_length=128,
        verbose_name="Node key (unique within DAG)",
        db_index=True,
    )
    node_type = models.CharField(
        max_length=64,
        verbose_name="Adapter node_type",
        db_index=True,
    )
    node_name = models.CharField(
        max_length=255,
        verbose_name="Node display name (snapshot at execution time)",
        blank=True,
        default="",
    )

    # Host expansion: non-empty when requires_host=True; otherwise = "__NO_HOST__" (consistent with schemas.NO_HOST_SENTINEL)
    host_id = models.CharField(
        max_length=64,
        verbose_name="Host ID (UUID) or __NO_HOST__",
        db_index=True,
        default="__NO_HOST__",
    )

    # Attempt/Idempotency: dispatch_id = sha256(execution_id+node_key+host_id+attempt_no)
    dispatch_id = models.CharField(
        max_length=128,
        verbose_name="Idempotency key (parameter for adapter dispatch)",
        unique=True,
        db_index=True,
    )
    attempt_no = models.PositiveSmallIntegerField(
        verbose_name="Attempt number (starting from 1)",
        default=1,
    )

    # Config snapshot: params rendered by WorkflowContext during execution
    rendered_params = models.JSONField(
        verbose_name="Rendered node config (including interpolation results)",
        default=dict,
    )
    secrets_mask = models.JSONField(
        verbose_name="List of JSON pointers requiring masking (for view_masked)",
        default=list,
        blank=True,
    )

    # RuntimeStatus
    status = models.SmallIntegerField(
        verbose_name="Execution status (0~5 global constants)",
        choices=_NODE_STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    exit_code = models.IntegerField(
        verbose_name="Process exit code (null if none)",
        null=True,
        blank=True,
    )
    error_message = models.TextField(
        verbose_name="Error message (with E0xxx/E2xxx prefix)",
        null=True,
        blank=True,
    )
    # Adapter state across poll calls (e.g., pid/task handle/approval token)
    adapter_state = models.JSONField(
        verbose_name="Adapter internal status",
        default=dict,
        blank=True,
    )
    # Final output (dict, corresponds to schemas.UnitOutput.output, interpolable downstream via dot path)
    output = models.JSONField(
        verbose_name="Execution output (upstream can reference via ${node_key.output.xxx})",
        default=dict,
        blank=True,
    )
    output_refs = models.JSONField(
        verbose_name="Attachment reference list (e.g., collected file manifest)",
        default=list,
        blank=True,
    )

    # Timeline
    queued_at = models.DateTimeField(
        verbose_name="Enqueue time",
        null=True,
        blank=True,
        db_index=True,
    )
    started_at = models.DateTimeField(
        verbose_name="First dispatch time",
        null=True,
        blank=True,
        db_index=True,
    )
    finished_at = models.DateTimeField(
        verbose_name="Terminal state time",
        null=True,
        blank=True,
        db_index=True,
    )
    duration_ms = models.IntegerField(
        verbose_name="Actual execution duration (ms, excluding queueing)",
        null=True,
        blank=True,
    )
    timeout_sec = models.IntegerField(
        verbose_name="Configured timeout seconds",
        default=0,
        help_text="0 = Use global config",
    )
    poll_count = models.IntegerField(
        verbose_name="poll call count (for monitor, avoids infinite polling)",
        default=0,
    )

    # audit
    user_id = models.IntegerField(
        verbose_name="TriggerUser ID",
        db_index=True,
        null=True,
        blank=True,
    )
    fail_strategy = models.CharField(
        max_length=16,
        choices=_FAIL_STRATEGY_CHOICES,
        default="fail_fast",
        verbose_name="this node failure strategy",
    )
    last_error_code = models.CharField(
        max_length=8,
        verbose_name="last error code prefix (e.g. E2401)",
        null=True,
        blank=True,
        db_index=True,
    )

    class Meta:
        verbose_name = "Workflow node execution instance"
        app_label = "taurus"
        db_table = _table_prefix + "workflow_node_execution"
        constraints = [
            models.UniqueConstraint(
                fields=("execution", "node_key", "host_id", "attempt_no"),
                name="uniq_node_exec_attempt",
            ),
        ]
        indexes = [
            models.Index(fields=("execution", "status"), name="idx_node_exec_status"),
            models.Index(fields=("node_type", "status"), name="idx_node_ntype_status"),
            models.Index(fields=("dag_version", "node_key"), name="idx_node_dagv_key"),
        ]
        ordering = ("execution_id", "node_key", "-attempt_no")

    def __str__(self) -> str:
        return (
            f"Exec#{self.execution_id} {self.node_key}@{self.host_id} "
            f"#{self.attempt_no} status={self.status}"
        )