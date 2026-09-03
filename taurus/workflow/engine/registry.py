"""S4 ExecutableUnit plugin registry.

Usage for external adapter authors:

    from taurus.workflow.engine.registry import register_unit_adapter

    @register_unit_adapter("http_callback")
    class HttpCallbackAdapter(ExecutableUnit):
        ...

Or configure INSTALLED_UNIT_ADAPTERS via conf/env.py, auto-import all adapters at apps.ready().
"""
from __future__ import annotations

import threading
from typing import Callable

from .base_adapter import ExecutableUnit


class UnitAdapterRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        # node_type -> adapter class
        self._adapters: dict[str, type[ExecutableUnit]] = {}
        # First instantiation calls registry._adapters[nt].__init__ for instance cache
        self._instances: dict[str, ExecutableUnit] = {}

    # ---------- registration ----------
    def register(
        self,
        node_type: str,
    ) -> Callable[[type[ExecutableUnit]], type[ExecutableUnit]]:
        """Decorator factory. Used to register an adapter class under a node_type key."""

        def _decorator(cls: type[ExecutableUnit]) -> type[ExecutableUnit]:
            if not (isinstance(cls, type) and issubclass(cls, ExecutableUnit)):
                raise ValueError(
                    f"register_unit_adapter('{node_type}') can only decorate ExecutableUnit subclasses, "
                    f"got: {cls!r}"
                )
            missing = cls._ensure_class_attrs()
            if missing:
                raise ValueError(
                    f"Adapter {cls.__name__} is missing required class attributes {missing}, "
                    f"cannot register with node_type='{node_type}'"
                )
            # Decorator parameter node_type must be consistent with class attribute (both must be defined, avoid ambiguity)
            if cls.node_type != node_type:
                raise ValueError(
                    f"Adapter {cls.__name__}.node_type='{cls.node_type}' and decorator parameter "
                    f"node_type='{node_type}' are inconsistent"
                )
            with self._lock:
                if node_type in self._adapters:
                    raise ValueError(
                        f"Node type conflict: node_type='{node_type}' is already occupied by "
                        f"{self._adapters[node_type].__name__!r}"
                    )
                self._adapters[node_type] = cls
            return cls

        return _decorator

    def register_alias(self, alias: str, existing_node_type: str) -> None:
        """Add an alias node_type for an already registered adapter.

        Usage: backward compatibility between new and old versions (e.g. frontend old naming virtual_start points to new start).
        Note: alias only exists in the lookup map, will not appear in list_all_adapter_types / manifest_all output,
        to avoid duplicate nodes in frontend panel.
        """
        with self._lock:
            if existing_node_type not in self._adapters:
                raise KeyError(
                    f"register_alias('{alias}' -> '{existing_node_type}') failed: "
                    f"existing_node_type='{existing_node_type}' not found in registered adapters; "
                    f"registered: {sorted(self._adapters)}"
                )
            if alias in self._adapters and self._adapters[alias] is not self._adapters[existing_node_type]:
                raise ValueError(
                    f"register_alias conflict: alias='{alias}' is already occupied by "
                    f"{self._adapters[alias].__name__!r}, cannot point to "
                    f"{self._adapters[existing_node_type].__name__!r}"
                )
            self._adapters[alias] = self._adapters[existing_node_type]

    # ---------- query API ----------
    def has(self, node_type: str) -> bool:
        with self._lock:
            return node_type in self._adapters

    def get_class(self, node_type: str) -> type[ExecutableUnit]:
        with self._lock:
            if node_type not in self._adapters:
                raise KeyError(
                    f"Unknown node_type='{node_type}'; registered: {sorted(self._adapters)}"
                )
            return self._adapters[node_type]

    def instantiate(self, node_type: str) -> ExecutableUnit:
        """Fetch a shared adapter instance (adapters are required to be stateless, safe across calls)."""
        with self._lock:
            if node_type not in self._instances:
                cls = self.get_class(node_type)
                self._instances[node_type] = cls()
            return self._instances[node_type]

    def list_all_adapter_types(self) -> list[str]:
        with self._lock:
            # Deduplicate: same cls only keeps canonical (i.e. cls.node_type), aliases not output to list
            seen: set[type[ExecutableUnit]] = set()
            result: list[str] = []
            for nt in sorted(self._adapters.keys()):
                cls = self._adapters[nt]
                if cls in seen:
                    continue
                seen.add(cls)
                result.append(nt)
            return sorted(result)

    def manifest_all(self) -> list[dict]:
        """Return minimal message set required for frontend manifest (display_name / requires_host / category, etc.)."""
        with self._lock:
            out: list[dict] = []
            # Deduplicate: same adapter class only outputs once (using cls.node_type as canonical)
            seen: set[type[ExecutableUnit]] = set()
            for nt in sorted(self._adapters.keys()):
                cls = self._adapters[nt]
                if cls in seen:
                    continue
                seen.add(cls)
                out.append(
                    {
                        "node_type": cls.node_type,
                        "display_name": cls.display_name,
                        "requires_host": cls.requires_host,
                        "is_asynchronous_human": cls.is_asynchronous_human,
                        "category": getattr(cls, "category", "execution"),
                    }
                )
            return out


# ------------------------------------------------------------ Global singleton
_global_registry: UnitAdapterRegistry | None = None
_global_lock = threading.Lock()


def get_registry() -> UnitAdapterRegistry:
    """Fetch process-level adapter registry singleton."""
    global _global_registry
    if _global_registry is None:
        with _global_lock:
            if _global_registry is None:
                _global_registry = UnitAdapterRegistry()
    return _global_registry


def register_unit_adapter(node_type: str):
    """Convenience decorator: registers to global singleton."""
    return get_registry().register(node_type)