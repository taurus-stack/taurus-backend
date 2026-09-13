"""
全功能开源版本的唯一 Edition 实现（文件名 community.py 仅作历史兼容保留）。

行为全部由 License 状态机驱动（taurus_ee.license.load_license）：
  · free      无 License              → tier=community，max_hosts=50，全功能
  · licensed  验签有效且未过期         → License tier 配额与服务等级权益
  · grace     过期 ≤ 30 天宽限期       → 保留原 tier 配额，仅告警
  · blocked   过期超宽限/指纹不符/坏签 → 回退 community，max_hosts=50

features 恒为 ALL_FEATURE_CODES 全集——功能不做门禁，License 只决定配额与服务等级。
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet

from .base import BaseEdition, _FREE_LICENSE_FALLBACK
from .features import ALL_FEATURE_CODES


class CommunityEdition(BaseEdition):
    """单一全功能版本（edition_name 恒为 "community"，仅兼容保留）。"""

    @property
    def edition_name(self):
        return "community"  # type: ignore[return-value]

    @property
    def _license_obj(self):
        """懒加载 LicenseStatus（load_license 内部带缓存）；taurus_ee 缺失时返回 None。

        热路径（quota/tier）直接读对象属性，避免 to_dict() 内 hosts_used 的 DB COUNT；
        仅 /edition/info 序列化时才支付一次 COUNT。
        """
        try:
            from taurus_ee.license import load_license  # type: ignore
            return load_license()
        except ImportError:
            return None

    @property
    def tier(self):
        obj = self._license_obj
        return (obj.tier if obj is not None else "community")  # type: ignore[return-value]

    @property
    def features(self) -> FrozenSet[str]:
        # 全功能版本：返回已注册 FeatureCode 全集，Gate 不再做 License 双条件拦截
        return ALL_FEATURE_CODES

    @property
    def quota(self) -> Dict[str, int | None]:
        obj = self._license_obj
        if obj is None:
            return dict(_FREE_LICENSE_FALLBACK["quota"])
        return dict(obj.quota)

    @property
    def license_status(self) -> Dict[str, Any]:
        obj = self._license_obj
        if obj is None:
            return dict(_FREE_LICENSE_FALLBACK)
        return obj.to_dict()


def get_edition() -> CommunityEdition:
    """editions.loader 内部调用该工厂创建唯一 Edition 实例。"""
    return CommunityEdition()
