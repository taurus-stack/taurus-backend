"""
Edition / License 专用 API。

对外 3 个接口（允许匿名访问 edition/info，前端 bootstrap 时即要拿到）：
    GET /api/taurus/edition/info       →   tier + 全集 features + quota + License 四态
                                           + 服务等级权益（branding/update_channels/service_level）
    GET /api/taurus/edition/features   →   精简版：仅 edition + features[]
    GET /api/taurus/edition/describe   →   FeatureCode → 中文描述映射表（UI 文案使用）

全功能版本下 features 恒为全集，前端以 tier / license.state / quota 作为展示与配额判断依据。
"""

from __future__ import annotations

from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from dvadmin.utils.json_response import DetailResponse

from taurus.editions import get_edition
from taurus.editions.features import FEATURE_GROUPS, describe_feature, ALL_FEATURE_CODES


# --------------------------------------------------------------------------- /info
class EditionInfoView(APIView):
    """返回完整的 Edition 信息（前端 Pinia store 初始化时调用）。"""
    permission_classes = [AllowAny]  # bootstrap 登录前就需要拿到 edition 渲染菜单

    def get(self, request):
        gate = get_edition()
        data = gate.info_payload()
        # 附加 Feature 分组（全功能版本 in_edition 恒为 true，字段保留供前端分组渲染）
        data["feature_groups"] = [
            {"group": grp, "items": [{"code": c, "name": n, "in_edition": c in data["features"]}
                                     for c, n in mapping.items()]}
            for grp, mapping in FEATURE_GROUPS.items()
        ]
        return DetailResponse(data=data, msg='success')


# --------------------------------------------------------------------------- /features
class EditionFeaturesView(APIView):
    """精简版：仅 edition + features 列表（给前端 hasFeature 对比用，体积小）。"""
    permission_classes = [AllowAny]

    def get(self, request):
        gate = get_edition()
        return DetailResponse(
            data={
                "edition": gate.name,
                "tier": gate.tier,
                "features": sorted(gate.edition.features),
            },
            msg='success',
        )


# --------------------------------------------------------------------------- /describe
class EditionDescribeView(APIView):
    """返回 FeatureCode → 中文描述映射表（不区分 Edition，全集），供前端 UI 文案用。"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 返回所有已注册 Code（含当前 Edition 不支持的）
        codes = sorted(ALL_FEATURE_CODES)
        data = {
            "total": len(codes),
            "items": [{"code": c, "name": describe_feature(c)} for c in codes],
            "groups": [
                {"group": g, "items": [{"code": c, "name": n} for c, n in m.items()]}
                for g, m in FEATURE_GROUPS.items()
            ],
        }
        return DetailResponse(data=data, msg='success')