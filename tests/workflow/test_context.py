"""Tests for WorkflowContext ${path} interpolation engine.
"""
from __future__ import annotations

import pytest

from taurus.workflow.engine.context import WorkflowContext, InterpolationError


class TestContextBasics:
    def test_render_plain_string_no_var(self):
        ctx = WorkflowContext(workflow_env={"TOKEN": "secret"})
        assert ctx.render("hello world") == "hello world"

    def test_render_simple_workflow_env(self):
        ctx = WorkflowContext(workflow_env={"TOKEN": "abc"})
        assert ctx.render("Bearer ${workflow.env.TOKEN}") == "Bearer abc"

    def test_render_nested_node_output(self):
        ctx = WorkflowContext(
            workflow_env={},
            node_outputs={
                "node_script": {
                    "status": 2,
                    "output": {
                        "response_json": {"data": {"id": 42, "tags": ["a", "b"]}},
                        "exit_code": 0,
                    },
                }
            },
        )
        assert ctx.render("${node_script.output.response_json.data.id}") == "42"
        assert ctx.render("${node_script.output.response_json.data.tags[1]}") == "b"
        assert ctx.render("got ${node_script.output.exit_code} ok") == "got 0 ok"
        assert ctx.render("st=${node_script.status}") == "st=2"

    def test_render_missing_raises_interpolation_error(self):
        ctx = WorkflowContext(workflow_env={})
        with pytest.raises(InterpolationError) as exc:
            ctx.render("${workflow.env.NOT_EXIST}")
        assert "NOT_EXIST" in str(exc.value)
        # spec §3 validate_and_render: 未Definitionvariable视为 E0xxx configError
        assert exc.value.error_code.startswith("E0")

    def test_render_dict_values_recursive(self):
        """dict template递归Interpolate(spec §7 示例 http_callback body dict)."""
        ctx = WorkflowContext(
            workflow_env={"HOST": "1.2.3.4", "PORT": 80},
            node_outputs={},
        )
        tpl = {
            "target": "${workflow.env.HOST}:${workflow.env.PORT}",
            "nested": {"meta": "host-${workflow.env.HOST}"},
            "list": ["${workflow.env.PORT}", "fixed"],
            "int": 123,
        }
        got = ctx.render_structure(tpl)
        assert got == {
            "target": "1.2.3.4:80",
            "nested": {"meta": "host-1.2.3.4"},
            "list": ["80", "fixed"],
            "int": 123,
        }

    def test_secret_masking_in_render_output(self):
        """secret Field在 view_masked() 里被Mask为 ***, 但真实Render正确."""
        ctx = WorkflowContext(
            workflow_env={},
            secrets={"DB_PASSWORD": "s3cret"},
        )
        # 真实 dispatch path:不Mask
        assert ctx.render("${workflow.secrets.DB_PASSWORD}") == "s3cret"
        # Logview:mask
        view = ctx.view_masked()
        assert view["workflow"]["secrets"]["DB_PASSWORD"] == "***"
        assert view["workflow"]["env"] == {}
        # 原 ctx internal值保持原样
        assert ctx.secrets["DB_PASSWORD"] == "s3cret"

    def test_escape_dollar_literal(self):
        """$$ 转义为字面量 $, 不TriggerInterpolate."""
        ctx = WorkflowContext(workflow_env={"A": "1"})
        assert ctx.render("Price is $$${workflow.env.A}") == "Price is $1"
        assert ctx.render("Plain $$ dollar") == "Plain $ dollar"

    def test_trigger_params_accessible(self):
        ctx = WorkflowContext(
            workflow_env={},
            trigger_params={"version": "v1.2.3", "rollback": False},
        )
        assert ctx.render("ver=${trigger.version}") == "ver=v1.2.3"
        assert ctx.render("rb=${trigger.rollback}") == "rb=False"

    def test_path_step_limit_blocks_long_chains(self):
        """超长path(> _MAX_PATH_STEPS 步)应被拦截, Avoid刻意构造的超长Expression."""
        # 35 层嵌套 env key(_MAX_PATH_STEPS 默认为 32)
        long_chain = "trigger." + ".".join([f"a{i}" for i in range(35)])
        d = {}
        cur = d
        for i in range(34):
            cur[f"a{i}"] = {}
            cur = cur[f"a{i}"]
        cur["a34"] = "leak"
        ctx = WorkflowContext(workflow_env={}, trigger_params=d)
        with pytest.raises(InterpolationError) as exc:
            ctx.render(f"${{{long_chain}}}")
        assert exc.value.error_code == "E0405"


class TestWorkflowContextResolvePath:
    def test_resolve_empty_root(self):
        ctx = WorkflowContext(workflow_env={"K": "v"})
        # rootpathmust存在:workflow / trigger / node_*
        with pytest.raises(InterpolationError):
            ctx.render("${nothing.here}")