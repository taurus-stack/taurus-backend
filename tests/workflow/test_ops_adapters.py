"""S2-01 / S2-02 / S2-03 Script, Command & FileOp AdapterUnit test.

只测纯逻辑part(validate_config / validate_and_render / classattribute), 
require真实 Host / OpsExecution 的 dispatch/poll/cancel 由Integration test覆盖.
"""
from __future__ import annotations

import pytest


class TestScriptAdapterClassAttrs:
    """ScriptAdapter classattributeRegister正确."""

    def test_script_adapter_registered_in_global_registry(self):
        from taurus.workflow.engine.registry import get_registry

        r = get_registry()
        assert r.has("script")
        cls = r.get_class("script")
        assert cls.node_type == "script"
        assert cls.display_name == "脚本执行"
        assert cls.requires_host is True
        assert cls.is_asynchronous_human is False

    def test_script_manifest_fields(self):
        from taurus.workflow.engine.registry import get_registry

        r = get_registry()
        manifest = {m["node_type"]: m for m in r.manifest_all()}
        s = manifest["script"]
        assert s["requires_host"] is True
        assert s["is_asynchronous_human"] is False


class TestCommandAdapterClassAttrs:
    """CommandAdapter classattributeRegister正确."""

    def test_command_adapter_registered_in_global_registry(self):
        from taurus.workflow.engine.registry import get_registry

        r = get_registry()
        assert r.has("command")
        cls = r.get_class("command")
        assert cls.node_type == "command"
        assert cls.display_name == "命令执行"
        assert cls.requires_host is True
        assert cls.is_asynchronous_human is False

    def test_command_manifest_fields(self):
        from taurus.workflow.engine.registry import get_registry

        r = get_registry()
        manifest = {m["node_type"]: m for m in r.manifest_all()}
        c = manifest["command"]
        assert c["requires_host"] is True
        assert c["is_asynchronous_human"] is False


class TestScriptAdapterValidateConfig:
    """ScriptAdapter.validate_config staticcheck."""

    @pytest.fixture()
    def script_adapter(self):
        from taurus.workflow.engine.registry import get_registry

        return get_registry().instantiate("script")

    def test_validate_requires_script_id_or_dynamic_content(self, script_adapter):
        """must传 script_id 或 script_overrides.script_content 两者之一."""
        # 都no → Failed
        bad_empty: dict = {}
        r = script_adapter.validate_config(bad_empty)
        assert r.ok is False
        assert "/params" in (r.errors or {})
        assert "E0302" in str(r.errors)

    def test_validate_script_id_only_passes(self, script_adapter):
        """只传 script_id, staticcheckvia(存在性check留给 dispatch 做 E0302)."""
        p = {"script_id": 123}
        r = script_adapter.validate_config(p)
        assert r.ok is True, r.errors

    def test_validate_dynamic_script_requires_type(self, script_adapter):
        """usedynamicScript时must指定Valid的 script_type ∈ {sh, python}, 缺省不填时报错."""
        p = {
            "script_overrides": {
                "script_content": "echo hello",
                # 缺 script_type → check报错, must显式指定
            }
        }
        r = script_adapter.validate_config(p)
        assert r.ok is False
        assert "/params/script_type" in (r.errors or {})

    def test_validate_dynamic_script_invalid_type_fails(self, script_adapter):
        """invalid script_type(非allowlist)报错."""
        p = {
            "script_overrides": {
                "script_content": "echo hello",
                "script_type": "ruby",
            }
        }
        r = script_adapter.validate_config(p)
        assert r.ok is False
        assert "/params/script_type" in (r.errors or {})

    def test_validate_valid_dynamic_script_python_passes(self, script_adapter):
        """python class型dynamicScriptvia."""
        p = {
            "script_overrides": {
                "script_content": "print('hi')",
                "script_type": "python",
            }
        }
        r = script_adapter.validate_config(p)
        assert r.ok is True

    def test_validate_timeout_seconds_out_of_range_fails(self, script_adapter):
        """timeout_seconds 超过 7 天或 ≤0 报错."""
        p = {"script_id": 1, "timeout_seconds": 0}
        r = script_adapter.validate_config(p)
        assert r.ok is False
        assert "/params/timeout_seconds" in (r.errors or {})

        p2 = {"script_id": 1, "timeout_seconds": 86400 * 8}
        r2 = script_adapter.validate_config(p2)
        assert r2.ok is False

    def test_validate_timeout_seconds_within_range_passes(self, script_adapter):
        p = {"script_id": 1, "timeout_seconds": 300}
        r = script_adapter.validate_config(p)
        assert r.ok is True

    def test_validate_args_must_be_list(self, script_adapter):
        p = {"script_id": 1, "args": "not-a-list"}  # type: ignore[dict-item]
        r = script_adapter.validate_config(p)
        assert r.ok is False
        assert "/params/args" in (r.errors or {})

    def test_validate_args_list_passes(self, script_adapter):
        p = {"script_id": 1, "args": ["a", "b"]}
        r = script_adapter.validate_config(p)
        assert r.ok is True

    def test_validate_environment_must_be_dict(self, script_adapter):
        p = {"script_id": 1, "environment": ["not", "a", "dict"]}  # type: ignore[dict-item]
        r = script_adapter.validate_config(p)
        assert r.ok is False
        assert "/params/environment" in (r.errors or {})

    def test_validate_environment_dict_passes(self, script_adapter):
        p = {"script_id": 1, "environment": {"FOO": "bar"}}
        r = script_adapter.validate_config(p)
        assert r.ok is True


class TestCommandAdapterValidateConfig:
    """CommandAdapter.validate_config staticcheck."""

    @pytest.fixture()
    def command_adapter(self):
        from taurus.workflow.engine.registry import get_registry

        return get_registry().instantiate("command")

    def test_validate_requires_command_non_empty(self, command_adapter):
        """command FieldRequired且cannotEmpty白."""
        r0 = command_adapter.validate_config({})
        assert r0.ok is False
        assert "/params/command" in (r0.errors or {})
        assert "E0302" in str(r0.errors)

        r1 = command_adapter.validate_config({"command": ""})
        assert r1.ok is False

        r2 = command_adapter.validate_config({"command": "   "})
        assert r2.ok is False

    def test_validate_simple_command_passes(self, command_adapter):
        p = {"command": "ls -la"}
        r = command_adapter.validate_config(p)
        assert r.ok is True, r.errors

    def test_validate_timeout_range(self, command_adapter):
        bad = {"command": "sleep 10", "timeout_seconds": -1}
        r = command_adapter.validate_config(bad)
        assert r.ok is False

        good = {"command": "sleep 10", "timeout_seconds": 100}
        r2 = command_adapter.validate_config(good)
        assert r2.ok is True

    def test_validate_args_and_environment_types(self, command_adapter):
        bad_args = {"command": "echo", "args": "foo"}  # type: ignore[dict-item]
        r = command_adapter.validate_config(bad_args)
        assert r.ok is False
        assert "/params/args" in (r.errors or {})

        bad_env = {"command": "echo", "environment": ["x"]}  # type: ignore[dict-item]
        r2 = command_adapter.validate_config(bad_env)
        assert r2.ok is False


class TestAdapterValidateAndRender:
    """validate_and_render Interpolate:environment/args 会被递归Interpolate."""

    def test_script_render_interpolates_environment_and_args(self):
        from taurus.workflow.engine.context import WorkflowContext
        from taurus.workflow.engine.registry import get_registry

        a = get_registry().instantiate("script")
        ctx = WorkflowContext(
            workflow_env={"APP_HOME": "/opt/taurus", "NAME": "hello"},
            trigger_params={"VER": "1.0"},
        )
        params = {
            "script_id": 42,
            "args": ["v=${trigger.VER}", "${workflow.env.NAME}"],
            "environment": {
                "HOME": "${workflow.env.APP_HOME}",
                "GREETING": "hi-${workflow.env.NAME}",
            },
            "working_directory": "${workflow.env.APP_HOME}/logs",
        }
        rp = a.validate_and_render(params, ctx)
        assert rp["args"][0] == "v=1.0"
        assert rp["args"][1] == "hello"
        assert rp["environment"]["HOME"] == "/opt/taurus"
        assert rp["environment"]["GREETING"] == "hi-hello"
        assert rp["working_directory"] == "/opt/taurus/logs"

    def test_command_render_interpolates_command_string(self):
        from taurus.workflow.engine.context import WorkflowContext
        from taurus.workflow.engine.registry import get_registry

        a = get_registry().instantiate("command")
        ctx = WorkflowContext(workflow_env={"BIN": "/usr/local/bin"})
        params = {"command": "${workflow.env.BIN}/python --version"}
        rp = a.validate_and_render(params, ctx)
        assert rp["command"] == "/usr/local/bin/python --version"

    def test_file_op_render_interpolates_paths(self):
        from taurus.workflow.engine.context import WorkflowContext
        from taurus.workflow.engine.registry import get_registry

        a = get_registry().instantiate("file_op")
        ctx = WorkflowContext(workflow_env={"REMOTE_DIR": "/data/uploads"})
        params = {
            "action": "upload",
            "source_path": "/tmp/local.txt",
            "remote_path": "${workflow.env.REMOTE_DIR}/remote.txt",
        }
        rp = a.validate_and_render(params, ctx)
        assert rp["remote_path"] == "/data/uploads/remote.txt"
        assert rp["source_path"] == "/tmp/local.txt"


class TestFileOpAdapterClassAttrs:
    """FileOpAdapter classattributeRegister正确."""

    def test_file_op_adapter_registered_in_global_registry(self):
        from taurus.workflow.engine.registry import get_registry

        r = get_registry()
        assert r.has("file_op")
        cls = r.get_class("file_op")
        assert cls.node_type == "file_op"
        assert cls.display_name == "文件传输"
        assert cls.requires_host is True
        assert cls.is_asynchronous_human is False

    def test_file_op_manifest_fields(self):
        from taurus.workflow.engine.registry import get_registry

        r = get_registry()
        manifest = {m["node_type"]: m for m in r.manifest_all()}
        f = manifest["file_op"]
        assert f["requires_host"] is True
        assert f["is_asynchronous_human"] is False


class TestFileOpAdapterValidateConfig:
    """FileOpAdapter.validate_config staticcheck."""

    @pytest.fixture()
    def file_op_adapter(self):
        from taurus.workflow.engine.registry import get_registry

        return get_registry().instantiate("file_op")

    def test_validate_requires_valid_action(self, file_op_adapter):
        """action must ∈ {upload, download}."""
        r = file_op_adapter.validate_config({})
        assert r.ok is False
        assert "/params/action" in (r.errors or {})
        assert "E0304" in str(r.errors)

        r2 = file_op_adapter.validate_config({"action": "copy", "remote_path": "/tmp/x"})
        assert r2.ok is False
        assert "/params/action" in (r2.errors or {})

    def test_validate_upload_requires_source_path(self, file_op_adapter):
        """action=upload 时 source_path Required."""
        r = file_op_adapter.validate_config({"action": "upload", "remote_path": "/tmp/x"})
        assert r.ok is False
        assert "/params/source_path" in (r.errors or {})

        r2 = file_op_adapter.validate_config({
            "action": "upload",
            "source_path": "/tmp/local.txt",
            "remote_path": "/tmp/x",
        })
        assert r2.ok is True, r2.errors

    def test_validate_download_does_not_require_source_path(self, file_op_adapter):
        """action=download 不require source_path."""
        r = file_op_adapter.validate_config({"action": "download", "remote_path": "/tmp/x"})
        assert r.ok is True, r.errors

    def test_validate_remote_path_required(self, file_op_adapter):
        """remote_path Required."""
        r = file_op_adapter.validate_config({"action": "download", "remote_path": ""})
        assert r.ok is False
        assert "/params/remote_path" in (r.errors or {})

        r2 = file_op_adapter.validate_config({"action": "download", "remote_path": "   "})
        assert r2.ok is False

    def test_validate_timeout_range(self, file_op_adapter):
        """timeout_seconds 范围check."""
        bad = {"action": "download", "remote_path": "/tmp/x", "timeout_seconds": 0}
        r = file_op_adapter.validate_config(bad)
        assert r.ok is False
        assert "/params/timeout_seconds" in (r.errors or {})

        bad2 = {"action": "download", "remote_path": "/tmp/x", "timeout_seconds": 86400 * 8}
        r2 = file_op_adapter.validate_config(bad2)
        assert r2.ok is False

        good = {"action": "download", "remote_path": "/tmp/x", "timeout_seconds": 600}
        r3 = file_op_adapter.validate_config(good)
        assert r3.ok is True


class TestOpsExecutionFailStrategyManifest:
    """test ops execute策略Field的后端constant和classattribute.

    manifest_all() 不return params 细节(由Frontend TS manifest Definition), 
    这里validate后端constant, Adapterattribute和 dispatch 默认值.
    """

    def test_fail_strategy_valid_values_constant(self):
        """FAIL_STRATEGY_VALUES constant应contain stop/continue."""
        from taurus.workflow.units.ops_execution import FAIL_STRATEGY_VALUES
        assert "stop" in FAIL_STRATEGY_VALUES
        assert "continue" in FAIL_STRATEGY_VALUES
        assert "abort" not in FAIL_STRATEGY_VALUES

    def test_exec_mode_valid_values_constant(self):
        """EXEC_MODE_VALUES constant应contain serial/parallel/pilot."""
        from taurus.workflow.units.ops_execution import EXEC_MODE_VALUES
        assert "serial" in EXEC_MODE_VALUES
        assert "parallel" in EXEC_MODE_VALUES
        assert "pilot" in EXEC_MODE_VALUES

    def test_script_adapter_class_attributes(self):
        """ScriptAdapter classattributeRegister正确."""
        from taurus.workflow.engine.registry import get_registry
        r = get_registry()
        cls = r.get_class("script")
        assert cls.node_type == "script"
        assert cls.display_name == "脚本执行"
        assert cls.requires_host is True

    def test_command_adapter_class_attributes(self):
        """CommandAdapter classattributeRegister正确."""
        from taurus.workflow.engine.registry import get_registry
        r = get_registry()
        cls = r.get_class("command")
        assert cls.node_type == "command"
        assert cls.display_name == "命令执行"
        assert cls.requires_host is True


class TestOpsExecutionFailStrategyValidate:
    """test ops_fail_strategy 及relatedField的 validate_config check."""

    @pytest.fixture()
    def script_adapter(self):
        from taurus.workflow.engine.registry import get_registry
        return get_registry().instantiate("script")

    @pytest.fixture()
    def command_adapter(self):
        from taurus.workflow.engine.registry import get_registry
        return get_registry().instantiate("command")

    def test_validate_script_accepts_valid_fail_strategy_stop(self, script_adapter):
        """ops_fail_strategy=stop checkvia."""
        p = {"script_id": 1, "ops_fail_strategy": "stop"}
        r = script_adapter.validate_config(p)
        assert r.ok is True

    def test_validate_script_accepts_valid_fail_strategy_continue(self, script_adapter):
        """ops_fail_strategy=continue checkvia."""
        p = {"script_id": 1, "ops_fail_strategy": "continue"}
        r = script_adapter.validate_config(p)
        assert r.ok is True

    def test_validate_script_invalid_fail_strategy_value_fails(self, script_adapter):
        """invalid ops_fail_strategy 值(如 abort)应报错."""
        p = {"script_id": 1, "ops_fail_strategy": "abort"}
        r = script_adapter.validate_config(p)
        assert r.ok is False

    def test_validate_command_accepts_valid_fail_strategy(self, command_adapter):
        p = {"command": "ls", "ops_fail_strategy": "continue"}
        r = command_adapter.validate_config(p)
        assert r.ok is True

    def test_validate_command_invalid_fail_strategy_fails(self, command_adapter):
        p = {"command": "ls", "ops_fail_strategy": "invalid_value"}
        r = command_adapter.validate_config(p)
        assert r.ok is False

    def test_validate_invalid_exec_mode_fails(self, script_adapter):
        """invalid exec_mode 应报错."""
        p = {"script_id": 1, "exec_mode": "invalid"}
        r = script_adapter.validate_config(p)
        assert r.ok is False

    def test_validate_valid_exec_mode_values_pass(self, script_adapter):
        """Valid exec_mode(serial/parallel/pilot)应via."""
        for mode in ("serial", "parallel", "pilot"):
            p = {"script_id": 1, "exec_mode": mode}
            r = script_adapter.validate_config(p)
            assert r.ok is True, f"exec_mode={mode} should pass but got: {r.errors}"

    def test_validate_concurrent_out_of_range_fails(self, script_adapter):
        """Concurrency超过 50 或小于 1 报错."""
        p_over = {"script_id": 1, "concurrent": 51}
        r1 = script_adapter.validate_config(p_over)
        assert r1.ok is False

        p_under = {"script_id": 1, "concurrent": 0}
        r2 = script_adapter.validate_config(p_under)
        assert r2.ok is False

    def test_validate_concurrent_within_range_passes(self, script_adapter):
        p = {"script_id": 1, "concurrent": 10}
        r = script_adapter.validate_config(p)
        assert r.ok is True

    def test_validate_pilot_count_out_of_range_fails(self, script_adapter):
        """canaryvalidateHost数超过 10 或小于 1 报错."""
        p_over = {"script_id": 1, "exec_mode": "pilot", "pilot_count": 11}
        r1 = script_adapter.validate_config(p_over)
        assert r1.ok is False

        p_under = {"script_id": 1, "exec_mode": "pilot", "pilot_count": 0}
        r2 = script_adapter.validate_config(p_under)
        assert r2.ok is False

    def test_validate_pilot_success_rate_out_of_range_fails(self, script_adapter):
        """canarySuccess阈值超过 100 或小于 1 报错."""
        p_over = {"script_id": 1, "pilot_success_rate": 101}
        r1 = script_adapter.validate_config(p_over)
        assert r1.ok is False

        p_under = {"script_id": 1, "pilot_success_rate": 0}
        r2 = script_adapter.validate_config(p_under)
        assert r2.ok is False


class TestOpsExecutionFailStrategyClamp:
    """test后端 dispatch phase的数值钳位逻辑.

    clamp 逻辑在 ops_execution.py 的 dispatch method内, 
    use与 dispatch 相同的 `if val is not None` 模式validate.
    """

    @staticmethod
    def _clamp_int(val, default, lo, hi):
        """Mock dispatch 中的钳位逻辑."""
        v = int(val if val is not None else default)
        return max(lo, min(v, hi))

    @staticmethod
    def _clamp_int_or(val, default, lo, hi):
        """Mock旧版 `or` 语义(test Python falsy 行为)."""
        v = int(val or default)
        return max(lo, min(v, hi))

    def test_clamp_concurrent_over_max(self):
        """Concurrency超过 50 时被钳位到 50."""
        from taurus.workflow.units.ops_execution import _normalize_frontend_params
        p = _normalize_frontend_params({"concurrent": 999})
        val = p.get("concurrent")
        concurrent = self._clamp_int(val, 10, 1, 50)
        assert concurrent == 50

    def test_clamp_concurrent_under_min(self):
        """Concurrency小于 1 时被钳位到 1."""
        from taurus.workflow.units.ops_execution import _normalize_frontend_params
        p = _normalize_frontend_params({"concurrent": 0})
        val = p.get("concurrent")
        concurrent = self._clamp_int(val, 10, 1, 50)
        assert concurrent == 1

    def test_clamp_concurrent_zero_not_treated_as_default(self):
        """concurrent=0 不应被 `or` 误Parse为默认值 10."""
        from taurus.workflow.units.ops_execution import _normalize_frontend_params
        p = _normalize_frontend_params({"concurrent": 0})
        val = p.get("concurrent")
        # 旧版 `or` 语义:0 or 10 = 10(Python falsy)
        old_result = self._clamp_int_or(val, 10, 1, 50)
        assert old_result == 10
        # 新版 `if not None` 语义:0 保持为 0, 再被 clamp 到 1
        new_result = self._clamp_int(val, 10, 1, 50)
        assert new_result == 1

    def test_clamp_concurrent_normal_passthrough(self):
        """Concurrency在Valid范围内时不被modify."""
        from taurus.workflow.units.ops_execution import _normalize_frontend_params
        p = _normalize_frontend_params({"concurrent": 20})
        val = p.get("concurrent")
        concurrent = self._clamp_int(val, 10, 1, 50)
        assert concurrent == 20

    def test_clamp_timeout_over_max(self):
        """timeout超过 86400 秒被钳位."""
        from taurus.workflow.units.ops_execution import _normalize_frontend_params
        p = _normalize_frontend_params({"timeout": 86400 * 8})
        val = p.get("timeout_seconds")
        timeout = self._clamp_int(val, 300, 10, 86400)
        assert timeout == 86400

    def test_clamp_timeout_under_min(self):
        """timeout小于 10 秒被钳位."""
        from taurus.workflow.units.ops_execution import _normalize_frontend_params
        p = _normalize_frontend_params({"timeout": 5})
        val = p.get("timeout_seconds")
        timeout = self._clamp_int(val, 300, 10, 86400)
        assert timeout == 10

    def test_clamp_pilot_count_over_max(self):
        """canaryvalidateHost数超过 10 被钳位."""
        from taurus.workflow.units.ops_execution import _normalize_frontend_params
        p = _normalize_frontend_params({"pilot_count": 100})
        val = p.get("pilot_count")
        pc = self._clamp_int(val, 2, 1, 10)
        assert pc == 10

    def test_clamp_pilot_success_rate_over_max(self):
        """canarySuccess阈值超过 100 被钳位."""
        from taurus.workflow.units.ops_execution import _normalize_frontend_params
        p = _normalize_frontend_params({"pilot_success_rate": 150})
        val = p.get("pilot_success_rate")
        rate = self._clamp_int(val, 100, 1, 100)
        assert rate == 100

    def test_clamp_pilot_success_rate_under_min(self):
        """canarySuccess阈值小于 1 被钳位到 1."""
        from taurus.workflow.units.ops_execution import _normalize_frontend_params
        p = _normalize_frontend_params({"pilot_success_rate": 0})
        val = p.get("pilot_success_rate")
        rate = self._clamp_int(val, 100, 1, 100)
        assert rate == 1

    def test_default_fail_strategy_is_stop(self):
        """无 ops_fail_strategy Parameters时默认值为 stop."""
        from taurus.workflow.units.ops_execution import _normalize_frontend_params
        p = _normalize_frontend_params({})
        fail_strategy = p.get("ops_fail_strategy") or "stop"
        assert fail_strategy == "stop"

    def test_explicit_fail_strategy_override(self):
        """显式setting ops_fail_strategy=continue 时被keep."""
        from taurus.workflow.units.ops_execution import _normalize_frontend_params
        p = _normalize_frontend_params({"ops_fail_strategy": "continue"})
        fail_strategy = p.get("ops_fail_strategy") or "stop"
        assert fail_strategy == "continue"

    def test_default_exec_mode_is_parallel(self):
        """无 exec_mode Parameters时默认值为 parallel."""
        from taurus.workflow.units.ops_execution import _normalize_frontend_params
        p = _normalize_frontend_params({})
        exec_mode = p.get("exec_mode") or "parallel"
        assert exec_mode == "parallel"

    def test_default_concurrent_is_ten(self):
        """无 concurrent Parameters时默认值为 10."""
        from taurus.workflow.units.ops_execution import _normalize_frontend_params
        p = _normalize_frontend_params({})
        val = p.get("concurrent")
        concurrent = self._clamp_int(val, 10, 1, 50)
        assert concurrent == 10


class TestPrivilegedAndSudoPassword:
    """test特权execute (privileged) 和 su 密码的related逻辑."""

    @pytest.fixture()
    def script_adapter(self):
        from taurus.workflow.engine.registry import get_registry
        return get_registry().instantiate("script")

    @pytest.fixture()
    def command_adapter(self):
        from taurus.workflow.engine.registry import get_registry
        return get_registry().instantiate("command")

    # ----- validate_config check -----

    def test_validate_privileged_flag_passes(self, script_adapter):
        """privileged=True 时checkvia(su_user 可缺省)."""
        p = {"script_id": 1, "privileged": True}
        r = script_adapter.validate_config(p)
        assert r.ok is True

    def test_validate_privileged_with_user_passes(self, script_adapter):
        """privileged=True + su_user viacheck."""
        p = {"script_id": 1, "privileged": True, "su_user": "root"}
        r = script_adapter.validate_config(p)
        assert r.ok is True

    def test_validate_privileged_with_password_passes(self, script_adapter):
        """privileged=True + su_password viacheck."""
        p = {"script_id": 1, "privileged": True, "su_password": "secret123"}
        r = script_adapter.validate_config(p)
        assert r.ok is True

    def test_validate_privileged_full_config_passes(self, command_adapter):
        """完整特权config(flag + user + password + working_directory)via."""
        p = {
            "command": "ls -la",
            "privileged": True,
            "su_user": "deploy",
            "su_password": "p@ssw0rd",
            "working_directory": "/opt/app",
            "load_profile": "true",
            "merge_streams": False,
        }
        r = command_adapter.validate_config(p)
        assert r.ok is True

    def test_validate_non_privileged_ignores_su_fields(self, script_adapter):
        """privileged=False 时 su_user/su_password 不影响check结果."""
        p = {"script_id": 1, "privileged": False, "su_user": "root", "su_password": "pw"}
        r = script_adapter.validate_config(p)
        assert r.ok is True

    def test_validate_su_password_empty_string_passes(self, script_adapter):
        """su_password 为Empty字符串时checkvia(NOPASSWD 场景)."""
        p = {"script_id": 1, "privileged": True, "su_user": "root", "su_password": ""}
        r = script_adapter.validate_config(p)
        assert r.ok is True

    # ----- su_password Encryption逻辑(Mock dispatch Environment variables构建)-----

    @staticmethod
    def _build_env_dict(params: dict) -> dict:
        """Mock dispatch 中的 env_dict 构建逻辑."""
        privileged_flag = bool(params.get("privileged", False))
        env_dict: dict = dict(params.get("environment") or {})
        if privileged_flag:
            env_dict["PRIVILEGED_EXECUTION"] = "true"
            su_user = params.get("su_user")
            if su_user:
                env_dict["SU_USER"] = str(su_user)
            su_pwd = params.get("su_password")
            if su_pwd:
                from taurus.config_crypto import encrypt_value
                env_dict["SU_PASSWORD"] = encrypt_value(str(su_pwd))
        return env_dict

    def test_privileged_flag_sets_env(self):
        """privileged=True 时 env_dict contain PRIVILEGED_EXECUTION."""
        env = self._build_env_dict({"privileged": True})
        assert env["PRIVILEGED_EXECUTION"] == "true"

    def test_privileged_flag_sets_su_user(self):
        """privileged=True + su_user 时 env_dict contain SU_USER."""
        env = self._build_env_dict({"privileged": True, "su_user": "deploy"})
        assert env["SU_USER"] == "deploy"
        assert env["PRIVILEGED_EXECUTION"] == "true"

    def test_privileged_flag_without_user_no_su_user(self):
        """privileged=True 但无 su_user 时 env_dict 不含 SU_USER."""
        env = self._build_env_dict({"privileged": True})
        assert "SU_USER" not in env

    def test_privileged_flag_encrypts_password(self, monkeypatch):
        """privileged=True + su_password 时密码被Encryption存入 env_dict."""
        monkeypatch.setattr(
            "taurus.config_crypto.encrypt_value",
            lambda v: f"fernet:{v}",
        )
        env = self._build_env_dict({"privileged": True, "su_password": "my_secret"})
        assert env["PRIVILEGED_EXECUTION"] == "true"
        assert "SU_PASSWORD" in env
        assert env["SU_PASSWORD"].startswith("fernet:")
        assert env["SU_PASSWORD"] != "my_secret"

    def test_no_password_no_su_password_env(self):
        """privileged=True 但无 su_password 时 env_dict 不含 SU_PASSWORD."""
        env = self._build_env_dict({"privileged": True})
        assert "SU_PASSWORD" not in env

    def test_password_empty_string_not_encrypted(self):
        """su_password 为Empty字符串时不Encryption(Empty值Skip)."""
        env = self._build_env_dict({"privileged": True, "su_password": ""})
        assert "SU_PASSWORD" not in env

    def test_non_privileged_no_privileged_env(self):
        """privileged=False 时 env_dict 不contain任何特权related键."""
        env = self._build_env_dict({"privileged": False, "su_user": "root", "su_password": "pw"})
        assert "PRIVILEGED_EXECUTION" not in env
        assert "SU_USER" not in env
        assert "SU_PASSWORD" not in env

    def test_non_privileged_flag_default_is_false(self):
        """不传 privileged 时默认 False, env_dict 不setting特权键."""
        env = self._build_env_dict({})
        assert "PRIVILEGED_EXECUTION" not in env

    def test_env_dict_preserves_existing_env_vars(self):
        """env_dict keep原有Environment variables, 不因特权execute而丢失."""
        env = self._build_env_dict({
            "privileged": True,
            "environment": {"HOME": "/home/app", "LANG": "en_US.UTF-8"},
        })
        assert env["HOME"] == "/home/app"
        assert env["LANG"] == "en_US.UTF-8"
        assert env["PRIVILEGED_EXECUTION"] == "true"

    def test_su_user_coerced_to_string(self):
        """su_user 自动转为字符串class型."""
        env = self._build_env_dict({"privileged": True, "su_user": 123})
        assert env["SU_USER"] == "123"
        assert isinstance(env["SU_USER"], str)