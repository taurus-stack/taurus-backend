# -*- coding: utf-8 -*-
from django.db.models import Q
from rest_framework import serializers

from dvadmin.system.models import Area
from dvadmin.utils.json_response import SuccessResponse
from dvadmin.utils.permission_decorator import require_viewset_perms
from dvadmin.utils.serializers import CustomModelSerializer
from dvadmin.utils.viewset import CustomModelViewSet


class AreaSerializer(CustomModelSerializer):
    """
    Area - Serializer
    """
    pcode_count = serializers.SerializerMethodField(read_only=True)
    hasChild = serializers.SerializerMethodField()
    def get_pcode_count(self, instance: Area):
        return Area.objects.filter(pcode=instance).count()
    def get_hasChild(self, instance):
        hasChild = Area.objects.filter(pcode=instance.code)
        if hasChild:
            return True
        return False
    class Meta:
        model = Area
        fields = "__all__"
        read_only_fields = ["id"]


class AreaCreateUpdateSerializer(CustomModelSerializer):
    """
    Area management - create/update serializer
    """

    class Meta:
        model = Area
        fields = '__all__'


@require_viewset_perms(
    list='area:list',
    retrieve='area:retrieve',
    create='area:create',
    update='area:update',
    destroy='area:destroy',
    partial_update='area:update',
    module='system',
    roles=['admin'],
)
class AreaViewSet(CustomModelViewSet):
    """
    Area management interface
    list: Query
    create: Create
    update: Modify
    retrieve: Retrieve single
    destroy: Delete
    """
    queryset = Area.objects.all()
    serializer_class = AreaSerializer
    extra_filter_class = []

    def get_queryset(self):
        self.request.query_params._mutable = True
        params = self.request.query_params
        pcode = params.get('pcode', None)
        page = params.get('page', None)
        limit = params.get('limit', None)
        if page:
            del params['page']
        if limit:
            del params['limit']
        if params and pcode:
            queryset = self.queryset.filter(enable=True, pcode=pcode)
        else:
            queryset = self.queryset.filter(enable=True)
        return queryset