"""
M1.2 — Edition Loader + 全局 EditionGate。

读取环境变量 TAURUS_EDITION（默认 community），懒加载对应 edition 单例。
对外暴露：
    · get_edition() -> EditionGate    # 获取全局 gate 实例
    · has_feature(code) -> bool       # 快捷函数
    · require_feature(code)           # DRF ViewSet Action 装饰器
    · get_quota(field) -> int|None    # 快捷取配额字段
"""

from __future__ import annotations

import os
import threading
import functools
from typing import Any, Callable, Dict

from .features import describe_feature


class EditionGate:
    """统一入口。后端 ViewSet、Template、Management Commands 都用它。"""

    def __init__(self, edition_name: str):
        self.name = edition_name.lower()
        if self.name == "community":
            from .community import get_edition as _loader
            self.edition = _loader()
        elif self.name == "enterprise":
            from .enterprise import get_edition as _loader
            self.edition = _loader()
        else:
            raise ValueError(
                f"TAURUS_EDITION={edition_name!r} 非法，仅支持 'community' 或 'enterprise'"
            )

    # ------------------------------------------------------------------ feature
    def has_feature(self, feature_code: str) -> bool:
        # --- 双条件 Gate：EE 模式必须同时满足 License 有效 ---
        # M4 License 系统接入前，EnterpriseEdition.license_status 默认返回 valid=False
        # （见 BaseEdition.license_status 的 EE 默认占位），因此会把裸奔 EE 拦住。
        # 开发阶段设置 TAURUS_DEV_BYPASS_LICENSE=1 可跳过此检查，方便本地调试 EE 功能。
        if self.name == "enterprise":
            if not os.environ.get("TAURUS_DEV_BYPASS_LICENSE", "").strip():
                lic = self.edition.license_status
                if not lic.get("valid"):
                    return False
        return self.edition.has_feature(feature_code)

    def require_feature(self, feature_code: str) -> Callable:
        """
        DRF ViewSet @action 装饰器：拦截 EE 功能在 CE 的调用。

        用法：
            from taurus.editions import require_feature

            class MyViewSet(CustomModelViewSet):
                @action(detail=True, methods=['POST'])
                @require_feature('SCRIPT_APPROVAL_FLOW')
                def submit_approval(self, request, pk=None): ...
        """
        from rest_framework.exceptions import PermissionDenied

        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                if not self.has_feature(feature_code):
                    human = describe_feature(feature_code)
                    raise PermissionDenied(
                        f"功能「{human}」({feature_code}) 为商业版专属，"
                        "请升级到企业版或联系销售开通。"
                    )
                return func(*args, **kwargs)
            return wrapper
        return decorator

    # ------------------------------------------------------------------ quota
    @property
    def quota(self) -> Dict[str, int | None]:
        return dict(self.edition.quota)

    def get_quota(self, field: str, default: Any = None) -> int | None:
        return self.edition.quota.get(field, default)

    def is_over_quota(self, field: str, current: int) -> bool:
        """
        对比当前值与配额；配额为 None 代表无上限。
        例：gate.is_over_quota('max_hosts', Host.objects.filter(status=1).count())
        """
        limit = self.edition.quota.get(field)
        if limit is None:
            return False
        return current > limit

    # ------------------------------------------------------------------ misc
    @property
    def tier(self) -> str:
        return self.edition.tier  # type: ignore[return-value]

    @property
    def license_status(self) -> Dict[str, Any]:
        return self.edition.license_status

    def info_payload(self) -> Dict[str, Any]:
        return self.edition.info_payload()


# -------------------------------------------------------------------- 全局单例

_GATE: EditionGate | None = None
_LOCK = threading.Lock()


def _resolve_edition_name() -> str:
    # 1. 优先环境变量 TAURUS_EDITION
    env_val = os.environ.get("TAURUS_EDITION", "").strip().lower()
    if env_val in ("community", "enterprise"):
        return env_val
    # 2. 读取 Django settings.TAURUS_EDITION（M1.3 会注入）
    try:
        from django.conf import settings as _django_settings
        dj_val = getattr(_django_settings, "TAURUS_EDITION", "").strip().lower()
        if dj_val in ("community", "enterprise"):
            return dj_val
    except Exception:  # noqa: BLE001 Django 未初始化时 settings 不可用
        pass
    # 3. 兜底：community
    return "community"


def get_edition() -> EditionGate:
    """懒加载全局 EditionGate（线程安全）。"""
    global _GATE
    if _GATE is None:
        with _LOCK:
            if _GATE is None:
                _GATE = EditionGate(_resolve_edition_name())
    return _GATE


# -------------------------------------------------------------------- 快捷函数
# （只在非热点路径使用；热点路径建议先 gate = get_edition() 后局部复用）

def has_feature(feature_code: str) -> bool:
    return get_edition().has_feature(feature_code)


def require_feature(feature_code: str) -> Callable:
    """延迟版能力 Gate 装饰器：dispatch 发生时再调用 get_edition() 获取最新 Gate。

    原因：@method_decorator(require_feature(F), name='dispatch') 会在类 import 时
    立即求值 require_feature(F) 并绑定返回的装饰器到类。如果返回的装饰器把 Gate
    引用闭包写死，那么测试场景中 reset_for_testing() 切换 TAURUS_EDITION 就不会生效。
    所以这里每次装饰器被调用时都重新解算 Gate。

    拒绝响应构造策略：
    - 由于 @method_decorator(X, name='dispatch') 作用在类上时，传入 X 包装函数的
      args[0] 实际上是 WSGIRequest（而非 ViewSet self），因此不能指望「从 args[0]
      取 self.handle_exception」走标准 DRF 路径。
    - 退而求其次，优先遍历 args 找到 APIView 实例 → 调用 self.handle_exception()；
      找不到就返回一个原生 JsonResponse(status=403)，确保状态码正确且响应可被前端
      正常解析，不会退化成 Django 调试页 500。
    """
    def _decorator(view_func):
        @functools.wraps(view_func)
        def _wrapped(*args, **kwargs):
            from django.http import JsonResponse
            from rest_framework import status as _drf_status

            gate = get_edition()
            if gate.has_feature(feature_code):
                return view_func(*args, **kwargs)

            human = describe_feature(feature_code)
            detail = (
                f"功能「{human}」({feature_code}) 为商业版专属，"
                "请升级到企业版或联系销售开通。"
            )
            payload = {
                "detail": detail,
                "code": "edition_gate",
                "feature": feature_code,
            }

            # 路径 A：从 args 中找 self（APIView 实例） → 用 self.handle_exception，
            # 这样错误响应格式与项目其它 DRF PermissionDenied 完全一致。
            self_obj = None
            for candidate in args:
                if hasattr(candidate, "handle_exception") and callable(
                    getattr(candidate, "handle_exception", None)
                ):
                    self_obj = candidate
                    break
            if self_obj is not None:
                from rest_framework.exceptions import PermissionDenied as _DRFPermDenied

                try:
                    return self_obj.handle_exception(_DRFPermDenied(detail))
                except Exception:  # pragma: no cover — handle_exception 失败兜底
                    pass

            # 路径 B：未找到 self → 退回 JsonResponse。status=403 一定正确，前端不会
            # 因 200 读到 EE 数据；JSON 格式便于统一处理。
            return JsonResponse(
                data=payload,
                status=_drf_status.HTTP_403_FORBIDDEN,
                json_dumps_params={"ensure_ascii": False},
            )

        return _wrapped
    return _decorator


def get_quota(field: str, default: Any = None) -> int | None:
    return get_edition().get_quota(field, default)


def is_over_quota(field: str, current: int) -> bool:
    """模块级快捷函数：对比当前值与配额；配额为 None 代表无上限。"""
    return get_edition().is_over_quota(field, current)


def check_quota(field: str, current: int, human_name: str) -> None:
    """
    配额校验辅助：超出配额时抛出 PermissionDenied。

    用法：
        check_quota('max_hosts', Host.objects.count(), '托管主机')
    """
    from rest_framework.exceptions import PermissionDenied

    gate = get_edition()
    limit = gate.get_quota(field)
    if limit is None:
        return
    if current >= limit:
        raise PermissionDenied(
            f"当前版本（{gate.edition.tier}）{human_name} 配额已满（上限 {limit}），"
            "请删除部分后再操作，或联系销售升级。"
        )


def evict_to_quota(queryset, field: str, order_by: str = 'create_datetime',
                   filter_exclude: dict | None = None) -> int:
    """
    FIFO 淘汰：将 queryset 中最旧的记录删掉直到当前数量 < 配额。

    用法：限制某脚本最多保留 3 个历史版本：
        evict_to_quota(script.versions.all(), 'max_script_versions_per_script')

    返回实际删除的记录数。配额为 None 时返回 0（不淘汰）。
    """
    gate = get_edition()
    limit = gate.get_quota(field)
    if limit is None:
        return 0

    current = queryset.count()
    if current < limit:
        return 0

    # 需保留最新的 limit 条，其余按时间升序淘汰
    to_remove_count = current - limit + 1  # +1 是因为马上要创建新的
    remove_qs = queryset.order_by(order_by)
    if filter_exclude:
        remove_qs = remove_qs.exclude(**filter_exclude)
    # 只取 ID 列表再 delete，避免 Python 层逐条对象加载
    ids_to_remove = list(remove_qs.values_list('pk', flat=True)[:to_remove_count])
    if not ids_to_remove:
        return 0
    deleted, _ = queryset.filter(pk__in=ids_to_remove).delete()
    return deleted


def reset_for_testing() -> None:
    """仅测试 / 冒烟脚本使用，用于切换 TAURUS_EDITION 环境变量后重置单例。"""
    global _GATE
    with _LOCK:
        _GATE = None