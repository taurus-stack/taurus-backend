"""
WorkflowScheduler 逻辑Unit test(mock 版)

不depend on数据library/Redis/ASGI, via sys.modules Preset taurus.models mock
Avoid pymysql depend on, 覆盖所有核心Branch.

run方式:
  cd taurus-backend
  python tests/workflow/test_workflow_scheduler_mock.py
"""
from __future__ import annotations

import datetime
import os
import sys
import threading
import time
import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch, PropertyMock

# ===========================================================================
# 前置 mock:仅拦截 taurus.models(Avoid pymysql load)
# ===========================================================================


class _FakeTaurusModels:
    """替代 taurus.models 的假module"""

    class WorkflowExecution:
        def __init__(self, pk, status=1):
            self.pk = pk
            self.status = status

        objects = MagicMock()

    class OpsExecution:
        def __init__(self, pk, execution_id, status=0, **kwargs):
            self.pk = pk
            self.execution_id = execution_id
            self.status = status
            self.execution_type = kwargs.get("execution_type", "command")
            self.host_id = kwargs.get("host_id")
            self.host = (
                type("H", (), {"id": self.host_id}) if self.host_id else None
            )
            self.create_datetime = kwargs.get("create_datetime")
            self.started_at = kwargs.get("started_at")
            self.timeout_seconds = kwargs.get("timeout_seconds", 300)

        def save(self, update_fields=None):
            pass

    WorkflowExecution.objects = MagicMock()
    OpsExecution.objects = MagicMock()


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

sys.modules["taurus.models"] = _FakeTaurusModels
os.environ["DJANGO_SETTINGS_MODULE"] = "application.settings"


class TestWorkflowScheduler(unittest.TestCase):
    """覆盖 run_workflow_scheduler.Command 的所有核心Branch."""

    def setUp(self):
        from taurus.management.commands.run_workflow_scheduler import Command

        self.Command = Command
        # Reset mock
        _FakeTaurusModels.WorkflowExecution.objects = MagicMock()
        _FakeTaurusModels.OpsExecution.objects = MagicMock()

    # ==================================================================
    # _advance_running_workflows
    # ==================================================================

    def test_advance_empty_list(self):
        """Branch B:无 RUNNING execute → return 0"""
        _FakeTaurusModels.WorkflowExecution.objects.filter.return_value.order_by.return_value.values_list.return_value = []

        cmd = self.Command()
        cmd.batch_size = 50
        cmd._running = True
        result = cmd._advance_running_workflows()
        self.assertEqual(result, 0)

    def test_advance_all_success(self):
        """3   execution allAdvanceSuccess"""
        from taurus.workflow.engine.runner import AdvanceTick

        mock_tick = AdvanceTick(
            execution_id=1, polled=2, newly_completed=1, newly_dispatched=1,
            newly_skipped=0, finished=True,
        )
        # 构造 3   RUNNING execution 的 values_list(flat=True) return值
        _FakeTaurusModels.WorkflowExecution.objects.filter.return_value.order_by.return_value.values_list.return_value = [
            101, 102, 103
        ]

        with patch("taurus.workflow.engine.runner.WorkflowRunner") as MockRunner:
            mock_runner = MockRunner.return_value
            mock_runner.advance_workflow.return_value = mock_tick

            cmd = self.Command()
            cmd.batch_size = 50
            cmd._running = True
            result = cmd._advance_running_workflows()
            self.assertEqual(result, 3)
            self.assertEqual(mock_runner.advance_workflow.call_count, 3)

    def test_advance_partial_failure(self):
        """2   execution 中 1  AdvanceFailed"""
        from taurus.workflow.engine.runner import AdvanceTick

        mock_tick = AdvanceTick(
            execution_id=1, polled=1, newly_completed=0, newly_dispatched=1,
            newly_skipped=0, finished=False,
        )
        # 构造 2   RUNNING execution
        _FakeTaurusModels.WorkflowExecution.objects.filter.return_value.order_by.return_value.values_list.return_value = [
            201, 202
        ]

        with patch("taurus.workflow.engine.runner.WorkflowRunner") as MockRunner:
            mock_runner = MockRunner.return_value
            mock_runner.advance_workflow.side_effect = [
                mock_tick, RuntimeError("推进失败"),
            ]

            cmd = self.Command()
            cmd.batch_size = 50
            cmd._running = True
            result = cmd._advance_running_workflows()
            self.assertEqual(result, 1)

    def test_advance_db_exception(self):
        """Query RUNNING list抛exception → return 0"""
        _FakeTaurusModels.WorkflowExecution.objects.filter.side_effect = RuntimeError("DB挂了")

        cmd = self.Command()
        cmd.batch_size = 50
        cmd._running = True
        result = cmd._advance_running_workflows()
        self.assertEqual(result, 0)

    def test_advance_stops_on_shutdown(self):
        """收到stopsignal后中断剩余Advance"""
        from taurus.workflow.engine.runner import AdvanceTick

        mock_tick = AdvanceTick(
            execution_id=1, polled=0, newly_completed=0, newly_dispatched=0,
            newly_skipped=0, finished=False,
        )
        _FakeTaurusModels.WorkflowExecution.objects.filter.return_value.order_by.return_value.values_list.return_value = [
            301, 302, 303, 304
        ]

        call_count = {"n": 0}

        with patch("taurus.workflow.engine.runner.WorkflowRunner") as MockRunner:
            mock_runner = MockRunner.return_value

            def side_effect_impl(execution_id):
                call_count["n"] += 1
                if call_count["n"] >= 2:
                    cmd._running = False
                return mock_tick

            mock_runner.advance_workflow.side_effect = side_effect_impl

            cmd = self.Command()
            cmd.batch_size = 50
            cmd._running = True
            result = cmd._advance_running_workflows()
            self.assertEqual(result, 2)

    # ==================================================================
    # _reap_zombie_ops_executions
    # ==================================================================

    def test_reap_no_zombies(self):
        """Branch B:无僵尸 → return 0"""
        _FakeTaurusModels.OpsExecution.objects.filter.return_value.order_by.return_value.values_list.return_value = []

        cmd = self.Command()
        cmd.batch_size = 50
        cmd.ops_pending_max_age = 15.0
        cmd._running = True
        result = cmd._reap_zombie_ops_executions()
        self.assertEqual(result, 0)

    def test_reap_zombie_success(self):
        """Branch D:僵尸Successre-派发"""
        fake_now = datetime.datetime.now()
        ops1 = MagicMock()
        ops1.pk = 1
        ops1.execution_id = "exec-001"
        ops1.status = 0
        ops1.create_datetime = fake_now - timedelta(seconds=60)
        ops1.host_id = None
        ops1.host = None

        # 构造 values_list return值
        _FakeTaurusModels.OpsExecution.objects.filter.return_value.order_by.return_value.values_list.return_value = [
            (1, "exec-001", ops1.create_datetime)
        ]

        sf_mock = MagicMock()
        sf_mock.filter.return_value.first.return_value = ops1
        _FakeTaurusModels.OpsExecution.objects.select_for_update.return_value = sf_mock

        # 关键:patch django.utils.timezone.now 使其return与 create_datetime 兼容的 datetime
        with patch("taurus.management.commands.run_workflow_scheduler.timezone") as mock_tz, \
             patch.object(self.Command, "_resubmit_ops_execution") as mock_resubmit:
            mock_tz.now.return_value = fake_now
            mock_tz.timedelta = timedelta

            cmd = self.Command()
            cmd.batch_size = 50
            cmd.ops_pending_max_age = 15.0
            cmd._running = True
            result = cmd._reap_zombie_ops_executions()
            self.assertEqual(result, 1)
            mock_resubmit.assert_called_once_with(ops1)

    def test_reap_zombie_skipped_status_changed(self):
        """Branch C:僵尸已被其他ProcessUpdate → Skip"""
        now = datetime.datetime.now(datetime.timezone.utc)
        ops1 = MagicMock()
        ops1.pk = 1
        ops1.execution_id = "exec-001"
        ops1.status = 1  # 已变为 RUNNING
        ops1.create_datetime = now - timedelta(seconds=60)
        ops1.host_id = None
        ops1.host = None

        _FakeTaurusModels.OpsExecution.objects.filter.return_value.order_by.return_value.values_list.return_value = [
            (1, "exec-001", ops1.create_datetime)
        ]

        sf_mock = MagicMock()
        sf_mock.filter.return_value.first.return_value = ops1
        _FakeTaurusModels.OpsExecution.objects.select_for_update.return_value = sf_mock

        with patch.object(self.Command, "_resubmit_ops_execution") as mock_resubmit:
            cmd = self.Command()
            cmd.batch_size = 50
            cmd.ops_pending_max_age = 15.0
            cmd._running = True
            result = cmd._reap_zombie_ops_executions()
            self.assertEqual(result, 0)
            mock_resubmit.assert_not_called()

    def test_reap_zombie_db_exception(self):
        """Query僵尸抛exception → return 0"""
        _FakeTaurusModels.OpsExecution.objects.filter.side_effect = RuntimeError("DB挂了")

        cmd = self.Command()
        cmd.batch_size = 50
        cmd.ops_pending_max_age = 15.0
        cmd._running = True
        result = cmd._reap_zombie_ops_executions()
        self.assertEqual(result, 0)

    # ==================================================================
    # _resubmit_ops_execution
    # ==================================================================

    def test_resubmit_ws_loop_available_success(self):
        """Branch A:ws loop 可用, submit_execution Success → 不改Status"""
        ops = MagicMock()
        ops.pk = 1
        ops.execution_id = "exec-001"
        ops.status = 0
        ops.timeout_seconds = 300

        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False

        with patch("taurus.websocket_async.get_event_loop", return_value=mock_loop), \
             patch("taurus.websocket_async.submit_execution", return_value=True), \
             patch("taurus.websocket_async._execute_ops_async", return_value=MagicMock()):
            self.Command._resubmit_ops_execution(ops)
            self.assertEqual(ops.status, 0)

    def test_resubmit_ws_loop_none_fallback(self):
        """Branch B:ws loop=None → 进入phase 2"""
        ops = MagicMock()
        ops.pk = 1
        ops.execution_id = "exec-001"
        ops.status = 0
        ops.timeout_seconds = 300

        with patch("taurus.websocket_async.get_event_loop", return_value=None):
            self.Command._resubmit_ops_execution(ops)
            self.assertEqual(ops.status, 1)

    def test_resubmit_ws_loop_closed_fallback(self):
        """Branch B:ws loop 已Close → 进入phase 2"""
        ops = MagicMock()
        ops.pk = 1
        ops.execution_id = "exec-001"
        ops.status = 0
        ops.timeout_seconds = 300

        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = True

        with patch("taurus.websocket_async.get_event_loop", return_value=mock_loop):
            self.Command._resubmit_ops_execution(ops)
            self.assertEqual(ops.status, 1)

    def test_resubmit_submit_failed_fallback(self):
        """Branch B:submit return False → 进入phase 2"""
        ops = MagicMock()
        ops.pk = 1
        ops.execution_id = "exec-001"
        ops.status = 0
        ops.timeout_seconds = 300

        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False

        with patch("taurus.websocket_async.get_event_loop", return_value=mock_loop), \
             patch("taurus.websocket_async.submit_execution", return_value=False), \
             patch("taurus.websocket_async._execute_ops_async", return_value=MagicMock()):
            self.Command._resubmit_ops_execution(ops)
            self.assertEqual(ops.status, 1)

    def test_resubmit_submit_exception_fallback(self):
        """Branch B:submit 抛exception → 进入phase 2"""
        ops = MagicMock()
        ops.pk = 1
        ops.execution_id = "exec-001"
        ops.status = 0
        ops.timeout_seconds = 300

        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False

        with patch("taurus.websocket_async.get_event_loop", return_value=mock_loop), \
             patch("taurus.websocket_async.submit_execution", side_effect=RuntimeError("异常")), \
             patch("taurus.websocket_async._execute_ops_async", return_value=MagicMock()):
            self.Command._resubmit_ops_execution(ops)
            self.assertEqual(ops.status, 1)

    def test_resubmit_stage2_thread_started(self):
        """Branch B:phase 2 → start后台line程"""
        ops = MagicMock()
        ops.pk = 1
        ops.execution_id = "exec-001"
        ops.status = 0
        ops.timeout_seconds = 300

        # _resubmit_ops_execution internal有 `import threading`, 
        # require patch Standardlibrary的 threading.Thread
        with patch("threading.Thread") as MockThread:
            mock_thread = MockThread.return_value
            self.Command._resubmit_ops_execution(ops)
            self.assertEqual(ops.status, 1)
            MockThread.assert_called_once()
            mock_thread.start.assert_called_once()

    # ==================================================================
    # _shutdown
    # ==================================================================

    def test_shutdown_sets_running_false(self):
        cmd = self.Command()
        cmd._running = True
        cmd._shutdown(15, None)
        self.assertFalse(cmd._running)

        cmd._running = True
        cmd._shutdown(2, None)
        self.assertFalse(cmd._running)

    # ==================================================================
    # handle() 集成
    # ==================================================================

    def test_handle_once_mode(self):
        with patch.object(self.Command, "_advance_running_workflows", return_value=5), \
             patch.object(self.Command, "_reap_zombie_ops_executions", return_value=2):
            cmd = self.Command()
            cmd.handle(
                interval=0.01, batch_size=50,
                ops_reap_interval=0.01, ops_pending_max_age=5.0,
                daemon=True, once=True,
            )

    def test_handle_daemon_quick_shutdown(self):
        with patch.object(self.Command, "_advance_running_workflows", return_value=0), \
             patch.object(self.Command, "_reap_zombie_ops_executions", return_value=0):
            cmd = self.Command()
            cmd._running = True
            call_count = {"n": 0}

            def fake_sleep(t):
                call_count["n"] += 1
                if call_count["n"] >= 1:
                    cmd._running = False

            with patch("time.sleep", side_effect=fake_sleep):
                cmd.handle(
                    interval=0.01, batch_size=50,
                    ops_reap_interval=999.0, ops_pending_max_age=5.0,
                    daemon=True, once=False,
                )
            self.assertFalse(cmd._running)


if __name__ == "__main__":
    unittest.main(verbosity=2)