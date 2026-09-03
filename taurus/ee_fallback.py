"""
taurus/ee_fallback.py — 物理剥离 taurus_ee 后的统一 fallback stub.

使用场景：
  taurus_ee/ 目录不存在时，taurus/views.py / serializers.py 中的 Thin Wrapper
  需要有一个可继承的 stub 父类 + 一个安全的 ee_service_or_403 替身，
  否则 Django 启动阶段就 ImportError 崩溃。

用法（所有需要 try/except 的模块级 import 统一这个模式）：

    from taurus.ee_fallback import _EEFallbackViewSet, _EEFallbackSerializer
    try:
        from taurus_ee.views.xxx import SomeViewSet as _EESomeViewSet
    except ImportError:
        _EESomeViewSet = _EEFallbackViewSet  # EE 缺失时走 fallback

    class SomeViewSet(_EESomeViewSet):
        pass

注：不要在此模块 import 任何 taurus_ee.* —— 这正是我们要隔离的包。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from dvadmin.utils.viewset import CustomModelViewSet
from dvadmin.utils.serializers import CustomModelSerializer

from rest_framework.exceptions import PermissionDenied
from taurus.editions import describe_feature


# ---------------------------------------------------------------------------
# ViewSet fallback
# ---------------------------------------------------------------------------
class _EEFallbackViewSet(CustomModelViewSet):
    """EE ViewSet 缺失时的 fallback 父类。

    dispatch 阶段直接抛 PermissionDenied(403)，
    保证该路由在 EE 包不存在时不会被误访问。
    """

    def dispatch(self, request, *args, **kwargs):
        raise PermissionDenied(
            "当前实例未安装企业版（taurus_ee 包缺失），本功能不可用。"
            "请联系销售获取商业版授权。"
        )


# ---------------------------------------------------------------------------
# Serializer fallback
# ---------------------------------------------------------------------------
class _EEFallbackSerializer(CustomModelSerializer):
    """EE Serializer 缺失时的 fallback 父类。

    在 Thin Wrapper 继承链中占位——仅用于让 Python class 定义不崩溃。
    实际运行时 ViewSet dispatch 会先被 EditionGate / _EEFallbackViewSet
    拦截到 403，永远不会走到 Serializer 实例化。
    """

    class Meta:
        model = None
        fields = []


# ---------------------------------------------------------------------------
# ee_service_or_403 stub — 替代 taurus_ee.utils.gate.ee_service_or_403
# ---------------------------------------------------------------------------
def ee_service_or_403(service_name: str, feature_code: Optional[str] = None):
    """stub：EE 包缺失时永远抛 PermissionDenied.

    真实实现见 taurus_ee.utils.gate.ee_service_or_403。
    taurus_ee 物理剥离后，方法体里的 `from taurus_ee.utils.gate import
    ee_service_or_403` 会被 try/except 替换成本模块的这个 stub。
    """
    desc = describe_feature(feature_code) if feature_code else service_name
    raise PermissionDenied(
        f"[{desc}] 当前实例未安装企业版（taurus_ee 包缺失），"
        f"服务 {service_name} 不可用。请联系销售获取商业版授权。"
    )


# ---------------------------------------------------------------------------
# ee_service_or_none stub — 类似 ee_registry.get_service()，但返回 None 不 raise
# ---------------------------------------------------------------------------
def ee_service_or_none(service_name: str) -> Optional[Any]:
    """stub：EE 包缺失时永远返回 None（供需要静默降级的调用方）."""
    return None


__all__ = [
    "_EEFallbackViewSet",
    "_EEFallbackSerializer",
    "ee_service_or_403",
    "ee_service_or_none",
]
