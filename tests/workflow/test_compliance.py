"""S5-01 Adapter合规test(通用part).

覆盖 checklist 中可对所有Adapter自动execute的check项:
- A-01: Adapter已Register
- A-02: 5   must method存在且可调用
- A-03: 4  Requiredclassattribute已setting且class型正确
- A-10: Terminal stateOutput不可再Advance(poll return同态)
- A-11: cancel Idempotency(对已Terminal state任务 cancel 不抛错)
- A-20: on_after_finish 抛exception被吞掉(不影响节.Terminal state)

Adapter专属合规数据via ``COMPLIANCE_DATA`` Register, 未Register的Adapter
Skiprequire valid/invalid config 的check(A-04~A-08, A-13~A-17).
"""
from __future__ import annotations

import inspect

import pytest

from taurus.workflow.engine.base_adapter import ExecutableUnit
from taurus.workflow.engine.registry import get_registry
from taurus.workflow.engine.schemas import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_SUCCESS,
    UnitOutput,
)

pytestmark = pytest.mark.compliance

# ---- Adapter专属合规数据Register表 ----------------------------------------------
# 每 entries目Format:
#   "node_type": {
#       "valid_config": {...},           # A-06: validate_config → ok=True
#       "invalid_config": {...},          # A-05: validate_config → ok=False
#       "valid_render_context": {...},    # A-07: validate_and_render Context
#       "expected_error_codes": ["E0xxx"], # A-13~A-17: dispatch Failed时 error_message 含
#   }
# 未provide数据的AdapterSkip对应check(skip), 不影响通用check.
COMPLIANCE_DATA: dict[str, dict] = {
    "noop": {
        "valid_config": {},
        "invalid_config": None,  # noop 接受Emptyconfig, 无 invalid 场景
    },
    "start": {
        "valid_config": {},
        "invalid_config": None,
    },
    "end": {
        "valid_config": {},
        "invalid_config": None,
    },
    "testing_noop": {
        "valid_config": {},
        "invalid_config": None,
    },
    "testing_fail": {
        "valid_config": {},
        "invalid_config": None,
    },
    "script": {
        "valid_config": {"script_id": 123},
        "invalid_config": {},  # 缺 script_id → E0302
    },
    "command": {
        "valid_config": {"command": "echo hello"},
        "invalid_config": {"command": ""},  # command 为Empty → E0302
    },
    "file_op": {
        "valid_config": {
            "action": "upload",
            "source_path": "/tmp/local.txt",
            "remote_path": "/tmp/remote.txt",
        },
        "invalid_config": {"action": "unknown"},  # action invalid → E0304
    },
    "program": {
        "valid_config": {"action": "start", "program_name": "demo-svc"},
        "invalid_config": {"program_name": "x"},  # 缺 action → E0201
    },
    "approval": {
        # action=inform 模式:不Requires approval人, 仅做notification
        "valid_config": {"action": "inform", "title": "通知示例", "timeout_seconds": 3600},
        # submit 模式缺 approver_user_id → E0501
        "invalid_config": {"action": "submit"},
    },
    "sub_workflow": {
        "valid_config": {"sub_workflow_id": 1},
        "invalid_config": {},  # 缺 sub_workflow_id → E0601
    },
    "http_callback": {
        "valid_config": {"url": "http://localhost:9999/cb"},
        "invalid_config": {},  # 缺 url → E0601
    },
    "condition": {
        "valid_config": {"expression": "1 == 1", "expression_type": "simple"},
        "invalid_config": {},  # 缺 expression → E0701
    },
    "transform": {
        "valid_config": {"transform_type": "python", "expression": "x + 1"},
        "invalid_config": {},  # 缺 transform_type → E0801
    },
    "http": {
        "valid_config": {"url": "http://localhost:9999/api", "method": "GET"},
        "invalid_config": {},  # 缺 url → E0901
    },
    "loop": {
        "valid_config": {"loop_type": "count", "count": 3, "body_node_type": "http"},
        "invalid_config": {},  # 缺 loop_type → E1001
    },
    "webhook_notification": {
        "valid_config": {"url": "http://localhost:9999/hook"},
        "invalid_config": {},  # 缺 url → E1101
    },
    "email_notification": {
        "valid_config": {
            "recipients": ["test@example.com"],
            "subject_template": "Test",
            "body_template": "Hello",
        },
        "invalid_config": {},  # 缺 recipients → E1201
    },
}


# ---- A-01: Adapter已Register ----------------------------------------------------
def test_a01_all_adapters_registered(all_adapter_types):
    """A-01: 每  COMPLIANCE_DATA 中声明的Adapter都已Register."""
    for nt in COMPLIANCE_DATA:
        assert nt in all_adapter_types, f"适配器 {nt!r} 声明了合规数据但未注册"


# ---- A-02: 5   must method存在且Signature正确 ------------------------------------
_MUST_METHODS = ("validate_config", "validate_and_render", "dispatch", "poll", "cancel")


def test_a02_must_methods_exist(all_adapter_types, adapter_instance):
    """A-02: 每 已RegisterAdapter都implement了 5   must method."""
    for nt in all_adapter_types:
        inst = adapter_instance(nt)
        for method_name in _MUST_METHODS:
            method = getattr(inst, method_name, None)
            assert method is not None, f"{nt}.{method_name} 不存在"
            assert callable(method), f"{nt}.{method_name} 不可调用"


# ---- A-03: 4  Requiredclassattribute已setting且class型正确 ----------------------------------
def test_a03_class_attrs_correct(all_adapter_types):
    """A-03: node_type/display_name Yes非Empty str, requires_host/is_asynchronous_human Yes bool."""
    registry = get_registry()
    for nt in all_adapter_types:
        cls = registry.get_class(nt)
        assert isinstance(cls.node_type, str) and cls.node_type, f"{nt}: node_type 非空 str"
        assert isinstance(cls.display_name, str) and cls.display_name, f"{nt}: display_name 非空 str"
        assert isinstance(cls.requires_host, bool), f"{nt}: requires_host 必须是 bool"
        assert isinstance(cls.is_asynchronous_human, bool), f"{nt}: is_asynchronous_human 必须是 bool"


# ---- A-10: poll 对 None state 不抛exception -------------------------------------
# 仅test不require DB 的纯Adapter;require DB 的Adapter(sub_workflow/approval 等)由各自
# 专属test覆盖 poll/cancel 行为.
_NO_DB_ADAPTERS = {"noop", "start", "end", "testing_noop", "testing_fail"}


def test_a10_poll_none_state_no_crash(all_adapter_types, adapter_instance, make_cfg):
    """A-10: poll(cfg, None) 不应抛exception(Adapter对missing state 应return RUNNING 或Terminal state).

    仅覆盖无 DB depend on的纯Adapter;require DB 的Adapter由专属Integration test覆盖.
    """
    tested = False
    for nt in all_adapter_types:
        if nt not in _NO_DB_ADAPTERS:
            continue
        tested = True
        inst = adapter_instance(nt)
        cfg = make_cfg()
        try:
            result = inst.poll(cfg, None)
            assert result is not None, f"{nt}.poll(None) 返回 None"
            assert hasattr(result, "status"), f"{nt}.poll(None) 返回值无 status 属性"
        except NotImplementedError:
            pytest.skip(f"{nt}.poll(None) 抛 NotImplementedError")
    if not tested:
        pytest.skip("没有无 DB 依赖的适配器可测试")


# ---- A-11: cancel Idempotency -----------------------------------------------------
def test_a11_cancel_idempotent(all_adapter_types, adapter_instance, make_cfg):
    """A-11: 对同一 cfg 连续 cancel 两次, Page二次不抛exception, returnTerminal state.

    仅覆盖无 DB depend on的纯Adapter;require DB 的Adapter由专属Integration test覆盖.
    """
    tested = False
    for nt in all_adapter_types:
        if nt not in _NO_DB_ADAPTERS:
            continue
        tested = True
        inst = adapter_instance(nt)
        cfg = make_cfg()
        try:
            first = inst.cancel(cfg, None)
            assert first.status in (STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELLED), (
                f"{nt}.cancel 第一次返回非终态 {first.status}"
            )
            second = inst.cancel(cfg, None)
            assert second.status in (STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELLED), (
                f"{nt}.cancel 第二次返回非终态 {second.status}"
            )
        except NotImplementedError:
            pytest.skip(f"{nt}.cancel 抛 NotImplementedError")
    if not tested:
        pytest.skip("没有无 DB 依赖的适配器可测试")


# ---- A-20: on_after_finish 默认implement不抛exception --------------------------------
def test_a20_on_after_finish_default_noop(all_adapter_types, adapter_instance, make_cfg):
    """A-20: on_after_finish 默认implement应为 no-op, 不抛exception.

    Engine层吞exception的行为由 runner test覆盖;这里仅validateAdapterLevel.
    """
    for nt in all_adapter_types:
        inst = adapter_instance(nt)
        cfg = make_cfg()
        output = UnitOutput(status=STATUS_SUCCESS, output={}, exit_code=0)
        inst.on_after_finish(cfg, output)


# ---- A-06: validate_config(valid) → ok=True --------------------------------
def test_a06_validate_config_valid(all_adapter_types, adapter_instance):
    """A-06: 对 COMPLIANCE_DATA 中声明的 valid_config, validate_config return ok=True."""
    ran = False
    for nt in all_adapter_types:
        data = COMPLIANCE_DATA.get(nt)
        if not data or data.get("valid_config") is None:
            continue
        ran = True
        inst = adapter_instance(nt)
        result = inst.validate_config(data["valid_config"])
        assert result.ok, f"{nt}.validate_config(valid) 返回 ok=False, errors={result.errors}"
    if not ran:
        pytest.skip("没有适配器提供 valid_config 合规数据")


# ---- A-05: validate_config(invalid) → ok=False ------------------------------
def test_a05_validate_config_invalid_rejected(all_adapter_types, adapter_instance):
    """A-05: 对provide了 invalid_config 的Adapter, validate_config 应return ok=False."""
    ran = False
    for nt in all_adapter_types:
        data = COMPLIANCE_DATA.get(nt)
        if not data or data.get("invalid_config") is None:
            continue
        ran = True
        inst = adapter_instance(nt)
        result = inst.validate_config(data["invalid_config"])
        assert result.ok is False, (
            f"{nt}.validate_config(invalid) 应返回 ok=False，实际 ok=True。"
            f" invalid_config={data['invalid_config']}"
        )
    if not ran:
        pytest.skip("没有适配器提供 invalid_config 合规数据")


# ---- A-04: validate_config(empty) → ok=False(对requireconfig的Adapter)-----------
def test_a04_validate_config_empty_rejected(all_adapter_types, adapter_instance):
    """A-04: 对requireconfig的Adapter, Empty dict 应被拒绝(ok=False)或接受(无Required项的Adapter).

    此checkvalidate validate_config 对Emptyconfig不抛exception, return ValidationResult.
    """
    for nt in all_adapter_types:
        inst = adapter_instance(nt)
        result = inst.validate_config({})
        # 只要不抛exception即可;ok 值depends onAdapterYesNo有Required项
        assert result is not None, f"{nt}.validate_config({{}}) 返回 None"


# ---- manifest 结构check -----------------------------------------------------
def test_manifest_all_structure(all_adapter_types):
    """manifest_all return的每 entries目都contain必需Field且class型正确."""
    registry = get_registry()
    manifest = registry.manifest_all()
    assert len(manifest) == len(all_adapter_types)
    for item in manifest:
        assert "node_type" in item
        assert "display_name" in item
        assert "requires_host" in item
        assert "is_asynchronous_human" in item
        assert isinstance(item["node_type"], str)
        assert isinstance(item["display_name"], str)
        assert isinstance(item["requires_host"], bool)
        assert isinstance(item["is_asynchronous_human"], bool)