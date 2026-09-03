"""validate TaurusConfig.ready() 里 INSTALLED_UNIT_ADAPTERS 被正确load, 
global单例 UnitAdapterRegistry 含 testing_noop / testing_fail.
Django environment由 tests/conftest.py initialize.
"""
from __future__ import annotations


def test_global_registry_contains_testing_adapters():
    """apps.ready 后globalRegister表里有 testing_noop / testing_fail."""
    from taurus.workflow.engine.registry import get_registry

    r = get_registry()
    types = r.list_all_adapter_types()
    assert "testing_noop" in types, (
        f"INSTALLED_UNIT_ADAPTERS 未生效，已注册类型: {types}"
    )
    assert "testing_fail" in types


def test_testing_noop_adapter_e2e_sync():
    """用global registry instance化 testing_noop 并跑完整:vcfg → validate_and_render → dispatch → poll → cancel."""
    from taurus.workflow.engine.context import WorkflowContext
    from taurus.workflow.engine.registry import get_registry
    from taurus.workflow.engine.schemas import (
        NO_HOST_SENTINEL,
        STATUS_SUCCESS,
        RenderedNodeConfig,
    )

    r = get_registry()
    a = r.instantiate("testing_noop")

    params = {"value": "hi-${workflow.env.NAME}"}
    v = a.validate_config(params)
    assert v.ok is True, v.errors

    ctx = WorkflowContext(workflow_env={"NAME": "taurus"})
    rendered_params = a.validate_and_render(params, ctx)
    assert rendered_params["value"] == "hi-taurus"

    cfg = RenderedNodeConfig(
        execution_id="exec-1",
        node_key="node_noop",
        node_name="noop",
        host_id=NO_HOST_SENTINEL,
        dispatch_id="did-noop-1",
        attempt_no=1,
        user_id=1,
        global_timeout_sec=60,
        secrets_mask=[],
        params=rendered_params,
        triggered_at="2026-08-05T00:00:00Z",
    )
    a.on_before_dispatch(cfg)
    out = a.dispatch(cfg)
    assert out.status == STATUS_SUCCESS
    assert out.output["echo"] == "hi-taurus"
    # Idempotency:同一 dispatch_id 再调用不报错
    out2 = a.dispatch(cfg)
    assert out2.status == STATUS_SUCCESS

    poll = a.poll(cfg, out.adapter_state)
    assert poll.is_terminal

    cancel = a.cancel(cfg, None)
    assert cancel.is_terminal

    # Terminal statehook正常被调用, 不抛exception
    a.on_after_finish(cfg, out)


def test_testing_fail_adapter_error_code_prefix_in_message():
    """testing_fail must在 error_message 首Field带 E0xxx/E2xxx/E3xxx."""
    from taurus.workflow.engine.context import WorkflowContext
    from taurus.workflow.engine.registry import get_registry
    from taurus.workflow.engine.schemas import (
        NO_HOST_SENTINEL,
        STATUS_FAILED,
        RenderedNodeConfig,
    )

    r = get_registry()
    a = r.instantiate("testing_fail")

    params = {"error_code": "E2401", "message": "远端主机不可达"}
    v = a.validate_config(params)
    assert v.ok is True

    ctx = WorkflowContext()
    rp = a.validate_and_render(params, ctx)
    cfg = RenderedNodeConfig(
        execution_id="exec-2",
        node_key="node_fail",
        node_name="fail",
        host_id=NO_HOST_SENTINEL,
        dispatch_id="did-fail-1",
        attempt_no=1,
        user_id=1,
        global_timeout_sec=60,
        secrets_mask=[],
        params=rp,
        triggered_at="2026",
    )
    out = a.dispatch(cfg)
    assert out.status == STATUS_FAILED
    assert out.error_message and out.error_message.startswith("E2401: ")
    # validate_config:invalid error_code 应报错
    bad = a.validate_config({"error_code": "WRONG"})
    assert bad.ok is False
    assert "/params/error_code" in (bad.errors or {})


def test_manifest_all_exposes_frontend_fields():
    """manifest_all() Fieldset供Frontend设计server绘制左侧面板."""
    from taurus.workflow.engine.registry import get_registry

    r = get_registry()
    manifest = r.manifest_all()
    assert isinstance(manifest, list) and len(manifest) >= 2
    by_type = {m["node_type"]: m for m in manifest}
    noop = by_type["testing_noop"]
    for field in ("node_type", "display_name", "requires_host", "is_asynchronous_human"):
        assert field in noop, f"manifest 缺少前端必需字段 {field}"
    assert isinstance(noop["requires_host"], bool)
    assert isinstance(noop["is_asynchronous_human"], bool)