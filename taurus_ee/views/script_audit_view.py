"""Script audit log ViewSet (EE)."""
from __future__ import annotations

from django.utils.decorators import method_decorator
from dvadmin.utils.viewset import CustomModelViewSet
from taurus.models import ScriptAudit
from taurus.editions import require_feature
from taurus.editions.features import F_SCRIPT_AUDIT_LOG
from taurus_ee.serializers.script_audit import ScriptAuditSerializer


@method_decorator(require_feature(F_SCRIPT_AUDIT_LOG), name='dispatch')
class ScriptAuditViewSet(CustomModelViewSet):
    """脚本操作审计日志（EE 专属）。"""
    queryset = ScriptAudit.objects.all()
    serializer_class = ScriptAuditSerializer
    filterset_fields = ['script', 'oper_type', 'operator']
    search_fields = ['detail', 'operator_name', 'client_ip']
    ordering = ['-create_datetime']
