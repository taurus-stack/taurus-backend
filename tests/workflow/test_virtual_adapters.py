"""S2-07 虚拟节.(start / end / noop)AdapterUnit test."""
from __future__ import annotations

import pytest


class TestVirtualAdapterRegistration:
    """三种虚拟Adapter都已Register."""

    @pytest.mark.parametrize("node_type,display_name", [
        ("start", "开始"),
        ("end", "结束"),
        ("noop", "空操作"),
    ])
    def test_registered_with_correct_attrs(self, node_type, display_name):
        from taurus.workflow.engine.registry import get_registry

        r = get_registry()
        assert r.has(node_type)
        cls = r.get_class(node_type)
        assert cls.node_type == node_type
        assert cls.display_name == display_name
        assert cls.requires_host is False
        assert cls.is_asynchronous_human is False


def _mk_cfg(node_key, params, dispatch_id="d-1", host_id=None):
    """构造符合 RenderedNodeConfig dataclass 的testconfig."""
    from taurus.workflow.engine.schemas import RenderedNodeConfig
    return RenderedNodeConfig(
        execution_id="exec-1",
        node_key=node_key,
        node_name=node_key,
        host_id=host_id,
        dispatch_id=dispatch_id,
        attempt_no=1,
        user_id=1,
        global_timeout_sec=30,
        secrets_mask=[],
        params=params,
        triggered_at="2026-01-01T00:00:00",
    )


class TestStartAdapter:
    """start 节. dispatch 瞬间return SUCCESS."""

    @pytest.fixture()
    def adapter(self):
        from taurus.workflow.engine.registry import get_registry
        return get_registry().instantiate("start")

    def test_validate_config_always_passes(self, adapter):
        r = adapter.validate_config({})
        assert r.ok is True

    def test_dispatch_returns_success_with_started_at(self, adapter):
        from taurus.workflow.engine.schemas import STATUS_SUCCESS

        cfg = _mk_cfg("start", {})
        out = adapter.dispatch(cfg)
        assert out.status == STATUS_SUCCESS
        assert "started_at" in out.output
        assert out.exit_code == 0

    def test_poll_returns_success(self, adapter):
        from taurus.workflow.engine.schemas import STATUS_SUCCESS

        cfg = _mk_cfg("start", {}, dispatch_id="d-2")
        out = adapter.poll(cfg, None)
        assert out.status == STATUS_SUCCESS

    def test_cancel_returns_success(self, adapter):
        from taurus.workflow.engine.schemas import STATUS_SUCCESS

        cfg = _mk_cfg("start", {}, dispatch_id="d-3")
        out = adapter.cancel(cfg, None)
        assert out.status == STATUS_SUCCESS


class TestEndAdapter:
    """end 节. dispatch 瞬间return SUCCESS."""

    @pytest.fixture()
    def adapter(self):
        from taurus.workflow.engine.registry import get_registry
        return get_registry().instantiate("end")

    def test_dispatch_returns_success_with_finished_at(self, adapter):
        from taurus.workflow.engine.schemas import STATUS_SUCCESS

        cfg = _mk_cfg("end", {}, dispatch_id="d-4")
        out = adapter.dispatch(cfg)
        assert out.status == STATUS_SUCCESS
        assert "finished_at" in out.output


class TestNoopAdapter:
    """noop 节.回显 params.value."""

    @pytest.fixture()
    def adapter(self):
        from taurus.workflow.engine.registry import get_registry
        return get_registry().instantiate("noop")

    def test_validate_config_accepts_json_value(self, adapter):
        r = adapter.validate_config({"value": "hello"})
        assert r.ok is True

        r2 = adapter.validate_config({"value": [1, 2, 3]})
        assert r2.ok is True

    def test_validate_config_rejects_non_json_value(self, adapter):
        r = adapter.validate_config({"value": object()})
        assert r.ok is False
        assert "/params/value" in (r.errors or {})

    def test_dispatch_echoes_value(self, adapter):
        from taurus.workflow.engine.schemas import STATUS_SUCCESS

        cfg = _mk_cfg("n1", {"value": "hello"}, dispatch_id="d-5")
        out = adapter.dispatch(cfg)
        assert out.status == STATUS_SUCCESS
        assert out.output["echo"] == "hello"

    def test_dispatch_default_echo_when_no_value(self, adapter):
        from taurus.workflow.engine.schemas import STATUS_SUCCESS

        cfg = _mk_cfg("n2", {}, dispatch_id="d-6")
        out = adapter.dispatch(cfg)
        assert out.status == STATUS_SUCCESS
        assert out.output["echo"] == "noop"

    def test_dispatch_uses_copy_field_first(self, adapter):
        from taurus.workflow.engine.schemas import STATUS_SUCCESS

        cfg = _mk_cfg("n3", {"copy": "from_upstream", "value": "local"}, dispatch_id="d-7")
        out = adapter.dispatch(cfg)
        assert out.output["echo"] == "from_upstream"

    def test_render_interpolates_value(self, adapter):
        from taurus.workflow.engine.context import WorkflowContext

        ctx = WorkflowContext(workflow_env={"NAME": "world"})
        params = {"value": "hello-${workflow.env.NAME}"}
        rp = adapter.validate_and_render(params, ctx)
        assert rp["value"] == "hello-world"