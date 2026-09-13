"""
BaseEdition — 全功能开源版本的 Edition 契约基类。

全功能版本起不再区分 community/enterprise，契约：
    1. edition_name : 恒为 "community"（字段仅作兼容保留）
    2. tier         : 服务等级，由 License 决定（free 态为 "community"）
    3. features     : ALL_FEATURE_CODES 全集（功能不做门禁）
    4. quota        : License 状态驱动（free/blocked 回退 max_hosts=50）
    5. license_status: License 四态 free/licensed/grace/blocked

EditionGate 在 loader 中组合具体实例，对外暴露 has_feature / quota / License 便捷属性。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, FrozenSet

from .features import EditionName, TierName

# taurus_ee 包不可用时的静态兜底（包常驻后正常不会触发）：
# 等价于 taurus_ee.license.free_status().to_dict()
_FREE_LICENSE_FALLBACK: Dict[str, Any] = {
    "valid": True,
    "state": "free",
    "tier": "community",
    "customer_id": None,
    "customer_name": "社区版（免费）",
    "expires_at": None,
    "fingerprint_ok": True,
    "quota": {
        "max_hosts": 50,
        "max_users": None,
        "max_scheduled_tasks": None,
        "max_script_versions_per_script": None,
        "max_concurrent_executions": None,
        "max_workflows": None,
    },
    "features": [],
    "warnings": [],
    "grace_days_left": None,
    "branding_allowed": False,
    "update_channels": ["stable"],
    "service_level": {
        "level": "社区支持",
        "sla": "社区互助，无商业 SLA",
        "channels": ["官方文档", "GitHub 社区"],
    },
    "hosts_used": None,
}


class BaseEdition(ABC):
    """所有 Edition 的抽象基类（当前仅有单一全功能实现）。"""

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
        # 全功能版本：feature 全集即白名单，未注册的 code 仍返回 False
        return feature_code in self.features

    @property
    def license_status(self) -> Dict[str, Any]:
        """
        License 校验状态。默认返回 free 静态兜底；
        具体实现（CommunityEdition）通过 taurus_ee.license.load_license() 懒加载。
        """
        return dict(_FREE_LICENSE_FALLBACK)

    def info_payload(self) -> Dict[str, Any]:
        """
        返回给前端 /api/taurus/edition/info 的完整 payload。
        包含：edition 兼容字段、tier、全集 features、quota、License 四态与服务等级权益。
        """
        feature_list = sorted(self.features)
        lic = self.license_status
        state = lic.get("state", "free")
        return {
            "edition": self.edition_name,
            "tier": self.tier,
            "features": feature_list,
            "feature_count": len(feature_list),
            "quota": self.quota,
            "license": lic,
            # --- 服务等级权益（与 license 内字段同源，顶层冗余便于前端直接消费）---
            "branding_allowed": bool(lic.get("branding_allowed", False)),
            "update_channels": list(lic.get("update_channels") or ["stable"]),
            "service_level": lic.get("service_level") or {},
            "hosts_used": lic.get("hosts_used"),
            "upgrade": {
                # 已授权（licensed）不展示升级横幅；free/grace/blocked 展示升级/续期引导
                "show_banner": state != "licensed",
                "contact_url": "/#/taurus/contact-lead",  # 前端路由占位
            },
        }
