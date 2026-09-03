"""S2-04 Program AdapterUnit test."""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.django_db


def _make_host(host_name, host_ip):
    """create Host 行, match test_runner.py 的模式."""
    from taurus.models import Host
    return Host.objects.create(
        host_name=host_name,
        host_ip=host_ip,
        status=1,
    )


class TestProgramAdapterClassAttrs:
    """program Adapterclassattributecheck."""

    def test_registration_and_flags(self):
        from taurus.workflow.engine.registry import get_registry

        r = get_registry()
        assert r.has("program")
        cls = r.get_class("program")
        assert cls.node_type == "program"
        assert cls.display_name == "程序管理"
        assert cls.requires_host is True
        assert cls.is_asynchronous_human is False

    def test_instantiate(self):
        from taurus.workflow.engine.registry import get_registry
        adapter = get_registry().instantiate("program")
        assert adapter is not None


class TestProgramAdapterValidateConfig:
    """staticcheck逻辑."""

    @pytest.fixture()
    def adapter(self):
        from taurus.workflow.engine.registry import get_registry
        return get_registry().instantiate("program")

    def test_validate_invalid_action(self, adapter):
        r = adapter.validate_config({"action": "reboot", "program_name": "app"})
        assert r.ok is False
        assert "/params/action" in (r.errors or {})
        assert "E0201" in str(r.errors)

    def test_validate_empty_program_name(self, adapter):
        r = adapter.validate_config({"action": "start", "program_name": ""})
        assert r.ok is False
        assert "/params/program_name" in (r.errors or {})
        assert "E0202" in str(r.errors)

    def test_validate_upgrade_without_target_version(self, adapter):
        r = adapter.validate_config({
            "action": "upgrade",
            "program_name": "app",
        })
        assert r.ok is False
        assert "/params/target_version" in (r.errors or {})
        assert "E0203" in str(r.errors)

    @pytest.mark.parametrize("action", ["install", "start", "stop", "restart", "remove"])
    def test_validate_accepts_action_without_target_version(self, adapter, action):
        r = adapter.validate_config({
            "action": action,
            "program_name": "app",
        })
        assert r.ok is True

    def test_validate_upgrade_with_target_version_ok(self, adapter):
        r = adapter.validate_config({
            "action": "upgrade",
            "program_name": "app",
            "target_version": "v2.0.0",
        })
        assert r.ok is True

    def test_validate_timeout_seconds_out_of_range(self, adapter):
        r = adapter.validate_config({
            "action": "start",
            "program_name": "app",
            "timeout_seconds": 999999,
        })
        assert r.ok is False
        assert "/params/timeout_seconds" in (r.errors or {})

    def test_validate_config_not_dict(self, adapter):
        r = adapter.validate_config({
            "action": "start",
            "program_name": "app",
            "config": [1, 2, 3],
        })
        assert r.ok is False
        assert "/params/config" in (r.errors or {})


class TestProgramAdapterRender:
    """InterpolateRender."""

    @pytest.fixture()
    def adapter(self):
        from taurus.workflow.engine.registry import get_registry
        return get_registry().instantiate("program")

    def test_render_interpolates_program_version(self, adapter):
        from taurus.workflow.engine.context import WorkflowContext

        ctx = WorkflowContext(
            workflow_env={"PROG_VERSION": "v3.1.0", "PROG_NAME": "taurus-executor"},
        )
        params = {
            "action": "upgrade",
            "program_name": "${workflow.env.PROG_NAME}",
            "target_version": "${workflow.env.PROG_VERSION}",
        }
        rp = adapter.validate_and_render(params, ctx)
        assert rp["program_name"] == "taurus-executor"
        assert rp["target_version"] == "v3.1.0"


def _mk_cfg(host, params, dispatch_id="d-1"):
    from taurus.workflow.engine.schemas import RenderedNodeConfig
    return RenderedNodeConfig(
        execution_id="exec-1",
        node_key="p1",
        node_name="p1",
        host_id=host.pk,
        dispatch_id=dispatch_id,
        attempt_no=1,
        user_id=1,
        global_timeout_sec=600,
        secrets_mask=[],
        params=params,
        triggered_at="2026-01-01T00:00:00",
    )


class TestProgramAdapterDispatch:
    """dispatch create ProgramCommand DB 行并return RUNNING."""

    @pytest.fixture()
    def adapter(self):
        from taurus.workflow.engine.registry import get_registry
        return get_registry().instantiate("program")

    @pytest.fixture()
    def host(self):
        return _make_host("wf-prog-host-1", "10.9.9.9")

    def test_dispatch_creates_program_command_and_returns_running(self, adapter, host):
        from taurus.workflow.engine.schemas import STATUS_RUNNING
        from taurus.models import ProgramCommand

        params = {
            "action": "install",
            "program_name": "taurus-executor",
            "target_version": "v1.0.0",
        }
        cfg = _mk_cfg(host, params, dispatch_id="d-p1")

        out = adapter.dispatch(cfg)
        assert out.status == STATUS_RUNNING
        cmd_id = out.adapter_state["program_command_id"]
        cmd = ProgramCommand.objects.get(pk=cmd_id)
        assert cmd.action == "install"
        assert cmd.program_name == "taurus-executor"
        assert cmd.target_version == "v1.0.0"
        assert cmd.host_id == host.pk

    def test_dispatch_idempotent_no_duplicate_exception(self, adapter, host):
        """多次 dispatch(同一  dispatch_id)不应raised byexception."""
        from taurus.models import ProgramCommand

        params = {"action": "start", "program_name": "nginx"}
        cfg1 = _mk_cfg(host, params, dispatch_id="d-p2")
        out1 = adapter.dispatch(cfg1)
        out2 = adapter.dispatch(cfg1)

        assert ProgramCommand.objects.filter(
            program_name="nginx", action="start", host_id=host.pk
        ).count() >= 1

    def test_dispatch_without_host_id_returns_failed(self, adapter, host):
        from taurus.workflow.engine.schemas import RenderedNodeConfig, STATUS_FAILED

        cfg = RenderedNodeConfig(
            execution_id="exec-1",
            node_key="p2",
            node_name="p2",
            host_id=None,
            dispatch_id="d-p3",
            attempt_no=1,
            user_id=1,
            global_timeout_sec=600,
            secrets_mask=[],
            params={"action": "start", "program_name": "app"},
            triggered_at="2026-01-01T00:00:00",
        )
        out = adapter.dispatch(cfg)
        assert out.status == STATUS_FAILED
        assert out.error_message and "E0301" in out.error_message


class TestProgramAdapterPollTerminal:
    """poll according to ProgramCommand.status 回写Terminal state."""

    @pytest.fixture()
    def adapter(self):
        from taurus.workflow.engine.registry import get_registry
        return get_registry().instantiate("program")

    @pytest.fixture()
    def host(self):
        return _make_host("wf-prog-host-2", "10.9.9.10")

    def test_poll_returns_success_when_status_2(self, adapter, host):
        from taurus.models import ProgramCommand
        from taurus.workflow.engine.schemas import STATUS_SUCCESS

        cmd = ProgramCommand.objects.create(
            host=host,
            program_name="app",
            action="install",
            status=2,
            dispatched=True,
            result_message="安装成功",
            creator_id=1,
            modifier="1",
        )
        out = adapter.poll(
            _mk_cfg(host, {"action": "install", "program_name": "app"}),
            {"program_command_id": cmd.pk},
        )
        assert out.status == STATUS_SUCCESS
        assert out.output["program_command_id"] == cmd.pk
        assert out.exit_code == 0

    def test_poll_returns_failed_when_status_3(self, adapter, host):
        from taurus.models import ProgramCommand
        from taurus.workflow.engine.schemas import STATUS_FAILED

        cmd = ProgramCommand.objects.create(
            host=host,
            program_name="app",
            action="start",
            status=3,
            dispatched=True,
            result_message="启动失败：端口被占用",
            creator_id=1,
            modifier="1",
        )
        out = adapter.poll(
            _mk_cfg(host, {"action": "start", "program_name": "app"}),
            {"program_command_id": cmd.pk},
        )
        assert out.status == STATUS_FAILED
        assert out.error_message and "E4001" in out.error_message
        assert "端口被占用" in (out.error_message or "")
        assert out.exit_code == 1

    def test_poll_missing_state_returns_failed(self, adapter, host):
        from taurus.workflow.engine.schemas import STATUS_FAILED
        out = adapter.poll(_mk_cfg(host, {}), None)
        assert out.status == STATUS_FAILED
        assert out.error_message and "E4001" in out.error_message


class TestProgramAdapterCancel:
    """cancel 将Running ProgramCommand 标记为 ABORTED FAILED."""

    @pytest.fixture()
    def adapter(self):
        from taurus.workflow.engine.registry import get_registry
        return get_registry().instantiate("program")

    @pytest.fixture()
    def host(self):
        return _make_host("wf-prog-host-3", "10.9.9.11")

    def test_cancel_running_marks_failed(self, adapter, host):
        from taurus.models import ProgramCommand
        from taurus.workflow.engine.schemas import STATUS_CANCELLED

        cmd = ProgramCommand.objects.create(
            host=host, program_name="app", action="upgrade",
            status=1, dispatched=True,
            creator_id=1, modifier="1",
        )
        out = adapter.cancel(
            _mk_cfg(host, {}), {"program_command_id": cmd.pk},
        )
        assert out.status == STATUS_CANCELLED
        cmd.refresh_from_db()
        assert cmd.status == 3
        assert "ABORTED" in (cmd.result_message or "")

    def test_cancel_terminal_success_is_noop(self, adapter, host):
        from taurus.models import ProgramCommand
        from taurus.workflow.engine.schemas import STATUS_SUCCESS

        cmd = ProgramCommand.objects.create(
            host=host, program_name="app", action="stop",
            status=2, dispatched=True, result_message="ok",
            creator_id=1, modifier="1",
        )
        out = adapter.cancel(
            _mk_cfg(host, {}), {"program_command_id": cmd.pk},
        )
        assert out.status == STATUS_SUCCESS
        cmd.refresh_from_db()
        assert cmd.status == 2

    def test_cancel_without_state_is_noop_success(self, adapter, host):
        from taurus.workflow.engine.schemas import STATUS_SUCCESS
        out = adapter.cancel(_mk_cfg(host, {}), None)
        assert out.status == STATUS_SUCCESS