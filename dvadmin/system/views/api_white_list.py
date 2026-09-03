# -*- coding: utf-8 -*-

"""
@author: Yuan Xiaotian
@contact: QQ:1638245306
@Created on: 2022/1/1 001 9:34
@Remark:
"""
from dvadmin.system.models import ApiWhiteList
from dvadmin.utils.serializers import CustomModelSerializer
from dvadmin.utils.viewset import CustomModelViewSet


class ApiWhiteListSerializer(CustomModelSerializer):
    """
    API whitelist - Serializer
    """

    class Meta:
        model = ApiWhiteList
        fields = "__all__"
        read_only_fields = ["id"]





class ApiWhiteListViewSet(CustomModelViewSet):
    """
    API whitelist
    list: Query
    create: Create
    update: Modify
    retrieve: Retrieve single
    destroy: Delete
    """
    queryset = ApiWhiteList.objects.all()
    serializer_class = ApiWhiteListSerializer
    # permission_classes = []