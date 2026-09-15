"""taurus_ee views — M2.5 log-center & extension centers (6 placeholders).

Role: taurus.views.py Thin Wrappers inherit these classes (via `from taurus_ee.views.xxxx import X as _EE`)
and then are decorated with `@method_decorator(require_feature(F_*), name='dispatch')`.

Exceptions (deliberately NOT in this file):
  - HostLogViewSet / LogCommandViewSet / ContactLeadViewSet / TaskCenterViewSet
    — in M2.5 we keep their implementations in taurus.views.py (unchanged logic) but
    wrap *classes* with the Double Gate @method_decorator(require_feature, name='dispatch')
    pattern.  This avoids duplicating their ~200+ lines (receive / rate throttle /
    aggregated list) while still correctly enforcing FeatureCode gating on the
    dispatch layer — the original view body only runs when the outer Gate allows EE.

Thus M2.5 taurus_ee.views only provides 6 extension-center placeholder ViewSets
(5 real CRUD-empty shells + ContactLead already covered by wrapping).  This is
consistent with docs/ee-module-map.md §M2.5 '6 Empty VS'.
"""
from __future__ import annotations

from rest_framework import viewsets
from dvadmin.utils.viewset import CustomModelViewSet
from dvadmin.utils.json_response import SuccessResponse


# ---------------------------------------------------------------------------
# 6 extension center placeholder ViewSets (M2.5 空壳：EE only，UI 挂载点占位)
# 每个 VS 都提供空 list + (可选) EE 专属说明，供 FeatureCode 网关拦截后在 CE 直接 DENIED。
# ---------------------------------------------------------------------------
class _EEKnowledgeBaseViewSet(CustomModelViewSet):
    """知识库（EE 占位，M2.5 空壳）"""
    # 故意不绑定 queryset / serializer_class，保留空实现 → 仅 CE Gate 时拒绝即可。
    pass


class _EEInspectionCenterViewSet(CustomModelViewSet):
    """巡检中心（EE 占位，M2.5 空壳）"""
    pass


class _EEToolsCenterViewSet(CustomModelViewSet):
    """工具中心（EE 占位，M2.5 空壳）"""
    pass


class _EEBackupRestoreCenterViewSet(CustomModelViewSet):
    """备份恢复中心（EE 占位，M2.5 空壳）"""
    pass


class _EEDownloadCenterViewSet(CustomModelViewSet):
    """客户端打包下载中心（EE 占位，M2.5 空壳）"""
    pass
