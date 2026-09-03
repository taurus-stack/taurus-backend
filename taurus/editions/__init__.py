"""
Taurus Stack Edition Gate (M1).

模块对外唯一入口：
    from taurus.editions import get_edition, has_feature, require_feature

设计约束（单仓库 + 单 Schema 模式）：
    · 所有模型/表仍留在 taurus app，不在此层物理迁移；
    · Edition Gate 仅在 **API/Serializer/UI** 三层做能力隐藏，
      避免 CE → EE 升级时因数据库 schema 分裂导致迁移灾难；
    · 后续 M2 才将 Serializer/ViewSet 迁到 taurus_ee app。
"""

from .loader import (
    get_edition, EditionGate, has_feature, require_feature,
    get_quota, is_over_quota, check_quota, evict_to_quota,
)
from .features import describe_feature  # 供前端 FeatureCode 中文描述表、403 错误文案等处使用

__all__ = [
    "get_edition",
    "EditionGate",
    "has_feature",
    "require_feature",
    "get_quota",
    "is_over_quota",
    "check_quota",
    "evict_to_quota",
    "describe_feature",
]
