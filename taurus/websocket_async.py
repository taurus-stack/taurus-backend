"""
Pure asyncio WebSocket implementation (no Twisted/Daphne dependency)

Uses websockets library instead of Django Channels to avoid Twisted/OpenSSL dependency issues.
"""

import asyncio
import json
import logging
import threading
from typing import Dict, Set
from urllib.parse import urlparse

import jwt
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone
from websockets.exceptions import ConnectionClosed
from websockets.server import serve

from taurus.config_crypto import decrypt_value


def _clean_path(path: str) -> str:
    """Clean up path, remove query parameters part"""
    if '?' in path:
        path = path.split('?')[0]
    return path

logger = logging.getLogger(__name__)


# Script error trap header
# Uses trap ERR + LINENO + BASH_COMMAND to precisely capture the line number, command and exit code of errors
# __TAURUS_HEADER_LINES records the trap header line count, used to compute the relative line number of user scripts
# Output format: ::TAURUS_ERROR::LINE=<user script line number>::CMD=<command>::EXIT=<exit code>::
SCRIPT_ERROR_TRAP_HEADER = (
    "set -eo pipefail\n"
    "__taurus_err_handler() {\n"
    "    local _line=$1 _cmd=\"$2\" _rc=$3\n"
    "    local _user_line=$((_line - __TAURUS_HEADER_LINES))\n"
    "    printf '::TAURUS_ERROR::LINE=%s::CMD=%s::EXIT=%s::\\n' \"$_user_line\" \"$_cmd\" \"$_rc\" >&2\n"
    "}\n"
    "trap '__taurus_err_handler ${LINENO} \"${BASH_COMMAND}\" $?' ERR\n"
    "__TAURUS_HEADER_LINES=$LINENO\n"
)


class WebSocketManager:
    """WebSocket connection manager"""
    
    def __init__(self):
        # user_id -> Set[websocket_connection]
        self.connections: Dict[str, Set] = {}
        self._lock_guard = threading.Lock()
        self._lock: asyncio.Lock = None
    
    def _get_lock(self) -> asyncio.Lock:
        with self._lock_guard:
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            if self._lock is not None and running_loop is not None and getattr(self._lock, '_loop', None) is not running_loop:
                self._lock = None
            if self._lock is None:
                self._lock = asyncio.Lock()
            return self._lock
    
    async def add_connection(self, user_id: str, websocket):
        """Add connection"""
        async with self._get_lock():
            if user_id not in self.connections:
                self.connections[user_id] = set()
            self.connections[user_id].add(websocket)
            logger.info(f"User {user_id} connected, current connections: {len(self.connections[user_id])}")
    
    async def remove_connection(self, user_id: str, websocket):
        """Remove connection"""
        async with self._get_lock():
            if user_id in self.connections:
                self.connections[user_id].discard(websocket)
                if not self.connections[user_id]:
                    del self.connections[user_id]
                logger.info(f"User {user_id} disconnected")
    
    async def send_to_user(self, user_id: str, message: dict):
        """Send message to specified user"""
        async with self._get_lock():
            if user_id not in self.connections:
                logger.warning(f"User {user_id} is offline")
                return False
            
            message_str = json.dumps(message)
            disconnected = set()
            
            for websocket in self.connections[user_id]:
                try:
                    await websocket.send(message_str)
                except ConnectionClosed:
                    disconnected.add(websocket)
                except Exception as e:
                    logger.error(f"Failed to send message: {e}")
                    disconnected.add(websocket)
            
            # Clean up disconnected connections
            for ws in disconnected:
                self.connections[user_id].discard(ws)
            
            if not self.connections[user_id]:
                del self.connections[user_id]
            
            return True
    
    def get_online_users(self) -> list:
        """Fetch list of online users"""
        return list(self.connections.keys())


# Global WebSocket manager instance
ws_manager = WebSocketManager()

# Global event loop reference (for Django views to commit async tasks)
_ws_event_loop: asyncio.AbstractEventLoop = None


def get_event_loop() -> asyncio.AbstractEventLoop:
    """Fetch the event loop of the WebSocket service"""
    return _ws_event_loop


def submit_execution(execution_id: str, coro) -> bool:
    """
    Commit execution task to the WebSocket service's event loop

    Args:
        execution_id: Execution ID
        coro: Coroutine to execute

    Returns:
        bool: Whether the commit was successful
    """
    if _ws_event_loop is None or _ws_event_loop.is_closed():
        logger.error("WebSocket server event loop is unavailable")
        return False

    try:
        asyncio.run_coroutine_threadsafe(coro, _ws_event_loop)
        return True
    except Exception as e:
        logger.error(f"Failed to submit execution task: {e}")
        return False


# Output queue for globally running tasks, pushed via WebSocket subscriptions
_execution_output_queues: Dict[str, asyncio.Queue] = {}
_execution_status_lock_guard = threading.Lock()
_execution_status_lock: asyncio.Lock = None


def _get_execution_status_lock() -> asyncio.Lock:
    with _execution_status_lock_guard:
        global _execution_status_lock
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if _execution_status_lock is not None and running_loop is not None and getattr(_execution_status_lock, '_loop', None) is not running_loop:
            _execution_status_lock = None
        if _execution_status_lock is None:
            _execution_status_lock = asyncio.Lock()
        return _execution_status_lock


def _advance_workflow_for_ops_execution(ops_execution_id: str):
    """When OpsExecution reaches terminal state, attempt to advance its parent DAG WorkflowExecution.

    Reverse lookup chain:
      OpsExecution.execution_id
        -> WorkflowNodeExecution.output.ops_execution_id or adapter_state.ops_execution_id
          -> WorkflowNodeExecution.execution_id (WorkflowExecution)

    Once found, calls WorkflowRunner.advance_workflow which triggers:
      * Poll the RUNNING node row, sync SUCCESS/FAILED back
      * Dispatch downstream nodes per DAG dependencies
      * If all terminal, mark WorkflowExecution as SUCCESS/FAILED/CANCELLED
    """
    _advance_workflow_for_ops_execution_impl(ops_execution_id)


def _advance_workflow_for_ops_execution_impl(ops_execution_id: str) -> None:
    """Implementation: push workflow status after OpsExecution completes.

    Core strategy (direct DB write, bypass poll() read path):
      1. Confirm OpsExecution has reached terminal state
      2. Directly write terminal state data (status/output/exit_code/error_message)
         to the associated WorkflowNodeExecution row -- bypasses the REPEATABLE READ
         snapshot issue in advance_workflow -> poll() path that could read stale values
      3. Then call advance_workflow to dispatch downstream nodes and determine
         workflow completion (advance_workflow will skip already-terminal nodes)
    """
    if not ops_execution_id:
        logger.warning("[wf-advance] ops_execution_id is empty, skipping push")
        return

    logger.info(
        "[wf-advance] Starting to push OpsExecution to workflow execution_id=%s",
        ops_execution_id,
    )

    # Step 1: Confirm OpsExecution has reached terminal state, read all required fields directly
    from taurus.models import OpsExecution
    ops = OpsExecution.objects.filter(execution_id=str(ops_execution_id)).first()
    if ops is None:
        logger.warning(
            "[wf-advance] OpsExecution not found execution_id=%s", ops_execution_id,
        )
        return
    if ops.status in (0, 1):  # PENDING or RUNNING
        logger.info(
            "[wf-advance] OpsExecution still running status=%d execution_id=%s, skipping push",
            ops.status, ops_execution_id,
        )
        return
    logger.info(
        "[wf-advance] OpsExecution reached terminal status=%d exit_code=%s execution_id=%s",
        ops.status, ops.exit_code, ops_execution_id,
    )

    # Step 2: Find the associated WorkflowNodeExecution row
    from taurus.workflow.models import WorkflowNodeExecution
    from taurus.workflow.engine.schemas import (
        STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELLED, TERMINAL_STATUSES,
    )
    from django.db.models import Q
    from django.utils import timezone

    str_id = str(ops_execution_id)
    node_rows = list(
        WorkflowNodeExecution.objects.filter(
            Q(adapter_state__ops_execution_id=str_id)
            | Q(output__ops_execution_id=str_id)
        )
    )
    if not node_rows:
        logger.warning(
            "[wf-advance] No associated WorkflowNodeExecution found ops_execution_id=%s",
            ops_execution_id,
        )
        return

    logger.info(
        "[wf-advance] Found %d associated WorkflowNodeExecution rows ops_execution_id=%s",
        len(node_rows), ops_execution_id,
    )

    # Step 3: Build output dictionary (consistent with _unit_output_from_ops logic)
    # Construct output directly from read ops data, avoid re-querying DB
    output: dict = {
        "ops_execution_id": ops.execution_id,
        "stdout": "",
        "stderr": "",
        "exit_code": ops.exit_code,
    }
    # Extract stdout/stderr from output_buffer
    buf = ops.output_buffer or []
    for chunk in buf:
        if isinstance(chunk, dict):
            if "stdout" in chunk and chunk["stdout"]:
                output["stdout"] += str(chunk["stdout"])
            if "stderr" in chunk and chunk["stderr"]:
                output["stderr"] += str(chunk["stderr"])

    if ops.started_at and ops.finished_at:
        dur_ms = int((ops.finished_at - ops.started_at).total_seconds() * 1000)
        output["duration_ms"] = dur_ms

    # === [Concurrency diagnostics] Step 3 complete: output dictionary built ===
    logger.info(
        "[wf-advance] Step3 output dictionary built ops_id=%s keys=%s stdout_len=%d stderr_len=%d "
        "exit_code=%s duration_ms=%s output_buffer_chunks=%d ops_status=%d ops_started_at=%s ops_finished_at=%s",
        ops_execution_id,
        list(output.keys()),
        len(output.get("stdout", "")),
        len(output.get("stderr", "")),
        output.get("exit_code"),
        output.get("duration_ms"),
        len(buf),
        ops.status,
        ops.started_at,
        ops.finished_at,
    )

    # Step 4: Directly write OpsExecution terminal state to WorkflowNodeExecution
    # Map OpsExecution.status -> WorkflowNodeExecution.status
    #   2(SUCCESS) -> STATUS_SUCCESS(2)
    #   3(FAILED)  -> STATUS_FAILED(3)
    #   4(ABORTED) -> STATUS_FAILED(3) or STATUS_CANCELLED(5)
    target_status = None
    error_msg = None
    ec = ops.exit_code

    if ops.status == 2:  # _OPS_SUCCESS
        target_status = STATUS_SUCCESS
    elif ops.status == 3:  # _OPS_FAILED
        target_status = STATUS_FAILED
        msg = ops.error_message or ""
        ec = ops.exit_code if ops.exit_code is not None else 1
        if not msg.startswith("E300"):
            error_msg = f"E3001: {msg}" if ops.exit_code is None else f"E3002: {msg}"
    elif ops.status == 4:  # _OPS_ABORTED
        reason = ops.error_message or ""
        if "workflow-level cancel" in reason.lower() or "cancel" in reason.lower():
            target_status = STATUS_CANCELLED
        else:
            target_status = STATUS_FAILED
            error_msg = f"E4001: Task timeout or interrupted, detail={reason}"
            ec = ops.exit_code or 1
    else:
        logger.warning(
            "[wf-advance] OpsExecution unknown status=%d, treating as FAILED execution_id=%s",
            ops.status, ops_execution_id,
        )
        target_status = STATUS_FAILED
        error_msg = f"E3001: Unknown ops_status={ops.status}"

    # === [Concurrency diagnostics] Step 4 pre-check: status mapping result ===
    logger.info(
        "[wf-advance] Step4 pre-write ops_id=%s ops_status=%d -> target_wf_status=%d "
        "exit_code=%s error_msg=%s node_rows_count=%d node_statuses=[%s]",
        ops_execution_id,
        ops.status,
        target_status,
        ec,
        error_msg,
        len(node_rows),
        ", ".join(f"{r.node_key}(pk={r.pk},status={r.status},host={r.host_id})" for r in node_rows),
    )

    updated_count = 0
    skipped_terminal = 0
    for row in node_rows:
        prev_status = row.status
        prev_output_keys = list((row.output or {}).keys()) if isinstance(row.output, dict) else []

        if row.status in TERMINAL_STATUSES:
            skipped_terminal += 1
            logger.info(
                "[wf-advance] Step4 skip (already terminal) node_key=%s pk=%d prev_status=%d prev_output_keys=%s",
                row.node_key, row.pk, row.status, prev_output_keys,
            )
            continue

        row.status = target_status
        row.output = output
        row.exit_code = ec
        row.finished_at = row.finished_at or timezone.now()
        if row.started_at and row.finished_at:
            row.duration_ms = max(
                0, int((row.finished_at - row.started_at).total_seconds() * 1000)
            )
        if error_msg:
            row.error_message = error_msg
        row.poll_count = int(getattr(row, "poll_count", 0) or 0) + 1

        # === [Concurrency diagnostics] Before DB write: print fields to be written ===
        logger.info(
            "[wf-advance] Step4 write DB node_key=%s pk=%d prev_status=%d -> new_status=%d "
            "new_exit_code=%s new_finished_at=%s new_duration_ms=%s new_error_msg=%s "
            "new_poll_count=%d new_output_keys=%s",
            row.node_key, row.pk, prev_status, row.status,
            row.exit_code, row.finished_at, row.duration_ms,
            row.error_message, row.poll_count, list(output.keys()),
        )

        row.save()
        updated_count += 1

        # === [Concurrency diagnostics] After DB write: read back from DB for validation ===
        from taurus.workflow.models import WorkflowNodeExecution as _WNE
        verify = _WNE.objects.filter(pk=row.pk).only(
            "status", "exit_code", "finished_at", "duration_ms", "error_message", "output"
        ).first()
        if verify:
            verify_output_keys = list((verify.output or {}).keys()) if isinstance(verify.output, dict) else []
            logger.info(
                "[wf-advance] DB verification node_key=%s pk=%d verified_status=%d verified_exit_code=%s "
                "verified_finished_at=%s verified_duration_ms=%s verified_error_msg=%s "
                "verified_output_keys=%s match=%s",
                row.node_key, row.pk, verify.status, verify.exit_code,
                verify.finished_at, verify.duration_ms, verify.error_message,
                verify_output_keys,
                verify.status == target_status and verify.exit_code == ec,
            )
        else:
            logger.error(
                "[wf-advance] DB verification FAILED node_key=%s pk=%d read-back returned None!",
                row.node_key, row.pk,
            )

    # === [Concurrency diagnostics] Step 4 complete: summary ===
    logger.info(
        "[wf-advance] Step4 direct DB write complete ops_id=%s updated=%d skipped_terminal=%d "
        "target_status=%d exit_code=%s total_rows=%d",
        ops_execution_id, updated_count, skipped_terminal,
        target_status, ec, len(node_rows),
    )

    if updated_count == 0:
        logger.info("[wf-advance] No nodes need update, skipping advance_workflow")
        return

    # Step 5: Call advance_workflow for each WorkflowExecution
    # (nodes already directly updated to terminal state, advance_workflow will skip poll and advance downstream)
    exec_ids = {r.execution_id for r in node_rows if r.execution_id}
    if not exec_ids:
        logger.warning("[wf-advance] exec_ids is empty ops_execution_id=%s", ops_execution_id)
        return

    # === [Concurrency diagnostics] Step 5 start ===
    logger.info(
        "[wf-advance] Step5 about to call advance_workflow ops_id=%s exec_ids=%s updated_count=%d",
        ops_execution_id, sorted(exec_ids), updated_count,
    )

    from taurus.models import WorkflowExecution
    from taurus.workflow.engine.runner import WorkflowRunner, WorkflowRunnerError

    runner = WorkflowRunner()
    for eid in exec_ids:
        wf = WorkflowExecution.objects.filter(pk=eid).only("status").first()
        if wf is None:
            logger.warning("[wf-advance] WorkflowExecution#%d not found", eid)
            continue
        if wf.status != 1:
            logger.info(
                "[wf-advance] Step5 skip WorkflowExecution#%d Status=%d (not RUNNING)",
                eid, wf.status,
            )
            continue
        try:
            # === [Concurrency diagnostics] Before advance_workflow: full node snapshot ===
            pre_rows = list(
                WorkflowNodeExecution.objects.filter(execution_id=eid)
                .only("node_key", "status", "host_id", "adapter_state")
            )
            terminal_count = sum(1 for r in pre_rows if r.status in TERMINAL_STATUSES)
            running_count = sum(1 for r in pre_rows if r.status == 1)
            pending_count = sum(1 for r in pre_rows if r.status == 0)
            logger.info(
                "[wf-advance] Step5 ⬅ pre-advance WF#%d total=%d terminal=%d running=%d pending=%d",
                eid, len(pre_rows), terminal_count, running_count, pending_count,
            )
            for pr in pre_rows:
                ops_id = ""
                if isinstance(pr.adapter_state, dict):
                    ops_id = pr.adapter_state.get("ops_execution_id", "")
                logger.info(
                    "[wf-advance] Step5 pre-row node_key=%s host=%s status=%d ops_id=%s",
                    pr.node_key, pr.host_id, pr.status, ops_id,
                )

            tick = runner.advance_workflow(eid)

            # === [Concurrency diagnostics] After advance_workflow: full node snapshot ===
            post_rows = list(
                WorkflowNodeExecution.objects.filter(execution_id=eid)
                .only("node_key", "status", "host_id", "adapter_state", "output")
            )
            post_terminal = sum(1 for r in post_rows if r.status in TERMINAL_STATUSES)
            post_running = sum(1 for r in post_rows if r.status == 1)
            logger.info(
                "[wf-advance] Step5 ➡ post-advance WF#%d total=%d terminal=%d running=%d "
                "tick_finished=%s tick_polled=%d tick_completed=%d tick_dispatched=%d",
                eid, len(post_rows), post_terminal, post_running,
                tick.finished, tick.polled, tick.newly_completed, tick.newly_dispatched,
            )
            for pr in post_rows:
                ops_id = ""
                if isinstance(pr.adapter_state, dict):
                    ops_id = pr.adapter_state.get("ops_execution_id", "")
                out_keys = list((pr.output or {}).keys()) if isinstance(pr.output, dict) else "N/A"
                logger.info(
                    "[wf-advance] Step5 post-row node_key=%s host=%s status=%d ops_id=%s output_keys=%s",
                    pr.node_key, pr.host_id, pr.status, ops_id, out_keys,
                )

            logger.info(
                "[wf-advance] WorkflowExecution#%d advance completed finished=%s "
                "polled=%d completed=%d dispatched=%d skipped=%d",
                eid, tick.finished, tick.polled,
                tick.newly_completed, tick.newly_dispatched, tick.newly_skipped,
            )
        except WorkflowRunnerError as exc:
            logger.warning(
                "[wf-advance] WorkflowExecution#%d advance FAILED WorkflowRunnerError: %s",
                eid, exc,
            )
        except Exception as exc:
            logger.exception(
                "[wf-advance] WorkflowExecution#%d advance EXCEPTION error=%s",
                eid, exc,
            )


def _try_finalize_script_task_execution(batch_id: str):
    """
    When all hosts' executions complete, attempt to aggregate and update the final status of the corresponding ScriptTaskExecution.
    - If there are still incomplete OpsExecution (status=0/1) under this batch_id: skip, wait for the last one
    - If all completed:
        * All hosts exit_code==0 and status=2 -> overall status=2 (Success)
        * Any failed -> overall status=3 (Failed)
    - Compatibility: silently skip when no corresponding ScriptTaskExecution found
      (compatible with manual execution without scheduled task scenarios)
    """
    if not batch_id:
        return
    try:
        from taurus.models import OpsExecution, ScriptTaskExecution
        from django.db.models import Count, Q

        # 1. Only process batch_id generated by scheduled tasks (format: st-<task_id>-<exec_id>-<ts>)
        if not str(batch_id).startswith('st-'):
            return

        # 2. Count the actual results of OpsExecution under the same batch_id
        ops_qs = OpsExecution.objects.filter(batch_id=batch_id)
        total_ops = ops_qs.count()
        if total_ops == 0:
            return

        # Incomplete ones (status 0=Pending, 1=Running)
        pending = ops_qs.filter(status__in=[0, 1]).count()
        if pending > 0:
            # Some hosts haven't finished, wait for the last one
            return

        # 3. Count failed ones (status=3 or exit_code != 0)
        failed_ops = ops_qs.filter(
            Q(status=3) | Q(exit_code__isnull=False) & ~Q(exit_code=0)
        ).count()
        success_ops = total_ops - failed_ops

        # 4. Find the corresponding ScriptTaskExecution record
        exec_qs = ScriptTaskExecution.objects.filter(result__batch_id=batch_id)
        if not exec_qs.exists():
            # Old data compatibility: can't find via result JSON field, try other ways; if not found, just skip
            return
        task_exec = exec_qs.first()
        # 5. Update final status and statistics digest (result write host_summary for convenient frontend direct reading)
        new_status = 2 if failed_ops == 0 else 3
        now = timezone.now()

        duration_seconds = None
        if task_exec.start_time:
            duration_seconds = int((now - task_exec.start_time).total_seconds())

        result = task_exec.result or {}
        result['host_summary'] = {
            'total': total_ops,
            'success': success_ops,
            'failed': failed_ops,
            'timeout': 0,
        }
        task_exec.status = new_status
        task_exec.end_time = now
        task_exec.duration = duration_seconds
        task_exec.result = result
        task_exec.save(update_fields=['status', 'end_time', 'duration', 'result', 'update_datetime'])

        # ===== M4: ops_canary EE hook =====
        # CE 模式 get_service 返回 None → 自动跳过
        try:
            from taurus.ee_registry import ee_registry as _ereg_canary
            canary_svc = _ereg_canary.get_service("ops_canary_service")
            if canary_svc:
                # 从 OpsExecution 读取 pilot_count / pilot_success_rate
                first_ops = ops_qs.first()
                pilot_count = int(getattr(first_ops, "pilot_count", 0) or 0)
                pilot_threshold = int(getattr(first_ops, "pilot_success_rate", 100) or 100)
                if pilot_count > 0 and pilot_count < total_ops:
                    decision, stats = canary_svc.check_pilot_health(
                        ops_qs, success_rate=pilot_threshold,
                    )
                    result["canary"] = {
                        "pilot_count": pilot_count,
                        "threshold": pilot_threshold,
                        "decision": decision,
                        "stats": stats,
                    }
                    task_exec.result = result
                    task_exec.save(update_fields=["result"])
        except Exception as _exc_canary:  # noqa: BLE001
            logger.warning("[ScriptTaskExecution] canary evaluation 跳过: %s", _exc_canary)

        logger.info(
            f"[ScriptTaskExecution aggregation complete] batch_id={batch_id} "
            f"task_exec_id={task_exec.id} new_status={new_status} "
            f"hosts: total={total_ops} success={success_ops} failed={failed_ops}"
        )
    except Exception as e:
        logger.error(f"[ScriptTaskExecution aggregation exception] batch_id={batch_id}: {e}", exc_info=True)


def _sync_approval_status(exec_obj):
    """
    When OpsExecution execution completes, synchronize and update the corresponding OpsExecutionApproval approval status.
    Only processes execution records triggered via approval (execution_type='script' with associated approval).
    """
    try:
        from taurus.models import OpsExecutionApproval
        from django.db.models import Q

        if not exec_obj or exec_obj.execution_type != 'script':
            return

        approvals = OpsExecutionApproval.objects.filter(
            ops_execution=exec_obj,
            status='executing'
        )
        if not approvals.exists():
            return

        now = timezone.now()
        new_status = 'done' if exec_obj.status == 2 else 'failed'

        approvals.update(
            status=new_status,
            finish_time=now,
            update_datetime=now,
        )
        logger.info(f"[Approval status sync] execution_id={exec_obj.execution_id} -> {new_status}")
    except Exception as e:
        logger.error(f"[Approval status sync exception] execution_id={getattr(exec_obj, 'execution_id', '?')}: {e}", exc_info=True)


async def _execute_ops_async(execution_id: str):
    """
    Independent execution coroutine: reads execution task from DB, calls executor to execute command.

    No dependency on WebSocket connection. Updates DB record after execution completes.
    Output simultaneously writes to output_buffer (for subsequent queries) and in-memory queue (for real-time WebSocket subscriptions).

    Args:
        execution_id: Task execution ID
    """
    from taurus.models import OpsExecution
    from taurus.sdk import TaurusClient
    from asgiref.sync import sync_to_async

    async with _get_execution_status_lock():
        existing = _execution_output_queues.get(execution_id)
        if existing is not None:
            # If the value is a real Queue -> an execution coroutine is already running, skip
            logger.info(f"[Execute task] Execution queue already exists, skipping: {execution_id}")
            return
        # existing is None -> either a placeholder written by polling path, or key doesn't exist
        # Both cases are taken over by this coroutine: create a real Queue and overwrite the placeholder
        output_queue = asyncio.Queue()
        _execution_output_queues[execution_id] = output_queue

    try:
        @sync_to_async
        def get_execution():
            return OpsExecution.objects.filter(execution_id=execution_id).select_related('host').first()

        @sync_to_async
        def update_execution_success(exec_obj, out_buf, ec, err):
            exec_obj.status = 3 if (err or ec != 0) else 2
            exec_obj.exit_code = ec
            exec_obj.error_message = err or ('Command terminated by signal' if ec < 0 else None)
            exec_obj.output_buffer = sanitize_output_buffer(out_buf)
            exec_obj.finished_at = timezone.now()
            exec_obj.save()

            # ===== M4: ops_notification EE hook =====
            # CE 模式 get_service 返回 None → 自动跳过，无副作用
            try:
                from taurus.ee_registry import ee_registry as _ereg
                notify_svc = _ereg.get_service("ops_notification_service")
                if notify_svc and getattr(exec_obj, "auto_notify", False):
                    status_label = "SUCCESS" if exec_obj.status == 2 else "FAILED"
                    notify_svc.dispatch(
                        channel="webhook",
                        title=f"OpsExecution {status_label}: {exec_obj.execution_id}",
                        body=(exec_obj.error_message or f"exit_code={ec}")[:500],
                        payload={
                            "execution_id": exec_obj.execution_id,
                            "status": status_label,
                            "exit_code": ec,
                            "host": str(getattr(exec_obj.host, "host_name", "") if exec_obj.host else ""),
                            "command": (exec_obj.command or "")[:200],
                        },
                    )
            except Exception as _exc:  # noqa: BLE001 — notification failure 不影响主执行流
                logger.warning("[WebsocketOps] ops_notification 跳过: %s", _exc)

            # After one host's execution completes, attempt to aggregate and update the scheduled task's overall status
            _try_finalize_script_task_execution(getattr(exec_obj, 'batch_id', None))
            # Sync and update approval record status
            _sync_approval_status(exec_obj)
            # If this ops was generated by a workflow node, advance the workflow in real-time to prevent node status from staying at RUNNING
            _advance_workflow_for_ops_execution(getattr(exec_obj, 'execution_id', None))

        @sync_to_async
        def mark_running(exec_obj):
            exec_obj.status = 1
            exec_obj.started_at = timezone.now()
            exec_obj.save()

        @sync_to_async
        def get_host_info(execution):
            host = execution.host
            return {
                'online_status': host.online_status,
                'host_ip': host.host_ip,
                'host_name': host.host_name,
            }

        @sync_to_async
        def get_execution_info(execution):
            environment = dict(execution.environment) if execution.environment else {}
            # SU_PASSWORD is already encrypted when stored in DB, decrypt before dispatching execution
            su_password = environment.get('SU_PASSWORD')
            if su_password:
                try:
                    environment['SU_PASSWORD'] = decrypt_value(su_password)
                except Exception:
                    logger.exception("[Execute task] SU_PASSWORD decryption failed, execution_id=%s", execution.execution_id)
                    environment.pop('SU_PASSWORD', None)
            return {
                'execution_type': execution.execution_type,
                'command': execution.command,
                'args': execution.args,
                'timeout_seconds': execution.timeout_seconds,
                'environment': environment,
                'use_shell': execution.use_shell,
                'merge_streams': execution.merge_streams,
                'load_profile': execution.load_profile,
                'working_directory': execution.working_directory,
                'script_type': execution.script_type,
                'script_content': execution.script_content,
            }

        execution = await get_execution()
        if not execution:
            logger.error(f"[Execute task] Task not found: {execution_id}")
            return

        # Completed -> Skip
        if execution.status == 2:
            logger.info(f"[Execute task] Task already completed, skipping: {execution_id}")
            return

        # Orphan detection: status=1 but start time exceeds timeout_seconds+60 seconds
        is_orphan = False
        if execution.status == 1 and execution.started_at:
            elapsed = (timezone.now() - execution.started_at).total_seconds()
            max_allowed = (execution.timeout_seconds or 300) + 60
            if elapsed > max_allowed:
                logger.warning(
                    f"[Execute task] Orphan task detected: {execution_id} "
                    f"(marked running {int(elapsed)}s > allowed {max_allowed}s), resetting and retrying"
                )
                is_orphan = True
            else:
                logger.info(
                    f"[Execute task] Task already running for {int(elapsed)}s, skipping: {execution_id}"
                )
                return

        # status=0 or orphan task -> mark as Running
        if execution.status == 0 or is_orphan:
            await mark_running(execution)

        host_info = await get_host_info(execution)
        if host_info['online_status'] != 1:
            logger.warning(f"[Execute task] Host is offline: {execution_id}")
            await update_execution_success(execution, [], 1, f"Host is currently offline: {host_info['host_ip']}")
            return

        address = f"{host_info['host_ip']}:50051"
        output_buffer = []
        exit_code = 0
        error_msg = None

        exec_info = await get_execution_info(execution)

        try:
            sdk_cert_dir = getattr(settings, 'SDK_CERT_DIR', None)
            client_kwargs = {
                'connect_timeout': int(getattr(settings, 'EXECUTOR_CONNECT_TIMEOUT', 5)),
            }
            if sdk_cert_dir:
                import os
                client_kwargs['cert_file'] = os.path.join(sdk_cert_dir, 'client.crt')
                client_kwargs['key_file'] = os.path.join(sdk_cert_dir, 'client.key')
                client_kwargs['ca_file'] = os.path.join(sdk_cert_dir, 'ca.crt')

            # Note: TaurusClient.__aenter__ already contains GetStatus probe with hard timeout,
            # failures will raise ConnectionError / gRPC AioRpcError, no need to re-probe here.
            async with TaurusClient(address, **client_kwargs) as client:
                if exec_info['execution_type'] == 'command':
                    env = _sanitize_value(exec_info['environment']) if exec_info['environment'] else {}
                    async for chunk in client.execute_command(
                        command=exec_info['command'],
                        args=_sanitize_value(exec_info['args']),
                        timeout=exec_info['timeout_seconds'],
                        environment=env if env else None,
                        shell=exec_info['use_shell'],
                        merge_streams=exec_info['merge_streams'],
                        load_profile=exec_info.get('load_profile', 'false'),
                        working_directory=exec_info['working_directory'] or None,
                    ):
                        sanitized = sanitize_chunk(chunk)
                        output_buffer.append(sanitized)
                        await output_queue.put(sanitized)
                        if 'finished' in sanitized:
                            exit_code = sanitized.get('exit_code', 0)
                            break
                else:
                    # Script execution: uses interpreter -c '<script content>' -- [args...] syntax
                    # shell=False lets executor directly posix_spawn with argument array,
                    # avoiding " ".join(argv) which would break multi-line scripts / quoting
                    # (-c option must be a single parameter)
                    interpreter_map = {'sh': '/bin/bash', 'python': '/usr/bin/python3'}
                    interpreter = interpreter_map.get(exec_info['script_type'])
                    if not interpreter:
                        error_msg = f"Unsupported script type: {exec_info['script_type']}"
                    else:
                        raw_args = _sanitize_value(exec_info['args']) or []
                        script_content = exec_info['script_content'] or ''
                        # Inject trap ERR header for sh-type scripts to precisely capture error line number, command and exit code
                        if exec_info['script_type'] == 'sh':
                            script_content = SCRIPT_ERROR_TRAP_HEADER + script_content
                        # bash -c 'script' -- arg1 arg2 ... -- standard -c argument passing
                        command_args = ['-c', script_content]
                        if raw_args:
                            command_args.append('--')
                            command_args.extend([str(a) for a in raw_args])
                        env = _sanitize_value(exec_info['environment']) if exec_info['environment'] else {}
                        async for chunk in client.execute_command(
                            command=interpreter,
                            args=command_args,
                            timeout=exec_info['timeout_seconds'],
                            environment=env if env else None,
                            shell=False,
                            working_directory=exec_info.get('working_directory') or None,
                            load_profile=exec_info.get('load_profile', 'false') if exec_info['script_type'] == 'sh' else 'false',
                            merge_streams=exec_info['merge_streams'],
                        ):
                            sanitized = sanitize_chunk(chunk)
                            output_buffer.append(sanitized)
                            await output_queue.put(sanitized)
                            if 'finished' in sanitized:
                                exit_code = sanitized.get('exit_code', 0)
                                break

            # ========== Fallback: loop completed but no 'finished' marker received ==========
            # (e.g. SDK version mismatch, stream abnormally closed, etc.)
            # Default to exit_code = 1 to prevent false success with exit_code=0
            has_finished_marker = any('finished' in c for c in output_buffer)
            has_error_marker = any('error' in c for c in output_buffer)
            if not has_finished_marker:
                if exit_code == 0 or exit_code is None:
                    exit_code = 1
                error_msg = error_msg or "Execution finished but no 'finished' marker received from executor"
                final_chunk = {'finished': True, 'exit_code': exit_code}
                if has_error_marker and not error_msg:
                    last_err = next((c.get('error') for c in reversed(output_buffer) if 'error' in c), None)
                    error_msg = last_err or "Execution failed"
                    final_chunk['error'] = error_msg
                output_buffer.append(final_chunk)
                try:
                    await output_queue.put(final_chunk)
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"[Execute task] Execution exception [{execution_id}]: {e}", exc_info=True)
            error_msg = error_msg or str(e)
            if exit_code == 0 or exit_code is None:
                exit_code = 1
            # Push an error output chunk
            output_buffer.append({'error': str(e)})
            try:
                await output_queue.put({'error': str(e)})
            except Exception:
                pass

        # Mark as complete
        await update_execution_success(execution, output_buffer, exit_code, error_msg)
        # Push end marker
        await output_queue.put({'_finished': True, 'exit_code': exit_code, 'error': error_msg})
        logger.info(f"[Execute task] Complete: execution_id={execution_id}, exit_code={exit_code}")

    except Exception as e:
        logger.error(f"[Execute task] Fatal exception [{execution_id}]: {e}", exc_info=True)
    finally:
        async with _get_execution_status_lock():
            _execution_output_queues.pop(execution_id, None)


@sync_to_async
def _get_message_unread(user_id: int) -> int:
    """Get user unread message count"""
    from dvadmin.system.models import MessageCenterTargetUser
    count = MessageCenterTargetUser.objects.filter(users=user_id, is_read=False).count()
    return count or 0


@sync_to_async
def _get_message_center_targets(message_id: int) -> list:
    """Get target users of the message center"""
    from dvadmin.system.models import MessageCenter
    targets = MessageCenter.objects.filter(id=message_id).values_list('target_user', flat=True)
    return list(targets)


def create_message(sender: str, msg_type: str, msg: str, unread: int = 0, content_code: str | None = None) -> dict:
    """Create message structure"""
    result = {
        'sender': sender,
        'contentType': msg_type,
        'content': msg,
        'unread': unread
    }
    if content_code is not None:
        result['content_code'] = content_code
    return result


def _sanitize_value(value):
    """
    Recursively clean up values, ensuring all bytes are converted to strings
    
    Args:
        value: Any type of value
        
    Returns:
        Cleaned value, bytes converted to UTF-8 string
    """
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    elif isinstance(value, dict):
        return {k: _sanitize_value(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    else:
        return value


def sanitize_chunk(chunk: dict) -> dict:
    """
    Clean up output chunk, ensuring all values are JSON-serializable strings
    
    Args:
        chunk: Dictionary containing stdout/stderr and other fields
        
    Returns:
        Cleaned dictionary, bytes values converted to UTF-8 strings
    """
    return _sanitize_value(chunk)


def sanitize_output_buffer(output_buffer: list) -> list:
    """
    Clean up the entire output buffer, ensuring all values are JSON-serializable
    
    Args:
        output_buffer: List of output chunks
        
    Returns:
        Cleaned list
    """
    return [_sanitize_value(chunk) for chunk in output_buffer]


async def handle_websocket(websocket, path=None):
    """
    WebSocket joinprocessserver
    
    兼容 websockets library的新 API(10.0+)
    """
    user_id = None
    
    try:
        # Parse URL Fetch service_uid
        path = websocket.request.path if hasattr(websocket, 'request') else websocket.path
        path = _clean_path(path)
        path_parts = path.strip('/').split('/')
        if len(path_parts) < 2:
            logger.error("Invalid WebSocket path")
            await websocket.close(code=1008, reason="Invalid path")
            return
        
        service_uid = path_parts[-1]
        
        # decode JWT token
        try:
            decoded_result = jwt.decode(service_uid, settings.SECRET_KEY, algorithms=["HS256"])
            user_id = str(decoded_result.get('user_id'))
        except jwt.InvalidSignatureError:
            logger.warning("JWT signature verification failed")
            await websocket.close(code=1008, reason="Invalid token")
            return
        except jwt.ExpiredSignatureError:
            logger.warning("JWT token expired")
            await websocket.close(code=1008, reason="Token expired")
            return
        
        # 添加join
        await ws_manager.add_connection(user_id, websocket)
        
        # sendjoinSuccessmessage
        unread_count = await _get_message_unread(int(user_id))
        if unread_count == 0:
            await websocket.send(json.dumps(create_message('system', 'SYSTEM', 'You are online', content_code='wsOnline')))
        else:
            await websocket.send(json.dumps(
                create_message('system', 'SYSTEM', "You have unread messages", unread=unread_count, content_code='wsUnreadMessages')
            ))
        
        # processmessage
        async for message in websocket:
            try:
                data = json.loads(message)
                await process_message(user_id, data)
            except json.JSONDecodeError:
                await websocket.send(json.dumps(
                    create_message('system', 'ERROR', 'Invalid message format')
                ))
    
    except ConnectionClosed:
        logger.info(f"User {user_id} connection closed")
    except Exception as e:
        logger.error(f"WebSocket handler exception: {e}", exc_info=True)
    finally:
        if user_id:
            await ws_manager.remove_connection(user_id, websocket)


async def process_message(user_id: str, data: dict):
    """processreceive到的message"""
    message_id = data.get('message_id')
    if not message_id:
        return
    
    # FetchTargetUserlist
    target_users = await _get_message_center_targets(message_id)
    
    # 向TargetUserPushmessage
    for target_user_id in target_users:
        await ws_manager.send_to_user(str(target_user_id), data)


async def handle_ops_websocket(websocket, path=None):
    """
    运维中心 WebSocket processserver(订阅Output模式).

    pathFormat: /ws/ops/<execution_id>/
    行为:
    - 任务Completed → PushcacheOutput
    - 任务Running → 订阅内存Outputqueue实时Push
    - 任务Pending execution → TriggerExecution并订阅Output
    """
    execution_id = None
    try:
        path = websocket.request.path if hasattr(websocket, 'request') else websocket.path
        path = _clean_path(path)
        path_parts = path.strip('/').split('/')
        if len(path_parts) < 3 or path_parts[0] != 'ws' or path_parts[1] != 'ops':
            logger.error("Invalid ops WebSocket path")
            await websocket.close(code=1008, reason="Invalid path")
            return

        execution_id = path_parts[2]
        logger.info(f"[OpsWS] Connected: execution_id={execution_id}")

        from taurus.models import OpsExecution
        from asgiref.sync import sync_to_async

        @sync_to_async
        def get_execution():
            return OpsExecution.objects.filter(execution_id=execution_id).first()

        execution = await get_execution()
        if not execution:
            await websocket.send(json.dumps({'error': f'Execution not found: {execution_id}'}))
            await websocket.close(code=1008, reason="Execution not found")
            return

        # Completed/Failed/中断 → sendcacheOutput
        if execution.status in (2, 3, 4):
            for chunk in execution.output_buffer:
                if 'stdout' in chunk:
                    await websocket.send(json.dumps({'stdout': chunk['stdout']}))
                elif 'stderr' in chunk:
                    await websocket.send(json.dumps({'stderr': chunk['stderr']}))
            if execution.status == 3 and execution.error_message:
                await websocket.send(json.dumps({'error': execution.error_message}))
            elif execution.status == 4:
                await websocket.send(json.dumps({
                    'error': execution.error_message or 'Execution interrupted: backend lost connection to executor, output lost'
                }))
            await websocket.send(json.dumps({'finished': execution.exit_code or 0}))
            return

        # 若为Pending execution, 先主动TriggerExecution
        need_trigger = (execution.status == 0)
        if need_trigger:
            should_trigger = False
            async with _get_execution_status_lock():
                existing = _execution_output_queues.get(execution_id)
                if existing is None:
                    # key 不存在, 或值YesPolling写的占坑 None —— 两种情况都require
                    # 先占坑再Trigger, 确保 _execute_ops_async 能顺利转正为真实 Queue
                    _execution_output_queues[execution_id] = None
                    should_trigger = True
            if should_trigger:
                asyncio.create_task(_execute_ops_async(execution_id))
                logger.info(f"[OpsWS] Triggering execution: {execution_id}")
            await asyncio.sleep(0.1)

        # status=1 且no活跃queue → 孤儿任务, InfoFrontend
        elif execution.status == 1:
            async with _get_execution_status_lock():
                has_queue = (
                    execution_id in _execution_output_queues
                    and _execution_output_queues[execution_id] is not None
                )
            if not has_queue:
                await websocket.send(json.dumps({
                    'error': 'Task interrupted (backend lost connection to executor). Output lost, task will not auto-retry. Please confirm in UI and resubmit manually.'
                }))
                await websocket.send(json.dumps({'finished': 1}))
                return

        # 订阅Outputqueue(Pollingdetect, 最多等 300 秒)
        queue_ref = None
        deadline = asyncio.get_event_loop().time() + 300
        while asyncio.get_event_loop().time() < deadline:
            async with _get_execution_status_lock():
                queue_ref = _execution_output_queues.get(execution_id)
            if queue_ref is not None:
                break
            # mayExecutionCompleted但未订阅, check DB Status
            execution = await get_execution()
            if execution and execution.status in (2, 3):
                for chunk in execution.output_buffer:
                    if 'stdout' in chunk:
                        await websocket.send(json.dumps({'stdout': chunk['stdout']}))
                    elif 'stderr' in chunk:
                        await websocket.send(json.dumps({'stderr': chunk['stderr']}))
                if execution.status == 3 and execution.error_message:
                    await websocket.send(json.dumps({'error': execution.error_message}))
                await websocket.send(json.dumps({'finished': execution.exit_code or 0}))
                return
            await asyncio.sleep(0.3)

        if queue_ref is None:
            logger.warning(f"[OpsWS] Execution queue not found: {execution_id}")
            await websocket.send(json.dumps({'error': 'Task execution timed out or exited'}))
            return

        # 订阅Push
        while True:
            try:
                chunk = await asyncio.wait_for(queue_ref.get(), timeout=300)
            except asyncio.TimeoutError:
                break
            if '_finished' in chunk:
                if chunk.get('error'):
                    await websocket.send(json.dumps({'error': chunk['error']}))
                await websocket.send(json.dumps({'finished': chunk.get('exit_code', 0)}))
                break
            if 'stdout' in chunk:
                await websocket.send(json.dumps({'stdout': chunk['stdout']}))
            if 'stderr' in chunk:
                await websocket.send(json.dumps({'stderr': chunk['stderr']}))
            if 'error' in chunk:
                await websocket.send(json.dumps({'error': chunk['error']}))

        logger.info(f"[OpsWS] Done: execution_id={execution_id}")

    except ConnectionClosed:
        logger.info(f"[OpsWS] Connection closed: execution_id={execution_id}")
    except Exception as e:
        logger.error(f"[OpsWS] Exception [{execution_id}]: {e}", exc_info=True)


async def _poll_pending_executions():
    """
    定期Polling数据library中Pending execution的任务并TriggerExecution.

    process两种任务:
    1. status=0(Pending execution)— 新Commit的任务 → 正常Execution
    2. status=1 且 timeout — 后端挂掉后的孤儿任务 → 安全check后标记Failed, 不自动重跑
    """
    from taurus.models import OpsExecution
    from django.utils import timezone

    @sync_to_async
    def get_pending_ids():
        return list(
            OpsExecution.objects.filter(status=0).values_list('execution_id', flat=True)
        )

    @sync_to_async
    def get_orphan_ids():
        now = timezone.now()
        orphans = []
        for row in OpsExecution.objects.filter(status=1).exclude(started_at__isnull=True).values(
            'execution_id', 'started_at', 'timeout_seconds'
        ):
            if row['started_at'] is None:
                continue
            elapsed = (now - row['started_at']).total_seconds()
            max_allowed = (row.get('timeout_seconds') or 300) + 60
            if elapsed > max_allowed:
                orphans.append(row['execution_id'])
        return orphans

    while True:
        try:
            # 1. Pending execution任务:正常Trigger
            pending_ids = await get_pending_ids()
            for eid in pending_ids:
                should_spawn = False
                async with _get_execution_status_lock():
                    if eid not in _execution_output_queues:
                        # 先立即占坑(值为 None 表示占坑中, 后续 _execute_ops_async 会replace成 queue), 
                        # Avoid create_task Dispatch间隙被 WebSocket path抢占, 导致同一 execution_id
                        # simultaneouslystart两entries TaurusClient 并发探活.
                        _execution_output_queues[eid] = None
                        should_spawn = True
                if should_spawn:
                    logger.info(f"[Polling] Found pending task: {eid}")
                    asyncio.create_task(_execute_ops_async(eid))
                await asyncio.sleep(0.05)

            # 2. 孤儿任务:查 executor 上Status后安全process, 不自动重跑
            orphan_ids = await get_orphan_ids()
            for eid in orphan_ids:
                should_spawn = False
                async with _get_execution_status_lock():
                    if eid not in _execution_output_queues:
                        # temporary占坑, Avoid重复process
                        _execution_output_queues[eid] = None
                        should_spawn = True
                if should_spawn:
                    asyncio.create_task(_handle_orphan_execution(eid))
                await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"[Polling] Exception checking pending task: {e}", exc_info=True)
        await asyncio.sleep(0.05)


async def _handle_orphan_execution(execution_id: str):
    """
    安全process孤儿任务(后端挂掉后遗留的 status=1 任务):
    1. Query executor 上YesNo有matchCommand在跑
    2. 不自动re-ExecutionCommand(Avoid重复Execution如 rm -rf, 数据librarywrite等非IdempotencyCommand)
    3. 标记为 status=4(中断), 让User在Frontend决定YesNore-run
    """
    from taurus.models import OpsExecution
    from taurus.sdk import TaurusClient
    from asgiref.sync import sync_to_async

    try:
        @sync_to_async
        def get_execution():
            return OpsExecution.objects.filter(execution_id=execution_id).select_related('host').first()

        @sync_to_async
        def mark_interrupted(exec_obj, error_msg):
            exec_obj.status = 4
            exec_obj.error_message = error_msg
            exec_obj.finished_at = timezone.now()
            exec_obj.save()
            # if该 ops YesWorkflow节.产生的, 实时Advance workflow(中断节.视为Terminal state)
            _advance_workflow_for_ops_execution(getattr(exec_obj, 'execution_id', None))

        execution = await get_execution()
        if not execution:
            return
        if execution.status != 1:
            return

        host_ip = execution.host.host_ip
        is_running_on_executor = False

        # Attemptcheck executor 上YesNo还有matchCommand在run
        try:
            from django.conf import settings
            sdk_cert_dir = getattr(settings, 'SDK_CERT_DIR', None)
            client_kwargs = {}
            if sdk_cert_dir:
                import os
                client_kwargs['cert_file'] = os.path.join(sdk_cert_dir, 'client.crt')
                client_kwargs['key_file'] = os.path.join(sdk_cert_dir, 'client.key')
                client_kwargs['ca_file'] = os.path.join(sdk_cert_dir, 'ca.crt')

            async with TaurusClient(
                f"{host_ip}:50051",
                connect_timeout=int(getattr(settings, 'EXECUTOR_CONNECT_TIMEOUT', 5)),
                **client_kwargs,
            ) as client:
                active_execs = await client.list_executions()
                target_cmd = execution.command or ''
                for exec_info in active_execs:
                    if exec_info.get('command') == target_cmd:
                        is_running_on_executor = True
                        break
        except Exception:
            # 连不上 executor 也属正常, 不影响标记流程
            pass

        if is_running_on_executor:
            msg = ("Execution interrupted: connection to executor lost, commands may still be running on executor. "
                   "Output has been lost, not auto-retrying to avoid duplicate execution. Please manually confirm the status on the host before proceeding.")
        else:
            msg = ("Execution interrupted: connection to executor lost, cannot confirm final command status. "
                   "Output has been lost, not auto-retrying to avoid duplicate execution. Please retry manually if needed.")

        logger.warning(f"[OrphanTask] {execution_id}: {msg}")
        await mark_interrupted(execution, msg)
    finally:
        # 释放占坑标记
        async with _get_execution_status_lock():
            _execution_output_queues.pop(execution_id, None)


async def start_websocket_server(host: str = '0.0.0.0', port: int = 8765):
    """
    start WebSocket Serviceserver
    
    Args:
        host: 监听地址
        port: 监听Port
    """
    global _ws_event_loop
    _ws_event_loop = asyncio.get_running_loop()
    logger.info(f"Starting WebSocket server: ws://{host}:{port}")

    asyncio.create_task(_poll_pending_executions())
    logger.info("Pending execution polling started")
    
    async def router_handler(websocket, path=None):
        """routeprocessserver, according topath分发到Different的processserver"""
        raw_path = websocket.request.path if hasattr(websocket, 'request') else websocket.path
        path = _clean_path(raw_path)
        path_parts = path.strip('/').split('/')
        logger.info(f"[WSRoute] Raw={raw_path}, cleaned={path}, parts={path_parts}")

        # /ws/ops/<execution_id>/
        if len(path_parts) >= 3 and path_parts[0] == 'ws' and path_parts[1] == 'ops':
            await handle_ops_websocket(websocket, path)
        # /ws/<service_uid>/ (Message center)
        elif len(path_parts) >= 2 and path_parts[0] == 'ws':
            await handle_websocket(websocket, path)
        else:
            logger.warning(f"Unknown WebSocket path: {path}")
            await websocket.close(code=1008, reason="Unknown path")
    
    async with serve(
        router_handler,
        host,
        port,
        ping_interval=20,
        ping_timeout=20,
    ) as server:
        await server.serve_forever()


def websocket_push(user_id: str, message: dict):
    """
    同步Pushmessage(供 Django View调用)
    
    use async_to_sync package装异步调用
    """
    from asgiref.sync import async_to_sync
    async_to_sync(ws_manager.send_to_user)(user_id, message)


def create_message_push(
    title: str,
    content: str,
    target_type: int = 0,
    target_user: list = None,
    target_dept=None,
    target_role=None,
    message: dict = None,
    request=None
):
    """
    createmessage并Push(替代原 MessageCenter 的 create_message_push)
    
    Args:
        title: messageTitle
        content: Message content
        target_type: Target type (0=指定User, 1=Role, 2=Dept, 3=System notification)
        target_user: TargetUserlist
        target_dept: TargetDeptlist
        target_role: TargetRolelist
        message: message体
        request: Django Request object
    """
    if message is None:
        message = {"contentType": "INFO", "content": None}
    if target_role is None:
        target_role = []
    if target_dept is None:
        target_dept = []
    
    from dvadmin.system.models import MessageCenter, Users
    from dvadmin.system.views.message_center import MessageCenterTargetUserSerializer
    from dvadmin.utils.serializers import CustomModelSerializer
    
    # createMessage centerrecord
    class MessageCreateSerializer(CustomModelSerializer):
        class Meta:
            model = MessageCenter
            fields = "__all__"
            read_only_fields = ["id"]
    
    data = {
        "title": title,
        "content": content,
        "target_type": target_type,
        "target_user": target_user,
        "target_dept": target_dept,
        "target_role": target_role
    }
    
    message_center_instance = MessageCreateSerializer(data=data, request=request)
    message_center_instance.is_valid(raise_exception=True)
    message_center_instance.save()
    
    # FetchTargetUser
    users = target_user or []
    if target_type == 1:  # By role
        users = list(Users.objects.filter(role__id__in=target_role).values_list('id', flat=True))
    elif target_type == 2:  # By dept
        users = list(Users.objects.filter(dept__id__in=target_dept).values_list('id', flat=True))
    elif target_type == 3:  # System notification
        users = list(Users.objects.values_list('id', flat=True))
    
    # createTargetUserassociate
    targetuser_data = []
    for user in users:
        targetuser_data.append({
            "messagecenter": message_center_instance.instance.id,
            "users": user
        })
    
    targetuser_instance = MessageCenterTargetUserSerializer(
        data=targetuser_data, many=True, request=request
    )
    targetuser_instance.is_valid(raise_exception=True)
    targetuser_instance.save()
    
    # Pushmessage
    for user in users:
        from asgiref.sync import async_to_sync
        
        user_id_str = str(user)
        unread_count = async_to_sync(_get_message_unread)(user)
        
        async_to_sync(ws_manager.send_to_user)(
            user_id_str,
            {**message, 'unread': unread_count}
        )