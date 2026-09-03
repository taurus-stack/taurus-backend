"""Tests for ExecutableUnit ABC + Registry."""
from __future__ import annotations

import re

import pytest

from taurus.workflow.engine.base_adapter import ExecutableUnit
from taurus.workflow.engine.registry import (
    UnitAdapterRegistry,
    get_registry,
    register_unit_adapter,
)
from taurus.workflow.engine.schemas import (
    NO_HOST_SENTINEL,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    RenderedNodeConfig,
    UnitOutput,
    ValidationResult,
)


# ---------------------------------------------------------------- helpers
def _minimal_rendered(node_key: str = "node_a") -> RenderedNodeConfig:
    return RenderedNodeConfig(
        execution_id="exec-1",
        node_key=node_key,
        node_name=node_key,
        host_id=NO_HOST_SENTINEL,
        dispatch_id=f"did-{node_key}-1",
        attempt_no=1,
        user_id=1,
        global_timeout_sec=60,
        secrets_mask=[],
        params={},
        triggered_at="2026-01-01T00:00:00Z",
    )


class TestExecutableUnitABC:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            ExecutableUnit()  # type: ignore[abstract]

    def test_missing_any_must_method_is_abstract(self):
        """缺少任何 5 must method, class都cannot被instance化."""

        class Partial(ExecutableUnit):
            node_type = "partial"
            display_name = "Partial"
            requires_host = False
            is_asynchronous_human = False

            # 只implement 1/5   must method
            def validate_config(self, params, secrets_mask=None):
                return ValidationResult.success()

        with pytest.raises(TypeError):
            Partial()


class TestRegistryBasic:
    def test_empty_registry_has_no_adapters(self):
        r = UnitAdapterRegistry()
        assert r.list_all_adapter_types() == []

    def test_register_and_get_adapter(self):
        r = UnitAdapterRegistry()

        @r.register("demo_echo")
        class EchoAdapter(ExecutableUnit):
            node_type = "demo_echo"
            display_name = "Demo Echo"
            requires_host = False
            is_asynchronous_human = False

            def validate_config(self, params, secrets_mask=None):
                return ValidationResult.success()

            def validate_and_render(self, params, context, secrets_mask=None):
                return context.render_structure(params)

            def dispatch(self, cfg):
                return UnitOutput(status=STATUS_SUCCESS, output={"echo": cfg.params})

            def poll(self, cfg, adapter_state):
                return UnitOutput(status=STATUS_SUCCESS, output={"echo": cfg.params})

            def cancel(self, cfg, adapter_state):
                return UnitOutput(status=STATUS_SUCCESS, output={})

        assert r.has("demo_echo") is True
        inst = r.instantiate("demo_echo")
        assert isinstance(inst, EchoAdapter)
        assert "demo_echo" in r.list_all_adapter_types()

    def test_duplicate_node_type_raises(self):
        r = UnitAdapterRegistry()
        dec = r.register("dup")

        class A(ExecutableUnit):
            node_type = "dup"
            display_name = "A"
            requires_host = False
            is_asynchronous_human = False

            def validate_config(self, params, secrets_mask=None):
                return ValidationResult.success()

            def validate_and_render(self, params, context, secrets_mask=None):
                return {}

            def dispatch(self, cfg):
                return UnitOutput(STATUS_SUCCESS, {})

            def poll(self, cfg, adapter_state):
                return UnitOutput(STATUS_SUCCESS, {})

            def cancel(self, cfg, adapter_state):
                return UnitOutput(STATUS_SUCCESS, {})

        dec(A)
        with pytest.raises(ValueError, match=re.escape("node_type='dup'")):
            dec(A)

    def test_missing_node_type_class_attributes_raises_on_register(self):
        r = UnitAdapterRegistry()
        with pytest.raises(ValueError, match="node_type"):
            @r.register("bad")
            class Bad(ExecutableUnit):
                # 故意遗漏 display_name / requires_host 等
                node_type = "bad"

                def validate_config(self, params, secrets_mask=None):
                    return ValidationResult.success()

                def validate_and_render(self, params, context, secrets_mask=None):
                    return {}

                def dispatch(self, cfg):
                    return UnitOutput(STATUS_SUCCESS, {})

                def poll(self, cfg, adapter_state):
                    return UnitOutput(STATUS_SUCCESS, {})

                def cancel(self, cfg, adapter_state):
                    return UnitOutput(STATUS_SUCCESS, {})

    def test_global_registry_singleton_is_shared(self):
        """@register_unit_adapter shouldRegister到同一 global单例."""
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2


class TestDefaultHooks:
    """5 must method的默认hook不抛exception."""

    def _make_minimal(self):
        r = UnitAdapterRegistry()

        @r.register("hooky")
        class Hooky(ExecutableUnit):
            node_type = "hooky"
            display_name = "h"
            requires_host = False
            is_asynchronous_human = False

            def validate_config(self, params, secrets_mask=None):
                return ValidationResult.success()

            def validate_and_render(self, params, context, secrets_mask=None):
                return {}

            def dispatch(self, cfg):
                return UnitOutput(STATUS_SUCCESS, {})

            def poll(self, cfg, adapter_state):
                return UnitOutput(STATUS_SUCCESS, {})

            def cancel(self, cfg, adapter_state):
                return UnitOutput(STATUS_SUCCESS, {})

        return r.instantiate("hooky")

    def test_on_before_dispatch_default_noop(self):
        a = self._make_minimal()
        # 不应抛exception
        a.on_before_dispatch(_minimal_rendered())

    def test_on_after_finish_default_noop(self):
        a = self._make_minimal()
        out = UnitOutput(STATUS_SUCCESS, {})
        a.on_after_finish(_minimal_rendered(), out)

    def test_get_metrics_labels_default_empty(self):
        a = self._make_minimal()
        # 至少Yes一  dict(may含 default Field)
        labels = a.get_metrics_labels(_minimal_rendered())
        assert isinstance(labels, dict)

    def test_inject_external_event_default_raises(self):
        a = self._make_minimal()
        # 默认implement:非人工异步节.调用 inject → ValueError
        with pytest.raises(ValueError):
            a.inject_external_event(
                cfg=_minimal_rendered(),
                adapter_state={},
                event={"kind": "approve", "by": "u"},
            )

    def test_inject_on_human_node_not_implemented_defaults(self):
        r = UnitAdapterRegistry()

        @r.register("human")
        class Human(ExecutableUnit):
            node_type = "human"
            display_name = "H"
            requires_host = False
            is_asynchronous_human = True

            def validate_config(self, params, secrets_mask=None):
                return ValidationResult.success()

            def validate_and_render(self, params, context, secrets_mask=None):
                return {}

            def dispatch(self, cfg):
                return UnitOutput(STATUS_RUNNING, output={}, adapter_state={"pending": True})

            def poll(self, cfg, adapter_state):
                return UnitOutput(STATUS_RUNNING, output={})

            def cancel(self, cfg, adapter_state):
                return UnitOutput(STATUS_SUCCESS, output={})

        # inject 没覆盖时仍可raised by NotImplemented
        h = r.instantiate("human")
        with pytest.raises(NotImplementedError):
            h.inject_external_event(
                cfg=_minimal_rendered(), adapter_state={}, event={}
            )