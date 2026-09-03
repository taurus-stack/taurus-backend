# -*- coding: utf-8 -*-

"""
@author: Yuan Xiaotian
@contact: QQ:1638245306
@Created on: 2021/6/3 003 0:30
@Remark: Menu button management
"""
from django.db.models import F
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from dvadmin.system.models import MenuButton, RoleMenuButtonPermission, Menu
from dvadmin.utils.json_response import DetailResponse, SuccessResponse
from dvadmin.utils.permission_decorator import require_viewset_perms
from dvadmin.utils.serializers import CustomModelSerializer
from dvadmin.utils.viewset import CustomModelViewSet


class MenuButtonSerializer(CustomModelSerializer):
    """
    Menu button - Serializer
    """

    class Meta:
        model = MenuButton
        fields = ['id', 'name', 'value', 'api', 'method','menu']
        read_only_fields = ["id"]




class MenuButtonCreateUpdateSerializer(CustomModelSerializer):
    """
    Initialize Menu button - Serializer
    """

    class Meta:
        model = MenuButton
        fields = "__all__"
        read_only_fields = ["id"]


@require_viewset_perms(
    list='menubutton:list',
    retrieve='menubutton:retrieve',
    create='menubutton:create',
    update='menubutton:update',
    destroy='menubutton:destroy',
    partial_update='menubutton:update',
    module='menu',
    roles=['admin'],
)
class MenuButtonViewSet(CustomModelViewSet):
    """
    Menu button interface
    list: Query
    create: Create
    update: Modify
    retrieve: Retrieve single
    destroy: Delete
    """
    queryset = MenuButton.objects.order_by('create_datetime')
    serializer_class = MenuButtonSerializer
    create_serializer_class = MenuButtonCreateUpdateSerializer
    update_serializer_class = MenuButtonCreateUpdateSerializer
    extra_filter_class = []

    def list(self, request, *args, **kwargs):
        """
        Override list method
        :param request:
        :param args:
        :param kwargs:
        :return:
        """
        queryset = self.filter_queryset(self.get_queryset()).order_by('name')
        serializer = self.get_serializer(queryset, many=True, request=request)
        return SuccessResponse(serializer.data,msg="Retrieved successfully")

    @action(methods=['get'], detail=False, permission_classes=[IsAuthenticated])
    def menu_button_all_permission(self,request):
        """
        Get all API permission codes for current user
        :param request:
        :return:
        """
        from dvadmin.utils.permission_decorator import get_user_permission_codes
        return DetailResponse(data=get_user_permission_codes(request.user))

    @action(methods=['post'], detail=False, permission_classes=[IsAuthenticated])
    def batch_create(self, request, *args, **kwargs):
        """
        Batch create menu "CRUD" permissions
        The created data comes from Menu, following standard menu creation parameters
        value: Menu's component_name:method
        api: Menu's web_path with '/api' prefix, and adding {id} according to method
        """
        menu_obj = Menu.objects.filter(id=request.data['menu']).first()
        result_list = [
            {'menu': menu_obj.id, 'name': 'Create', 'value': f'{menu_obj.component_name}:Create', 'api': f'/api{menu_obj.web_path}/',
             'method': 1},
            {'menu': menu_obj.id, 'name': 'Delete', 'value': f'{menu_obj.component_name}:Delete', 'api': f'/api{menu_obj.web_path}/{{id}}/',
             'method': 3},
            {'menu': menu_obj.id, 'name': 'Update', 'value': f'{menu_obj.component_name}:Update', 'api': f'/api{menu_obj.web_path}/{{id}}/',
             'method': 2},
            {'menu': menu_obj.id, 'name': 'Search', 'value': f'{menu_obj.component_name}:Search', 'api': f'/api{menu_obj.web_path}/',
             'method': 0},
            {'menu': menu_obj.id, 'name': 'Retrieve', 'value': f'{menu_obj.component_name}:Retrieve', 'api': f'/api{menu_obj.web_path}/{{id}}/',
             'method': 0}]
        serializer = self.get_serializer(data=result_list, many=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return SuccessResponse(serializer.data, msg="Batch created successfully")