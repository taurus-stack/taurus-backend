"""S5-01 合规testframework基础设施.

provide:
- ``all_adapter_types``:自动Found所有已RegisterAdapter的 node_type list
- ``make_cfg``:构造 RenderedNodeConfig 的工厂 fixture
- ``adapter_instance``:Per node_type FetchSharedAdapterinstance

用法::

    pytest -m compliance                       # 仅run合规test
    pytest tests/workflow/test_compliance.py -v

Adapter专属合规数据(valid/invalid config, 期望Error码等)via
``COMPLIANCE_DATA`` dictRegister, 见 ``test_compliance.py``.
"""
from __future__ import annotations

import pytest

from taurus.workflow.engine.registry import get_registry
from taurus.workflow.engine.schemas import RenderedNodeConfig


@pytest.fixture(scope="session")
def all_adapter_types() -> list[str]:
    """return所有已RegisterAdapter的 node_type list(Order后)."""
    return get_registry().list_all_adapter_types()


@pytest.fixture
def make_cfg():
    """构造 RenderedNodeConfig 的工厂function, test中Per需覆盖Field."""

    counter = {"i": 0}

    def _make(**kwargs):
        counter["i"] += 1
        defaults: dict = {
            "execution_id": str(counter["i"]),
            "node_key": f"N{counter['i']}",
            "node_name": f"Node-{counter['i']}",
            "host_id": "__NO_HOST__",
            "dispatch_id": f"disp-{counter['i']:04d}",
            "attempt_no": 1,
            "user_id": 1,
            "global_timeout_sec": 300,
            "secrets_mask": [],
            "params": {},
            "triggered_at": "2026-01-01T00:00:00Z",
        }
        defaults.update(kwargs)
        return RenderedNodeConfig(**defaults)

    return _make


@pytest.fixture
def adapter_instance():
    """Per node_type FetchSharedAdapterinstance."""

    registry = get_registry()

    def _get(node_type: str):
        return registry.instantiate(node_type)

    return _get