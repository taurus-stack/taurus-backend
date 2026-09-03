"""
M4 — Enterprise Edition（商业版）实现 + License 验签接入.

License 来源：taurus_ee.license.load_license()
  · CE 仓库（无 taurus_ee 包）→ try/except ImportError → 用 base 默认静态值
  · EE 仓库（有 taurus_ee 包）→ 调用真实 load_license() → RSA 验签 + 过期 + 机器指纹

双条件 Gate 已经在 loader.has_feature() 实现：
  edition == "enterprise" 且 license_status.valid == True
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Optional

from .base import BaseEdition
from .features import ALL_FEATURE_CODES, QuotaDefaults


def _try_load_license():
    """尝试加载 taurus_ee.license 模块；失败返回 None."""
    try:
        from taurus_ee.license import load_license  # type: ignore
        return load_license()
    except ImportError:
        return None


class EnterpriseEdition(BaseEdition):
    """
    EE 行为由 License 驱动：
      · License valid=True → tier/quota/features 从 License 解析
      · License valid=False → tier 默认 professional，quota 默认 professional，
        features 仍返回 ALL_FEATURE_CODES（但 loader.has_feature() 双条件 Gate
        会根据 license_status.valid=False 把所有 EE feature 拦截为 False）
      · taurus_ee 包不存在（CE 开源仓库）→ 同上 fallback
    """

    def __init__(self) -> None:
        # License 状态：首次访问时 lazy load
        self._license: Optional[Dict[str, Any]] = None

    def _get_license(self) -> Dict[str, Any]:
        """lazy-load License，带 base 默认 fallback."""
        if self._license is not None:
            return self._license
        status = _try_load_license()
        if status is not None:
            self._license = status.to_dict()
        else:
            # taurus_ee 包不存在（CE 仓库）→ 静态无效状态
            self._license = {
                "valid": False,
                "tier": "professional",
                "customer_id": None,
                "customer_name": None,
                "expires_at": None,
                "quota": {},
                "features": [],
                "warnings": [{"code": "NO_LICENSE", "message": "taurus_ee 包不存在，无法加载 License"}],
            }
        return self._license

    @property
    def edition_name(self) -> str:
        return "enterprise"

    @property
    def license_status(self) -> Dict[str, Any]:
        """M4：真实 License 状态（RSA 验签 + 过期 + 机器指纹）."""
        return self._get_license()

    @property
    def tier(self) -> str:
        lic = self._get_license()
        if lic.get("valid"):
            return lic.get("tier", "professional")
        return "professional"

    @property
    def features(self) -> FrozenSet[str]:
        # 商业版包含所有已定义的 Feature Code
        # 双条件 Gate 已在 loader.has_feature() 实现：
        #   edition=enterprise && license_status.valid=True
        # 所以 License 无效时这里返回全集也没关系——Gate 会拦截
        return ALL_FEATURE_CODES

    @property
    def quota(self) -> Dict[str, Optional[int]]:
        lic = self._get_license()
        if lic.get("valid") and lic.get("quota"):
            return dict(lic["quota"])
        # License 无效或缺失 → 默认 Professional 配额
        return dict(QuotaDefaults.PROFESSIONAL)


def get_edition() -> EnterpriseEdition:
    return EnterpriseEdition()
