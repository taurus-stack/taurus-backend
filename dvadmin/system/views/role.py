# -*- coding: utf-8 -*-

"""
@author: Yuan Xiaotian
@contact: QQ:1638245306
@Created on: 2021/6/3 003 0:30
@Remark: Role management
"""
from django_restql.mixins import QueryArgumentsMixin
from rest_framework import serializers

from dvadmin.system.models import Role, Menu, MenuButton
from dvadmin.system.views.dept import DeptSerializer
from dvadmin.system.views.menu import MenuSerializer
from dvadmin.system.views.menu_button import MenuButtonSerializer
from dvadmin.utils.crud_mixin import FastCrudMixin
from dvadmin.utils.field_permission import FieldPermissionMixin
from dvadmin.utils.permission_decorator import require_viewset_perms
from dvadmin.utils.serializers import CustomModelSerializer
from dvadmin.utils.validator import CustomUniqueValidator
from dvadmin.utils.viewset import CustomModelViewSet


class RoleSerializer(CustomModelSerializer, QueryArgumentsMixin):
    """
    Role serializer
    """

    class Meta:
        model = Role
        fields = "__all__"
        read_only_fields = ["id"]


class RoleCreateUpdateSerializer(CustomModelSerializer):
    """
    Serializer for role create/update operations
    """
    menu = MenuSerializer(many=True, read_only=True)
    dept = DeptSerializer(many=True, read_only=True)
    permission = MenuButtonSerializer(many=True, read_only=True)
    key = serializers.CharField(max_length=50,
                                validators=[
                                    CustomUniqueValidator(queryset=Role.objects.all(), message="Permission key must be unique")])
    name = serializers.CharField(max_length=50, validators=[CustomUniqueValidator(queryset=Role.objects.all())])

    def validate(self, attrs: dict):
        return super().validate(attrs)

    # def save(self, **kwargs):
    #     is_superuser = self.request.user.is_superuser
    #     if not is_superuser:
    #         self.validated_data.pop('admin')
    #     data = super().save(**kwargs)
    #     return data

    class Meta:
        model = Role
        fields = '__all__'


class MenuPermissionSerializer(CustomModelSerializer):
    """
    Menu button permissions
    """
    menuPermission = serializers.SerializerMethodField()

    def get_menuPermission(self, instance):
        is_superuser = self.request.user.is_superuser
        if is_superuser:
            queryset = MenuButton.objects.filter(menu__id=instance.id)
        else:
            menu_permission_id_list = self.request.user.role.values_list('permission', flat=True)
            queryset = MenuButton.objects.filter(id__in=menu_permission_id_list, menu__id=instance.id)
        serializer = MenuButtonSerializer(queryset, many=True, read_only=True)
        return serializer.data

    class Meta:
        model = Menu
        fields = ['id', 'parent', 'name', 'menuPermission']


class MenuButtonPermissionSerializer(CustomModelSerializer):
    """
    Menu and button permissions
    """
    isCheck = serializers.SerializerMethodField()

    def get_isCheck(self, instance):
        is_superuser = self.request.user.is_superuser
        if is_superuser:
            return True
        else:
            return MenuButton.objects.filter(
                menu__id=instance.id,
                role__id__in=self.request.user.role.values_list('id', flat=True),
            ).exists()

    class Meta:
        model = Menu
        fields = '__all__'


@require_viewset_perms(
    list='role:list',
    retrieve='role:retrieve',
    create='role:create',
    update='role:update',
    destroy='role:destroy',
    partial_update='role:update',
    module='role',
    roles=['admin'],
)
class RoleViewSet(CustomModelViewSet, FastCrudMixin, FieldPermissionMixin, QueryArgumentsMixin):
    """
    Role management interface
    list: List query
    create: Create
    update: Modify
    retrieve: Retrieve single
    destroy: Delete
    """
    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    create_serializer_class = RoleCreateUpdateSerializer
    update_serializer_class = RoleCreateUpdateSerializer
    search_fields = ['name', 'key']
    query_arguments = {
        'name': ['contains']
    }