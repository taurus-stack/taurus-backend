"""Script audit log serializer (EE only)."""
from rest_framework import serializers
from dvadmin.utils.serializers import CustomModelSerializer
from taurus.models import ScriptAudit


class ScriptAuditSerializer(CustomModelSerializer):
    script_name = serializers.CharField(source='script.name', read_only=True)
    oper_type_display = serializers.CharField(source='get_oper_type_display', read_only=True)
    username = serializers.CharField(source='operator.username', read_only=True, default='')

    class Meta:
        model = ScriptAudit
        fields = [
            'id', 'script', 'script_name', 'script_version',
            'operator', 'username', 'operator_name', 'oper_type', 'oper_type_display',
            'detail', 'client_ip', 'create_datetime',
        ]
