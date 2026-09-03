"""
M1.2 — BaseEdition 抽象基类。

Edition 的契约：
    1. edition_name:  Literal["community", "enterprise"]
    2. tier:          阶梯名（CE 恒为 "community"；EE 在 M4 由 License 提供）
    3. features:      FrozenSet[str]，来自 editions.features.* 常量
    4. quota:         Dict[str, int|None]，阶梯配额
    5. license_status: Dict[str, Any]，M4 接入后返回 {"valid": bool, ...}；CE 恒占位

EditionGate 会在 loader 中组合 BaseEdition 具体实例对外暴露 has_feature / require_feature。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, FrozenSet

from .features import EditionName, TierName


class BaseEdition(ABC):
    """所有 Edition 的抽象基类。"""

    # ------------------------------------------------------------------ 必须由子类声明
    @property
    @abstractmethod
    def edition_name(self) -> EditionName: ...

    @property
    @abstractmethod
    def tier(self) -> TierName: ...

    @property
    @abstractmethod
    def features(self) -> FrozenSet[str]: ...

    @property
    @abstractmethod
    def quota(self) -> Dict[str, int | None]: ...

    # ------------------------------------------------------------------ 公共方法

    def has_feature(self, feature_code: str) -> bool:
        return feature_code in self.features

    @property
    def license_status(self) -> Dict[str, Any]:
        """
        License 校验状态（M4 EE 版本重写此属性；CE 默认无 License）。
        返回结构示例：
            {
                "valid": True,
                "tier": "professional",
                "expires_at": "2027-01-01T00:00:00+08:00",
                "customer": "XX 有限公司",
                "hosts_used": 82,
                "users_used": 23,
                "warnings": [{"code": "EXPIRING_SOON", "days_left": 20}],
            }
        """
        if self.edition_name == "community":
            return {
                "valid": True,
                "tier": "community",
                "expires_at": None,
                "customer": "Community Edition",
                "warnings": [],
            }
        # EE 默认占位；M4 License 模块会通过 monkey-patch 或子类覆盖重写
        return {
            "valid": False,
            "tier": self.tier,
            "expires_at": None,
            "customer": None,
            "warnings": [{"code": "NO_LICENSE", "message": "企业版未导入 License，EE 功能暂不可用"}],
        }

    def info_payload(self) -> Dict[str, Any]:
        """
        返回给前端 /api/taurus/edition/info 的完整 payload。
        包含：edition 名称、tier、配额、可用 features（sorted list）、license 状态、
        升级引导文案。
        """
        feature_list = sorted(self.features)
        return {
            "edition": self.edition_name,
            "tier": self.tier,
            "features": feature_list,
            "feature_count": len(feature_list),
            "quota": self.quota,
            "license": self.license_status,
            "upgrade": {
                "show_banner": self.edition_name == "community",
                "contact_url": "/#/taurus/contact-lead",  # 前端路由占位
            },
        }
