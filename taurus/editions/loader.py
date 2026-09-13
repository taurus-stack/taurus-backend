"""
Edition Loader + 全局 EditionGate（全功能开源版本）。

单一版本不再按 TAURUS_EDITION 分支：
    · get_edition() -> EditionGate    # 获取全局 gate 单例
    · has_feature(code) -> bool       # 全集恒 True（未注册 code 为 False）
    · require_feature(code)           # DRF ViewSet Action 装饰器（保留接缝，正常不拦截）
    · get_quota(field) -> int|None    # 快捷取配额字段（由 License 状态决定）
    · license_state / branding_allowed / update_channels / service_level
                                      # License 四态与服务等级权益便捷属性
"""

from __future__ import annotations

import threading
import functools
from typing import Any, Callable, Dict, List

from .features import BRANDING_CONFIG_KEYS, describe_feature

# 单一版本兼容名（TAURUS_EDITION 已废弃，字段恒为 "community"）
EDITION_NAME = "community"


class EditionGate:
    """统一入口。后端 ViewSet、Template、Management Commands 都用它。"""

    def __init__(self, edition_name: str | None = None):
        # edition_name 参数仅为兼容保留：单一版本不再按名分支，任何取值均忽略
        self.name = EDITION_NAME
        from .community import get_edition as _loader
        self.edition = _loader()

    # ------------------------------------------------------------------ feature
    def has_feature(self, feature_code: str) -> bool:
        # 全功能版本：feature 全集即放行；License 不再参与功能门禁
        return self.edition.has_feature(feature_code)

    def require_feature(self, feature_code: str) -> Callable:
        """
        DRF ViewSet @action 装饰器（保留接缝）。

        全功能版本下已注册 FeatureCode 恒放行；仅当 feature_code 未在
        ALL_FEATURE_CODES 注册（编程错误）时才拒绝。

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
                        f"功能「{human}」({feature_code}) 未注册，请检查 FeatureCode 定义。"
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
        例：gate.is_over_quota('max_hosts', Host.objects.count())
        """
        limit = self.edition.quota.get(field)
        if limit is None:
            return False
        return current > limit

    # ------------------------------------------------------------------ license / 权益
    @property
    def tier(self) -> str:
        return self.edition.tier  # type: ignore[return-value]

    @property
    def license_status(self) -> Dict[str, Any]:
        return self.edition.license_status

    @property
    def license_state(self) -> str:
        """License 四态：free / licensed / grace / blocked。"""
        return str(self.edition.license_status.get("state", "free"))

    @property
    def branding_allowed(self) -> bool:
        """当前服务等级是否允许白标定制。"""
        return bool(self.edition.license_status.get("branding_allowed", False))

    @property
    def update_channels(self) -> List[str]:
        """当前服务等级可获取的版本升级通道，如 ['stable', 'lts']。"""
        return list(self.edition.license_status.get("update_channels") or ["stable"])

    @property
    def service_level(self) -> Dict[str, Any]:
        """服务等级展示信息：{level, sla, channels}。"""
        return dict(self.edition.license_status.get("service_level") or {})

    def info_payload(self) -> Dict[str, Any]:
        return self.edition.info_payload()


# -------------------------------------------------------------------- 全局单例

_GATE: EditionGate | None = None
_LOCK = threading.Lock()


def _resolve_edition_name() -> str:
    """已废弃：全功能版本不再读取 TAURUS_EDITION，恒返回 "community"。

    函数保留仅为兼容旧测试 / 冒烟脚本调用。
    """
    return EDITION_NAME


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

    全功能版本下已注册 FeatureCode 恒放行。保留统一 403 响应构造，
    确保未注册 code（编程错误）不会退化成 Django 调试页 500。
    """
    def _decorator(view_func):
        @functools.wraps(view_func)
        def _wrapped(*args, **kwargs):
            gate = get_edition()
            if gate.has_feature(feature_code):
                return view_func(*args, **kwargs)

            human = describe_feature(feature_code)
            detail = f"功能「{human}」({feature_code}) 未注册，请检查 FeatureCode 定义。"
            payload = {
                "detail": detail,
                "code": "edition_gate",
                "feature": feature_code,
            }

            # 路径 A：从 args 中找 self（APIView 实例） → 用 self.handle_exception，
            # 错误响应格式与项目其它 DRF PermissionDenied 完全一致。
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

            # 路径 B：未找到 self → 退回 JsonResponse，确保状态码与 JSON 格式正确。
            from django.http import JsonResponse
            from rest_framework import status as _drf_status

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
            f"当前服务等级（{gate.tier}）{human_name} 配额已满（上限 {limit}），"
            "请删除部分后再操作，或升级服务等级。"
        )


def enforce_branding_config(changed_keys) -> None:
    """
    白标强校验：当前服务等级不允许白标定制时，修改品牌配置键抛 PermissionDenied(403)。

    调用方只应传入「值确实发生变化」的品牌键——整表保存会顺带携带未修改的
    品牌项，这类请求必须放行，避免阻止同页普通配置的保存。

    用法（系统配置保存接口）：
        if key in BRANDING_CONFIG_KEYS and new_value != instance.value:
            enforce_branding_config({key})
    """
    keys = {k for k in changed_keys if k in BRANDING_CONFIG_KEYS}
    if not keys:
        return
    gate = get_edition()
    if gate.branding_allowed:
        return
    from rest_framework.exceptions import PermissionDenied
    raise PermissionDenied(
        f"当前服务等级（{gate.tier}）不支持白标定制（{'、'.join(sorted(keys))}），"
        "请升级到专业版或更高服务等级。"
    )


def evict_to_quota(queryset, field: str, order_by: str = 'create_datetime',
                   filter_exclude: dict | None = None) -> int:
    """
    FIFO 淘汰：将 queryset 中最旧的记录删掉直到当前数量 < 配额。

    用法：限制某脚本最多保留 N 个历史版本：
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
    """仅测试 / 冒烟脚本使用：重置 EditionGate 单例并清空 License 验签缓存。"""
    global _GATE
    try:
        from taurus_ee.license import invalidate_cache as _invalidate_license
        _invalidate_license()
    except ImportError:
        pass
    with _LOCK:
        _GATE = None
