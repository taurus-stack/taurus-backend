# -*- coding: utf-8 -*-

"""
@author: Yuan Xiaotian
@contact: QQ:1638245306
@Created on: 2021/6/1 22:57
@Remark: Custom viewset
"""
from django.db import transaction
from django_restql.mixins import QueryArgumentsMixin
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes
from rest_framework.decorators import action
from rest_framework.viewsets import ModelViewSet

from dvadmin.system.models import MenuField
from dvadmin.utils.filters import DataLevelPermissionsFilter, CoreModelFilterBankend
from dvadmin.utils.import_export_mixin import ExportSerializerMixin, ImportSerializerMixin
from dvadmin.utils.json_response import SuccessResponse, ErrorResponse, DetailResponse
from dvadmin.utils.models import get_custom_app_models
from dvadmin.utils.permission import CustomPermission


class CustomModelViewSet(QueryArgumentsMixin, ModelViewSet, ImportSerializerMixin, ExportSerializerMixin, ):
    """
    Custom ModelViewSet:
    Unified standard response format; different serializers can be used for create, query, modify
    (1) ORM performance optimization, use values_queryset form whenever possible
    (2) xxx_serializer_class serializer used under a specific method (xxx=create|update|list|retrieve|destroy)
    (3) filter_fields = '__all__' by default supports all model field queries (except json fields)
    (4) import_field_dict={} field dictionary for import {model value: model label}
    (5) export_field_label = [] fields for export

    Note: After the permission system migrated to decorator permission codes (require_viewset_perms / require_perm):
    - Globally no longer attaches DataLevelPermissionsFilter by default (forced dept-level data filtering
      by dept_belong_id / creator dept ownership), to prevent business failures in cross-dept collaboration
      scenarios (approval, execution, etc.) where users can see candidates but not the records.
    - ViewSets requiring fine-grained data isolation should explicitly implement it in their own
      get_queryset / filter_queryset (e.g. filter by submitter / candidate_approvers / dept_belong_id
      or business ownership tables).
    - To enable the legacy mode of "filter by current dept and sub-depts", override in the ViewSet:
          extra_filter_class = [CoreModelFilterBankend, DataLevelPermissionsFilter]
    """
    values_queryset = None
    ordering_fields = '__all__'
    create_serializer_class = None
    update_serializer_class = None
    filter_fields = '__all__'
    search_fields = ()
    extra_filter_class = [CoreModelFilterBankend]
    permission_classes = [CustomPermission]
    import_field_dict = {}
    export_field_label = {}

    def filter_queryset(self, queryset):
        for backend in set(set(self.filter_backends) | set(self.extra_filter_class or [])):
            queryset = backend().filter_queryset(self.request, queryset, self)
        return queryset

    def get_queryset(self):
        if getattr(self, 'values_queryset', None):
            return self.values_queryset
        return super().get_queryset()

    def get_serializer_class(self):
        action_serializer_name = f"{self.action}_serializer_class"
        action_serializer_class = getattr(self, action_serializer_name, None)
        if action_serializer_class:
            return action_serializer_class
        return super().get_serializer_class()

    # Directly modify the original API via many=True to support batch creation
    def get_serializer(self, *args, **kwargs):
        serializer_class = self.get_serializer_class()
        kwargs.setdefault('context', self.get_serializer_context())
        # Always use visible fields as the authority
        can_see = self.get_menu_field(serializer_class)
        # Exclude serializer-level fields
        # sub_set = set(serializer_class._declared_fields.keys()) - set(can_see)
        # for field in sub_set:
        #     serializer_class._declared_fields.pop(field)
        # if not self.request.user.is_superuser:
        #     serializer_class.Meta.fields = can_see
        # Used in paginator
        self.request.permission_fields = can_see
        if isinstance(self.request.data, list):
            with transaction.atomic():
                return serializer_class(many=True, *args, **kwargs)
        else:
            return serializer_class(*args, **kwargs)

    def get_menu_field(self, serializer_class):
        """Get field permissions"""
        finded = False
        for model in get_custom_app_models():
            if model['object'] is serializer_class.Meta.model:
                finded = True
                break
        if finded is False:
            return []
        return MenuField.objects.filter(model=model['model']
                                        ).values('field_name', 'title')

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, request=request)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return DetailResponse(data=serializer.data, msg="Created successfully")

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True, request=request)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True, request=request)
        return SuccessResponse(data=serializer.data, msg="Retrieved successfully")

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return DetailResponse(data=serializer.data, msg="Retrieved successfully")

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, request=request, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        if getattr(instance, '_prefetched_objects_cache', None):
            # If 'prefetch_related' has been applied to a queryset, we need to
            # forcibly invalidate the prefetch cache on the instance.
            instance._prefetched_objects_cache = {}
        return DetailResponse(data=serializer.data, msg="Updated successfully")

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.delete()
        return DetailResponse(data=[], msg="Deleted successfully")

    @extend_schema(
        request=OpenApiTypes.OBJECT,
        parameters=[
            OpenApiParameter(
                name='keys',
                type={'type': 'array', 'items': {'type': 'string'}},
                location='body',
                required=True,
                description='Primary key list'
            )
        ],
        summary='Batch delete'
    )
    @action(methods=['delete'], detail=False)
    def multiple_delete(self, request, *args, **kwargs):
        request_data = request.data
        keys = request_data.get('keys', None)
        if keys:
            self.get_queryset().filter(id__in=keys).delete()
            return SuccessResponse(data=[], msg="Deleted successfully")
        else:
            return ErrorResponse(msg="keys field not provided")