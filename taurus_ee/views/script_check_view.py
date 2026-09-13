"""Script security / script-check rule ViewSet (EE).
F_SECURITY_CHECK 能力 Gate 打开，社区版 403。
"""
from __future__ import annotations

from django.utils.decorators import method_decorator
from dvadmin.utils.viewset import CustomModelViewSet
from dvadmin.utils.json_response import (
    SuccessResponse, DetailResponse, ErrorResponse,
)
from rest_framework.decorators import action

from taurus.models import ScriptCheckRule, Script
from taurus.editions import require_feature
from taurus.editions.features import F_SCRIPT_SECURITY_CHECK
from taurus_ee.serializers.script_check import ScriptCheckRuleSerializer


@method_decorator(require_feature(F_SCRIPT_SECURITY_CHECK), name='dispatch')
class ScriptCheckRuleViewSet(CustomModelViewSet):
    """Script detect 规则管理（EE 专属）。"""
    queryset = ScriptCheckRule.objects.all()
    serializer_class = ScriptCheckRuleSerializer
    filter_fields = ['severity', 'scope', 'is_active', 'match_type']
    search_fields = ['name', 'rule_key', 'description', 'pattern']

    def perform_create(self, serializer):
        super().perform_create(serializer)
        from taurus.script_checker.custom_rule import CustomRuleChecker
        CustomRuleChecker.invalidate_cache()

    def perform_update(self, serializer):
        super().perform_update(serializer)
        from taurus.script_checker.custom_rule import CustomRuleChecker
        CustomRuleChecker.invalidate_cache()

    def perform_destroy(self, instance):
        super().perform_destroy(instance)
        from taurus.script_checker.custom_rule import CustomRuleChecker
        CustomRuleChecker.invalidate_cache()

    @action(detail=False, methods=['post'], url_path='init-default')
    def init_default(self, request):
        """Initialize 内置默认规则。"""
        from taurus.script_checker.custom_rule import DEFAULT_RULES, CustomRuleChecker

        existing_keys = set(ScriptCheckRule.objects.values_list('rule_key', flat=True))
        created = 0
        for rule_key, name, pattern, match_type, severity, scope, fix in DEFAULT_RULES:
            if rule_key in existing_keys:
                continue
            ScriptCheckRule.objects.create(
                rule_key=rule_key,
                name=name,
                description=name,
                pattern=pattern,
                match_type=match_type,
                severity=severity,
                scope=scope,
                fix_suggestion=fix,
                is_active=True,
                is_builtin=True,
                sort_order=100,
            )
            created += 1

        CustomRuleChecker.invalidate_cache()
        return DetailResponse(
            data={'created': created},
            msg=f"Initialized {created} built-in rules successfully",
        )

    @action(detail=True, methods=['post'], url_path='toggle')
    def toggle(self, request, pk=None):
        rule = self.get_object()
        rule.is_active = not rule.is_active
        rule.save()
        from taurus.script_checker.custom_rule import CustomRuleChecker
        CustomRuleChecker.invalidate_cache()
        return DetailResponse(
            data={'id': rule.id, 'is_active': rule.is_active},
            msg="Operation successful",
        )

    @action(detail=False, methods=['post'], url_path='batch/enable')
    def batch_enable(self, request):
        ids = request.data.get('ids', [])
        if ids:
            self.queryset.filter(id__in=ids).update(is_active=True)
        from taurus.script_checker.custom_rule import CustomRuleChecker
        CustomRuleChecker.invalidate_cache()
        return SuccessResponse(msg="Bulk enabled")

    @action(detail=False, methods=['post'], url_path='batch/disable')
    def batch_disable(self, request):
        ids = request.data.get('ids', [])
        if ids:
            self.queryset.filter(id__in=ids).update(is_active=False)
        from taurus.script_checker.custom_rule import CustomRuleChecker
        CustomRuleChecker.invalidate_cache()
        return SuccessResponse(msg="Bulk disabled")
