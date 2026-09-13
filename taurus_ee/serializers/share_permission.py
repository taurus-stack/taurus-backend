"""Share (permission definitions + direct shares + share links) serializers (EE)."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework import serializers
from dvadmin.utils.serializers import CustomModelSerializer
from taurus.models import (
    SharePermissionDef,
    ScriptSharePermission,
    WorkflowSharePermission,
    ShareLink,
    ShareLinkAccessLog,
)


class SharePermissionDefSerializer(CustomModelSerializer):
    resource_type_display = serializers.CharField(source='get_resource_type_display', read_only=True)
    category_display = serializers.CharField(source='get_category_display', read_only=True)

    class Meta:
        model = SharePermissionDef
        fields = [
            'id', 'resource_type', 'resource_type_display',
            'perm_code', 'perm_name', 'category', 'category_display',
            'description', 'sort', 'is_active',
            'create_datetime', 'update_datetime',
        ]
        read_only_fields = ['id', 'create_datetime', 'update_datetime']


class SharePermissionDefGroupedSerializer(serializers.Serializer):
    category = serializers.CharField()
    category_display = serializers.CharField()
    perms = SharePermissionDefSerializer(many=True)


class ScriptSharePermissionSerializer(CustomModelSerializer):
    subject_type_display = serializers.CharField(source='get_subject_type_display', read_only=True)
    script_name = serializers.CharField(source='script.name', read_only=True)
    subject_info = serializers.SerializerMethodField()
    perm_details = serializers.SerializerMethodField()

    class Meta:
        model = ScriptSharePermission
        fields = [
            'id', 'script', 'script_name',
            'subject_type', 'subject_type_display', 'subject_id',
            'subject_name_cache', 'subject_info',
            'permissions', 'perm_details',
            'expire_time', 'remark',
            'creator_name', 'create_datetime', 'update_datetime',
        ]
        read_only_fields = ['id', 'create_datetime', 'update_datetime', 'creator_name']

    def get_subject_info(self, obj):
        subject_map = self.context.get('subject_info_map', {})
        return subject_map.get(f'{obj.subject_type}:{obj.subject_id}')

    def get_perm_details(self, obj):
        perm_defs = self.context.get('perm_defs', {})
        return [perm_defs.get(p, {'perm_code': p, 'perm_name': p}) for p in (obj.permissions or [])]


class WorkflowSharePermissionSerializer(CustomModelSerializer):
    subject_type_display = serializers.CharField(source='get_subject_type_display', read_only=True)
    workflow_name = serializers.CharField(source='workflow.name', read_only=True)
    subject_info = serializers.SerializerMethodField()
    perm_details = serializers.SerializerMethodField()

    class Meta:
        model = WorkflowSharePermission
        fields = [
            'id', 'workflow', 'workflow_name',
            'subject_type', 'subject_type_display', 'subject_id',
            'subject_name_cache', 'subject_info',
            'permissions', 'perm_details',
            'expire_time', 'remark',
            'creator_name', 'create_datetime', 'update_datetime',
        ]
        read_only_fields = ['id', 'create_datetime', 'update_datetime', 'creator_name']

    def get_subject_info(self, obj):
        subject_map = self.context.get('subject_info_map', {})
        return subject_map.get(f'{obj.subject_type}:{obj.subject_id}')

    def get_perm_details(self, obj):
        perm_defs = self.context.get('perm_defs', {})
        return [perm_defs.get(p, {'perm_code': p, 'perm_name': p}) for p in (obj.permissions or [])]


class SharePermissionBatchCreateSerializer(serializers.Serializer):
    subjects = serializers.ListField(
        child=serializers.DictField(),
        required=True,
        help_text='[{subject_type, subject_id, subject_name?}]',
    )
    permissions = serializers.ListField(child=serializers.CharField())
    expire_time = serializers.DateTimeField(required=False, allow_null=True, default=None)
    remark = serializers.CharField(required=False, allow_blank=True, allow_null=True, default='')


class ShareLinkSerializer(CustomModelSerializer):
    resource_type_display = serializers.CharField(source='get_resource_type_display', read_only=True)
    access_scope_display = serializers.CharField(source='get_access_scope_display', read_only=True)
    status_display = serializers.SerializerMethodField()
    bind_subject_type_display = serializers.SerializerMethodField()
    resource_name = serializers.SerializerMethodField()
    perm_details = serializers.SerializerMethodField()
    creator_name = serializers.CharField(source='create_user.username', read_only=True, default='')

    class Meta:
        model = ShareLink
        fields = [
            'id', 'resource_type', 'resource_type_display',
            'resource_id', 'resource_name',
            'share_token', 'permissions', 'perm_details',
            'max_access_count', 'current_access_count',
            'expire_time', 'is_active', 'status_display',
            'access_scope', 'access_scope_display',
            'bind_subject_type', 'bind_subject_type_display',
            'bind_subject_id', 'remark',
            'creator_name', 'create_user', 'create_datetime', 'update_datetime',
        ]
        read_only_fields = [
            'id', 'share_token', 'current_access_count',
            'create_datetime', 'update_datetime', 'create_user', 'status_display',
        ]

    def get_status_display(self, obj):
        if not obj.is_active:
            return 'Revoked'
        from django.utils import timezone as _tz
        now = _tz.now()
        if obj.expire_time and obj.expire_time < now:
            return 'Expired'
        if obj.max_access_count and obj.max_access_count > 0:
            if (obj.current_access_count or 0) >= obj.max_access_count:
                return 'Exhausted'
        return 'Active'

    def get_bind_subject_type_display(self, obj):
        if not obj.bind_subject_type:
            return 'Unbound'
        mapping = {'user': 'User', 'role': 'Role', 'dept': 'Department'}
        return mapping.get(obj.bind_subject_type, obj.bind_subject_type)

    def get_resource_name(self, obj):
        return self.context.get('resource_names', {}).get(f'{obj.resource_type}:{obj.resource_id}')

    def get_perm_details(self, obj):
        perm_defs = self.context.get('perm_defs', {})
        return [perm_defs.get(p, {'perm_code': p, 'perm_name': p}) for p in (obj.permissions or [])]

    def validate_access_scope(self, value):
        if value == 'anyone':
            from rest_framework import serializers as _s
            raise _s.ValidationError(
                '"Anyone can access" scope not yet available, '
                'please select "Logged-in users only" or bind to a specific subject'
            )
        return value


class ShareLinkActivateSerializer(serializers.Serializer):
    share_token = serializers.CharField(required=True, max_length=64)


class ShareLinkAccessLogSerializer(CustomModelSerializer):
    share_token = serializers.CharField(source='share_link.share_token', read_only=True, default='')
    access_scope_display = serializers.SerializerMethodField()
    visitor_name = serializers.SerializerMethodField()
    visitor_id = serializers.IntegerField(source='access_user_id', read_only=True, allow_null=True)
    visitor_type = serializers.SerializerMethodField()
    access_status = serializers.BooleanField(source='access_success', read_only=True)
    access_time = serializers.DateTimeField(source='create_datetime', read_only=True)

    class Meta:
        model = ShareLinkAccessLog
        fields = [
            'id', 'share_link', 'share_token',
            'visitor_name', 'visitor_id',
            'visitor_type', 'access_scope_display',
            'client_ip', 'user_agent', 'access_status', 'fail_reason',
            'access_time',
        ]
        read_only_fields = fields

    def get_access_scope_display(self, obj):
        access_scope = getattr(getattr(obj, 'share_link', None), 'access_scope', '') or ''
        mapping = {'anyone': 'Anyone', 'authenticated': 'Logged-in users only', 'bound': 'Bound subject only'}
        return mapping.get(access_scope, access_scope or '-')

    def get_visitor_type(self, obj):
        return 'user' if obj.access_user_id else 'anonymous'

    def get_visitor_name(self, obj):
        if obj.access_user_id:
            visitor_map = self.context.get('visitor_map', {})
            name = visitor_map.get(str(obj.access_user_id))
            if name:
                return name
            u = obj.access_user
            if u:
                return getattr(u, 'name', None) or getattr(u, 'username', None) or f'User#{obj.access_user_id}'
            return f'User#{obj.access_user_id}'
        return 'Anonymous visitor'
