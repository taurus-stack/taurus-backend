"""taurus/ee_registry.py — EE Service 全局注册表容器.

（M2 跨模块通信的"桥"，避免 taurus→taurus_ee 反向 import 造成的循环依赖）

设计动机：
  taurus.views.py / taurus.serializers.py 的 Thin Wrapper 层需要调用
  EE 实际实现（script 审批、DAG 引擎、分享服务等），但「M2 导入方向纪律」
  禁止 taurus → taurus_ee，所以在这里定义一个「纯数据容器」：

       taurus_ee.apps.ready() → 把实现注入到 ee_registry.{services, workflow_units}
       taurus 侧任意模块     → 仅通过 ee_registry.get_service(name) 获取实现

  这样 taurus.* 只 import `taurus.ee_registry`（同目录，零依赖），
  完全不感知 taurus_ee 代码的存在。

使用：
    # ===== taurus 侧（Thin Wrapper，读）=====
    from taurus.ee_registry import ee_registry
    engine = ee_registry.get_service("script_approval_engine")
    if engine is None:
        raise PermissionDenied("脚本审批为商业版专属能力")

    # ===== taurus_ee 侧（写，ready() 内完成）=====
    from taurus.ee_registry import ee_registry
    ee_registry.register_service("script_approval_engine", MyEngine())
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class _EERegistry:
    """线程安全无需考虑：写入仅发生在 apps.ready() 单线程启动阶段."""

    def __init__(self) -> None:
        # EE Service 实现（脚本审批、分享、DAG、通知、金丝雀……）
        self.services: Dict[str, Any] = {}
        # EE 专属 DAG Units（Condition / Loop / SubWorkflow 等 12 个）
        self.workflow_units: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Services
    # ------------------------------------------------------------------
    def register_service(self, name: str, impl: Any) -> None:
        """注册一个 EE Service。重复注册会覆盖（测试场景需要）."""
        self.services[name] = impl

    def get_service(self, name: str) -> Optional[Any]:
        """获取 EE Service 实现；未注册或 CE 模式返回 None."""
        return self.services.get(name)

    def has_service(self, name: str) -> bool:
        return name in self.services and self.services[name] is not None

    # ------------------------------------------------------------------
    # Workflow Units
    # ------------------------------------------------------------------
    def register_workflow_unit(self, name: str, unit_cls: Any) -> None:
        self.workflow_units[name] = unit_cls

    def get_workflow_unit(self, name: str) -> Optional[Any]:
        return self.workflow_units.get(name)

    # ------------------------------------------------------------------
    def reset(self) -> None:
        """仅测试场景使用（pytest fixture 之间清理）."""
        self.services.clear()
        self.workflow_units.clear()


# 全局单例（模块首次被 import 时构造，晚于 apps.ready() 的调用）
ee_registry = _EERegistry()

__all__ = ["ee_registry", "_EERegistry"]
