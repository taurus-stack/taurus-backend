# -*- coding: utf-8 -*-

"""
@author: Yuan Xiaotian
@contact: QQ:1638245306
@Created on: 2021/6/1 001 22:38
@Remark: Menu modules
"""
from rest_framework import serializers
from rest_framework.decorators import action

from dvadmin.system.models import Menu, MenuButton, RoleMenuPermission
from dvadmin.system.views.menu_button import MenuButtonSerializer
from dvadmin.utils.json_response import DetailResponse, SuccessResponse, ErrorResponse
from dvadmin.utils.permission_decorator import require_viewset_perms
from dvadmin.utils.serializers import CustomModelSerializer
from dvadmin.utils.viewset import CustomModelViewSet


class MenuSerializer(CustomModelSerializer):
    """
    Simple serializer for Menu table
    """
    menuPermission = serializers.SerializerMethodField(read_only=True)
    hasChild = serializers.SerializerMethodField()

    def get_menuPermission(self, instance):
        queryset = instance.menuPermission.order_by('-name').values('id', 'name', 'value')
        # MenuButtonSerializer(instance.menuPermission.all(), many=True)
        if queryset:
            return queryset
        else:
            return None

    def get_hasChild(self, instance):
        hasChild = Menu.objects.filter(parent=instance.id)
        if hasChild:
            return True
        return False

    class Meta:
        model = Menu
        fields = "__all__"
        read_only_fields = ["id"]


class MenuCreateSerializer(CustomModelSerializer):
    """
    Create serializer for Menu table
    """
    name = serializers.CharField(required=False)

    def create(self, validated_data):
        menu_obj = Menu.objects.filter(parent_id=validated_data.get('parent', None)).order_by('-sort').first()
        last_sort = menu_obj.sort if menu_obj else 0
        validated_data['sort'] = last_sort + 1
        return super().create(validated_data)

    class Meta:
        model = Menu
        fields = "__all__"
        read_only_fields = ["id"]


class WebRouterSerializer(CustomModelSerializer):
    """
    Simple serializer for frontend menu routes

    M3.1 — 新增 requires_feature 字段：逗号分隔的 FeatureCode 列表，
    前端 backEnd.ts._filterTree() 读取此字段做菜单级 Edition 过滤。

    注意：dvadmin Menu model 本身没有 requires_feature 属性
    （它是 M3.1 migration 在 DB 层追加的列），所以用 SerializerMethodField
    + 模块级缓存 dict 避免 N+1 查询。init_edition_features 命令写入后会
    调用 invalidate_menu_requires_feature_cache() 刷新缓存。
    """
    path = serializers.CharField(source="web_path")
    title = serializers.CharField(source="name")
    requires_feature = serializers.SerializerMethodField(read_only=True)

    @staticmethod
    def get_requires_feature(obj) -> str:
        try:
            _ensure_menu_rf_cache()
            return _menu_rf_cache.get(getattr(obj, "pk", None), "")
        except Exception:  # noqa: BLE001
            return ""

    class Meta:
        model = Menu
        fields = (
            'id', 'parent', 'icon', 'sort', 'path', 'name', 'title', 'is_link','link_url', 'is_catalog', 'web_path', 'component',
            'component_name', 'cache', 'visible','is_iframe','is_affix', 'status',
            'requires_feature',  # M3.1 Edition Gate
        )
        read_only_fields = ["id"]


# ---------------------------------------------------------------------------
# M3.1 — menu.requires_feature 模块级缓存（避免 N+1 查询）
#   · _menu_rf_cache: {menu_id: str} — 全量预取 dict
#   · _menu_rf_cache_built: bool — 是否已构建
#   · invalidate_menu_requires_feature_cache(): seed 命令写完后调用
# ---------------------------------------------------------------------------
_menu_rf_cache: dict[int, str] = {}
_menu_rf_cache_built: bool = False


def _ensure_menu_rf_cache() -> dict[int, str]:
    global _menu_rf_cache, _menu_rf_cache_built
    if _menu_rf_cache_built:
        return _menu_rf_cache
    try:
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute(
                "SELECT id, COALESCE(requires_feature, '') "
                "FROM taurus_system_menu"
            )
            _menu_rf_cache = {int(r[0]): (r[1] or "") for r in cur.fetchall()}
    except Exception:  # noqa: BLE001 — migration 未跑时兜底
        _menu_rf_cache = {}
    _menu_rf_cache_built = True
    return _menu_rf_cache


def invalidate_menu_requires_feature_cache() -> None:
    """init_edition_features --apply 写完后调用，强制下一次读取重建缓存."""
    global _menu_rf_cache, _menu_rf_cache_built
    _menu_rf_cache = {}
    _menu_rf_cache_built = False


# 模块 import 时立即构建一次缓存（Django startup 时 DB 已 ready）
try:
    _ensure_menu_rf_cache()
except Exception:
    pass


@require_viewset_perms(
    list='menu:list',
    retrieve='menu:retrieve',
    create='menu:create',
    update='menu:update',
    destroy='menu:destroy',
    partial_update='menu:update',
    move_up='menu:MoveUp',
    move_down='menu:MoveDown',
    module='menu',
    roles=['admin'],
)
class MenuViewSet(CustomModelViewSet):
    """
    Menu management interface
    list: List query
    create: Create
    update: Modify
    retrieve: Retrieve single
    destroy: Delete
    """
    queryset = Menu.objects.all()
    serializer_class = MenuSerializer
    create_serializer_class = MenuCreateSerializer
    update_serializer_class = MenuCreateSerializer
    search_fields = ['name', 'status']
    filterset_fields = ['parent', 'name', 'status', 'is_link', 'visible', 'cache', 'is_catalog']

    @staticmethod
    def _delete_children(menu_ids):
        """Recursively delete menus and all their child menus (defensive fix for db_constraint=False cascading failures)"""
        ids_to_delete = set(menu_ids)
        current_level = list(menu_ids)
        while current_level:
            child_ids = Menu.objects.filter(parent_id__in=current_level).values_list('id', flat=True)
            child_ids = list(child_ids)
            if child_ids:
                ids_to_delete.update(child_ids)
                current_level = child_ids
            else:
                current_level = []
        RoleMenuPermission.objects.filter(menu_id__in=ids_to_delete).delete()
        MenuButton.objects.filter(menu_id__in=ids_to_delete).delete()
        Menu.objects.filter(id__in=ids_to_delete).delete()

    @staticmethod
    def _renormalize_sibling_sort(parent):
        """Fix sibling menu sort values: reorder by current (sort,id) to 1,2,3..., eliminating OrderException from duplicates"""
        siblings = list(Menu.objects.filter(parent=parent).order_by('sort', 'id').values_list('id', flat=True))
        updates = []
        for idx, mid in enumerate(siblings, start=1):
            updates.append(Menu(id=mid, sort=idx))
        if updates:
            Menu.objects.bulk_update(updates, ['sort'])

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        parent_of_deleted = instance.parent
        self._delete_children([instance.id])
        self._renormalize_sibling_sort(parent_of_deleted)
        return DetailResponse(data=[], msg="Deleted successfully")

    @action(methods=['delete'], detail=False)
    def multiple_delete(self, request, *args, **kwargs):
        request_data = request.data
        keys = request_data.get('keys', None)
        if keys:
            affected_parents = list(set(
                Menu.objects.filter(id__in=keys).values_list('parent_id', flat=True)
            ))
            self._delete_children(list(keys))
            for p in affected_parents:
                self._renormalize_sibling_sort(p)
            return SuccessResponse(data=[], msg="Deleted successfully")
        else:
            return ErrorResponse(msg="keys field not provided")

    def list(self, request):
        """Lazy load"""
        request.query_params._mutable = True
        params = request.query_params
        parent = params.get('parent', None)
        page = params.get('page', None)
        limit = params.get('limit', None)
        if page:
            del params['page']
        if limit:
            del params['limit']
        if params:
            if parent:
                queryset = self.queryset.filter(parent=parent)
            else:
                queryset = self.queryset.filter()
        else:
            queryset = self.queryset.filter(parent__isnull=True)
        queryset = self.filter_queryset(queryset).order_by('sort', 'id')
        serializer = MenuSerializer(queryset, many=True, request=request)
        data = serializer.data
        return SuccessResponse(data=data)

    @action(methods=['GET'], detail=False, permission_classes=[])
    def web_router(self, request):
        """Used by frontend to fetch routes for current role"""
        user = request.user
        if user.is_superuser:
            queryset = self.queryset.filter(status=1).order_by('sort', 'id')
        else:
            role_list = user.role.values_list('id', flat=True)
            menu_list = RoleMenuPermission.objects.filter(role__in=role_list).values_list('menu_id', flat=True)
            queryset = Menu.objects.filter(id__in=menu_list, status=1).order_by('sort', 'id')
        serializer = WebRouterSerializer(queryset, many=True, request=request)
        data = serializer.data
        return SuccessResponse(data=data, total=len(data), msg="Retrieved successfully")

    @action(methods=['GET'], detail=False, permission_classes=[])
    def get_all_menu(self, request):
        """Used by menu management to fetch all menus"""
        user = request.user
        queryset = self.queryset.all().order_by('sort', 'id')
        if not user.is_superuser:
            role_list = user.role.values_list('id', flat=True)
            menu_list = RoleMenuPermission.objects.filter(role__in=role_list).values_list('menu_id')
            queryset = Menu.objects.filter(id__in=menu_list).order_by('sort', 'id')
        serializer = WebRouterSerializer(queryset, many=True, request=request)
        data = serializer.data
        return SuccessResponse(data=data, total=len(data), msg="Retrieved successfully")

    @action(methods=['POST'], detail=False, permission_classes=[])
    def move_up(self, request):
        """Move menu up: determine display order by (sort,id), swap sort with immediate previous sibling only"""
        menu_id = request.data.get('menu_id')
        try:
            menu = Menu.objects.get(id=menu_id)
        except Menu.DoesNotExist:
            return ErrorResponse(msg="Menu does not exist")
        siblings_before = list(
            Menu.objects.filter(parent=menu.parent).order_by('sort', 'id').values_list('id', flat=True)
        )
        try:
            pos = siblings_before.index(menu.id)
        except ValueError:
            return SuccessResponse(data=[], msg="Moved up successfully")
        if pos <= 0:
            return SuccessResponse(data=[], msg="Already at the top")
        swap_id = siblings_before[pos - 1]
        swap_menu = Menu.objects.get(id=swap_id)
        menu.sort, swap_menu.sort = swap_menu.sort, menu.sort
        menu.save()
        swap_menu.save()
        self._renormalize_sibling_sort(menu.parent)
        return SuccessResponse(data=[], msg="Moved up successfully")

    @action(methods=['POST'], detail=False, permission_classes=[])
    def move_down(self, request):
        """Move menu down: determine display order by (sort,id), swap sort with immediate next sibling only"""
        menu_id = request.data.get('menu_id')
        try:
            menu = Menu.objects.get(id=menu_id)
        except Menu.DoesNotExist:
            return ErrorResponse(msg="Menu does not exist")
        siblings_before = list(
            Menu.objects.filter(parent=menu.parent).order_by('sort', 'id').values_list('id', flat=True)
        )
        try:
            pos = siblings_before.index(menu.id)
        except ValueError:
            return SuccessResponse(data=[], msg="Moved down successfully")
        if pos >= len(siblings_before) - 1:
            return SuccessResponse(data=[], msg="Already at the bottom")
        swap_id = siblings_before[pos + 1]
        swap_menu = Menu.objects.get(id=swap_id)
        menu.sort, swap_menu.sort = swap_menu.sort, menu.sort
        menu.save()
        swap_menu.save()
        self._renormalize_sibling_sort(menu.parent)
        return SuccessResponse(data=[], msg="Moved down successfully")