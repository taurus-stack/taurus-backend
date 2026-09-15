"""Script security / check rule serializers (EE only)."""
from __future__ import annotations

from rest_framework import serializers
from dvadmin.utils.serializers import CustomModelSerializer
from taurus.models import ScriptCheckRule


class ScriptCheckRuleSerializer(CustomModelSerializer):
    """Script detection rule serializer (EE)."""
    severity_display = serializers.CharField(source='get_severity_display', read_only=True)
    match_type_display = serializers.CharField(source='get_match_type_display', read_only=True)
    scope_display = serializers.CharField(source='get_scope_display', read_only=True)

    class Meta:
        model = ScriptCheckRule
        fields = [
            'id', 'name', 'rule_key', 'description', 'pattern', 'match_type',
            'match_type_display', 'severity', 'severity_display', 'scope',
            'scope_display', 'fix_suggestion', 'is_active', 'sort_order',
            'is_builtin', 'remark', 'creator', 'create_datetime', 'update_datetime',
        ]
        read_only_fields = ['id', 'create_datetime', 'update_datetime', 'is_builtin']
