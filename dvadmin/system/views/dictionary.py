# -*- coding: utf-8 -*-

"""
@author: Yuan Xiaotian
@contact: QQ:1638245306
@Created on: 2021/6/3 003 0:30
@Remark: Dictionary management
"""
from rest_framework import serializers
from rest_framework.views import APIView

from application import dispatch
from dvadmin.system.models import Dictionary
from dvadmin.utils.json_response import SuccessResponse
from dvadmin.utils.permission_decorator import require_viewset_perms
from dvadmin.utils.serializers import CustomModelSerializer
from dvadmin.utils.viewset import CustomModelViewSet


class DictionarySerializer(CustomModelSerializer):
    """
    Dictionary - Serializer
    """

    class Meta:
        model = Dictionary
        fields = "__all__"
        read_only_fields = ["id"]





class DictionaryCreateUpdateSerializer(CustomModelSerializer):
    """
    Dictionary management - create/update serializer
    """
    value = serializers.CharField(max_length=100)

    def validate_value(self, value):
        """
        Validate uniqueness of dictionary key under parent
        """
        initial_data = self.initial_data
        parent = initial_data.get('parent',None)
        if parent is None:
            unique =  Dictionary.objects.filter(value=value).exists()
            if unique:
                raise serializers.ValidationError("Dictionary code cannot be duplicated")
        return value

    class Meta:
        model = Dictionary
        fields = '__all__'


@require_viewset_perms(
    list='dictionary:list',
    retrieve='dictionary:retrieve',
    create='dictionary:create',
    update='dictionary:update',
    destroy='dictionary:destroy',
    partial_update='dictionary:update',
    module='system',
    roles=['admin'],
)
class DictionaryViewSet(CustomModelViewSet):
    """
    Dictionary management interface
    list: Query
    create: Create
    update: Modify
    retrieve: Retrieve single
    destroy: Delete
    """
    queryset = Dictionary.objects.all()
    serializer_class = DictionarySerializer
    create_serializer_class = DictionaryCreateUpdateSerializer
    extra_filter_class = []
    search_fields = ['label']

    def get_queryset(self):
        if self.action =='list':
            params = self.request.query_params
            parent = params.get('parent', None)
            if params:
                if parent:
                    queryset = self.queryset.filter(parent=parent)
                else:
                    queryset = self.queryset.filter(parent__isnull=True)
            else:
                queryset = self.queryset.filter(parent__isnull=True)
            return queryset
        else:
            return self.queryset


class InitDictionaryViewSet(APIView):
    """
    Get initialization config
    """
    authentication_classes = []
    permission_classes = []
    queryset = Dictionary.objects.all()

    def get(self, request):
        dictionary_key = self.request.query_params.get('dictionary_key')
        if dictionary_key:
            if dictionary_key == 'all':
                # Support forcing a cache refresh via query param. Useful after a
                # data migration updates dictionary labels (e.g. i18n key rewrite),
                # where the in-process DICTIONARY_CONFIG is stale and a server restart
                # is not desired.
                force_refresh = request.query_params.get('refresh', '').lower() in {'1', 'true', 'yes'}
                if force_refresh:
                    dispatch.refresh_dictionary()
                data = [ele for ele in dispatch.get_dictionary_config().values()]
                if not data:
                    dispatch.refresh_dictionary()
                    data = [ele for ele in dispatch.get_dictionary_config().values()]
            else:
                data = self.queryset.filter(parent__value=dictionary_key, status=True).values('label', 'value', 'type',
                                                                                              'color')
            return SuccessResponse(data=data, msg="Retrieved successfully")
        return SuccessResponse(data=[], msg="Retrieved successfully")


class HostTypeDictionaryViewSet(APIView):
    """
    Get host type dictionary data
    """
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        host_types = [
            {"label": "Linux", "value": "linux"},
            {"label": "Windows", "value": "windows"},
            {"label": "Unknown", "value": "unknown"},
        ]
        return SuccessResponse(data=host_types, msg="Retrieved successfully")