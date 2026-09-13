"""taurus_ee.utils.gate — EE 专属的 EditionGate 便捷工具.

相较 `taurus.editions.loader.require_feature`，这里补充两层：
  1. @ee_viewset_action() — 组合版装饰器：
       require_feature(feature_code) +
       自动检查对应的 ee_registry service 是否已注册，避免 None 报错；
  2. ee_service_or_403(name, feature_code) — 在 Thin Wrapper 的
       方法体内部"拿不到装饰器"时手动调用（CE 场景返回 403）。
"""
from __future__ import annotations

import functools
from typing import Callable, Optional

from rest_framework.exceptions import PermissionDenied

from taurus.editions import describe_feature, get_edition
from taurus.ee_registry import ee_registry


# ---------------------------------------------------------------------------
# 通用 403 错误文案构造（跟 editions.loader.require_feature 保持一致口径）
# ---------------------------------------------------------------------------
def _make_permission_denied(feature_code: str, reason: Optional[str] = None) -> PermissionDenied:
    desc = describe_feature(feature_code) or feature_code
    if reason:
        detail = f"[{desc}] {reason}"
    else:
        detail = f"[{desc}] 为商业版（Enterprise Edition）专属能力，当前 Edition 不支持。请联系销售升级企业版，或在管理端右上角「升级」入口提交试用申请。"
    return PermissionDenied(detail)


# ---------------------------------------------------------------------------
# ee_viewset_action(feature_code, *, service_name=None)
# ---------------------------------------------------------------------------
def ee_viewset_action(feature_code: str, *, service_name: Optional[str] = None):
    """组合装饰器：先 EditionGate 拦 Feature，再（可选）检查 EE Service 已注册.

    用法（直接挂在 DRF ViewSet 的 @action 上，或整个类级——类级用 @method_decorator）：

        class ScriptCheckRuleViewSet(CustomModelViewSet):
            queryset = ScriptCheckRule.objects.all()

            # 类级装饰：所有 action 都必须通过 feature + service 检查
            def initial(self, request, *args, **kwargs):
                gate = get_edition()
                if not gate.has_feature(SCRIPT_SECURITY_CHECK):
                    raise _make_permission_denied(SCRIPT_SECURITY_CHECK)
                if not ee_registry.has_service("script_checker"):
                    raise _make_permission_denied(SCRIPT_SECURITY_CHECK, reason="脚本检查服务未加载")
                return super().initial(request, *args, **kwargs)

            # 方法级装饰
            @action(detail=True, methods=["POST"])
            @ee_viewset_action(SCRIPT_SECURITY_CHECK, service_name="script_checker")
            def run_check(self, request, pk=None):
                ...
    """
    def decorator(func: Callable) -> Callable:
        gate_checked = get_edition().require_feature(feature_code)(func)

        @functools.wraps(gate_checked)
        def wrapper(*args, **kwargs):
            # service 二级校验（Edition 过了但 service 实现被抽走/占位 None → 同样 403，更健壮）
            if service_name and not ee_registry.has_service(service_name):
                raise _make_permission_denied(
                    feature_code,
                    reason=f"扩展服务（{service_name}）当前未就绪，请检查 taurus_ee 应用加载日志",
                )
            return gate_checked(*args, **kwargs)

        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# ee_service_or_403(name, feature_code)
# ---------------------------------------------------------------------------
def ee_service_or_403(service_name: str, feature_code: str):
    """手动取 EE Service 的 Thin Wrapper 便捷函数：拿不到直接抛 PermissionDenied.

    用例（taurus.views.py 中的 Thin Wrapper 不允许 import taurus_ee，因此也不能
    用 @ee_viewset_action，只能在方法体内部手动检查）：

        class ScriptViewSet(CustomModelViewSet):
            @action(detail=True, methods=["POST"])
            def shares(self, request, pk=None):
                # CE 场景 share_service == None → 403 拦
                service = ee_service_or_403("share_service", SCRIPT_SHARING)
                return service.shares_for_script(self.get_object(), request)
    """
    # 先 Feature Gate
    gate = get_edition()
    if not gate.has_feature(feature_code):
        raise _make_permission_denied(feature_code)
    impl = ee_registry.get_service(service_name)
    if impl is None:
        raise _make_permission_denied(
            feature_code,
            reason=f"EE Service({service_name}) 未就绪",
        )
    return impl


__all__ = ["ee_viewset_action", "ee_service_or_403", "_make_permission_denied"]
