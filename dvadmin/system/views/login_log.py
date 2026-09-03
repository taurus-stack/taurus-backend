# -*- coding: utf-8 -*-

"""
@author: Yuan Xiaotian
@contact: QQ:1638245306
@Created on: 2021/6/3 003 0:30
@Remark: Button permission management
"""
from dvadmin.system.models import LoginLog
from dvadmin.utils.serializers import CustomModelSerializer
from dvadmin.utils.viewset import CustomModelViewSet


class LoginLogSerializer(CustomModelSerializer):
    """
    Login log - Serializer
    """

    class Meta:
        model = LoginLog
        fields = "__all__"
        read_only_fields = ["id"]


class LoginLogViewSet(CustomModelViewSet):
    """
    Login log interface
    list: Query
    create: Create
    update: Modify
    retrieve: Retrieve single
    destroy: Delete
    """
    queryset = LoginLog.objects.all()
    serializer_class = LoginLogSerializer
    extra_filter_class = []