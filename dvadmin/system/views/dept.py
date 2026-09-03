# -*- coding: utf-8 -*-

"""
@author: H0nGzA1
@contact: QQ:2505811377
@Remark: Dept management
"""
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from dvadmin.system.models import Dept, RoleMenuButtonPermission, Users
from dvadmin.utils.json_response import DetailResponse, SuccessResponse, ErrorResponse
from dvadmin.utils.permission_decorator import require_viewset_perms
from dvadmin.utils.serializers import CustomModelSerializer
from dvadmin.utils.viewset import CustomModelViewSet


class DeptSerializer(CustomModelSerializer):
    """
    Dept serializer
    """
    parent_name = serializers.CharField(read_only=True, source='parent.name')
    status_label = serializers.SerializerMethodField()
    has_children = serializers.SerializerMethodField()
    hasChild = serializers.SerializerMethodField()

    dept_user_count = serializers.SerializerMethodField()

    def get_dept_user_count(self, obj: Dept):
        return Users.objects.filter(dept=obj).count()

    def get_hasChild(self, instance):
        hasChild = Dept.objects.filter(parent=instance.id)
        if hasChild:
            return True
        return False

    def get_status_label(self, obj: Dept):
        if obj.status:
            return "Enabled"
        return "Disabled"

    def get_has_children(self, obj: Dept):
        return Dept.objects.filter(parent_id=obj.id).count()

    class Meta:
        model = Dept
        fields = '__all__'
        read_only_fields = ["id"]


class DeptImportSerializer(CustomModelSerializer):
    """
    Dept import serializer
    """

    class Meta:
        model = Dept
        fields = '__all__'
        read_only_fields = ["id"]


class DeptCreateUpdateSerializer(CustomModelSerializer):
    """
    Serializer for dept create/update operations
    """

    def create(self, validated_data):
        value = validated_data.get('parent', None)
        if value is None:
            validated_data['parent'] = self.request.user.dept
        dept_obj = Dept.objects.filter(parent=self.request.user.dept).order_by('-sort').first()
        last_sort = dept_obj.sort if dept_obj else 0
        validated_data['sort'] = last_sort + 1
        instance = super().create(validated_data)
        instance.dept_belong_id = instance.id
        instance.save()
        return instance

    class Meta:
        model = Dept
        fields = '__all__'


@require_viewset_perms(
    list='dept:list',
    retrieve='dept:retrieve',
    create='dept:create',
    update='dept:update',
    destroy='dept:destroy',
    partial_update='dept:update',
    move_up='dept:MoveUp',
    move_down='dept:MoveDown',
    module='dept',
    roles=['admin'],
)
class DeptViewSet(CustomModelViewSet):
    """
    Dept management interface
    list: List query
    create: Create
    update: Modify
    retrieve: Retrieve single
    destroy: Delete
    """
    queryset = Dept.objects.all()
    serializer_class = DeptSerializer
    create_serializer_class = DeptCreateUpdateSerializer
    update_serializer_class = DeptCreateUpdateSerializer
    filter_fields = ['name', 'id', 'parent']
    search_fields = []
    # extra_filter_class = []
    import_serializer_class = DeptImportSerializer
    import_field_dict = {
        "name": "Department name",
        "key": "Department key",
    }

    def list(self, request, *args, **kwargs):
        # If lazy loading, only return parent
        request.query_params._mutable = True
        params = request.query_params
        parent = params.get('parent', None)
        page = params.get('page', None)
        limit = params.get('limit', None)
        if page:
            del params['page']
        if limit:
            del params['limit']
        if params and parent:
            queryset = self.queryset.filter(status=True, parent=parent)
        else:
            queryset = self.queryset.filter(status=True)
        queryset = self.filter_queryset(queryset)
        serializer = DeptSerializer(queryset, many=True, request=request)
        data = serializer.data
        return SuccessResponse(data=data)

    @action(methods=["GET"], detail=False, permission_classes=[IsAuthenticated], extra_filter_class=[])
    def dept_lazy_tree(self, request, *args, **kwargs):
        parent = self.request.query_params.get('parent')
        is_superuser = request.user.is_superuser
        if is_superuser:
            queryset = Dept.objects.values('id', 'name', 'parent')
        else:
            role_ids = request.user.role.values_list('id', flat=True)
            data_range = RoleMenuButtonPermission.objects.filter(role__in=role_ids).values_list('data_range', flat=True)
            user_dept_id = request.user.dept.id
            dept_list = [user_dept_id]
            data_range_list = list(set(data_range))
            for item in data_range_list:
                if item in [0, 2]:
                    dept_list = [user_dept_id]
                elif item == 1:
                    dept_list = Dept.recursion_all_dept(dept_id=user_dept_id)
                elif item == 3:
                    dept_list = Dept.objects.values_list('id', flat=True)
                elif item == 4:
                    dept_list = request.user.role.values_list('dept', flat=True)
                else:
                    dept_list = []
            queryset = Dept.objects.filter(id__in=dept_list).values('id', 'name', 'parent')
        return DetailResponse(data=queryset, msg="Retrieved successfully")

    @action(methods=["GET"], detail=False, permission_classes=[IsAuthenticated], extra_filter_class=[])
    def all_dept(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        data = queryset.filter(status=True).order_by('sort').values('name', 'id', 'parent')
        return DetailResponse(data=data, msg="Retrieved successfully")

    @action(methods=["GET"], detail=False, permission_classes=[IsAuthenticated], extra_filter_class=[])
    def tree(self, request, *args, **kwargs):
        is_superuser = request.user.is_superuser
        if is_superuser:
            queryset = Dept.objects.filter(status=True).order_by('sort')
        else:
            role_ids = request.user.role.values_list('id', flat=True)
            data_range = RoleMenuButtonPermission.objects.filter(role__in=role_ids).values_list('data_range', flat=True)
            user_dept_id = request.user.dept.id
            dept_list = [user_dept_id]
            data_range_list = list(set(data_range))
            for item in data_range_list:
                if item in [0, 2]:
                    dept_list = [user_dept_id]
                elif item == 1:
                    dept_list = Dept.recursion_all_dept(dept_id=user_dept_id)
                elif item == 3:
                    dept_list = Dept.objects.values_list('id', flat=True)
                elif item == 4:
                    dept_list = request.user.role.values_list('dept', flat=True)
                else:
                    dept_list = []
            queryset = Dept.objects.filter(id__in=dept_list, status=True).order_by('sort')
        serializer = DeptSerializer(queryset, many=True)
        data = self.build_tree(serializer.data)
        return DetailResponse(data=data, msg="Retrieved successfully")

    def build_tree(self, data):
        data_list = list(data)
        item_map = {item['id']: item for item in data_list}
        result = []
        for item in data_list:
            parent_id = item.get('parent')
            if parent_id is None:
                result.append(item)
            else:
                parent = item_map.get(parent_id)
                if parent is not None:
                    parent.setdefault('children', []).append(item)
                else:
                    result.append(item)
        return result

    @action(methods=['POST'], detail=False, permission_classes=[IsAuthenticated])
    def move_up(self, request):
        """Move dept up"""
        dept_id = request.data.get('dept_id')
        try:
            dept = Dept.objects.get(id=dept_id)
        except Dept.DoesNotExist:
            return ErrorResponse(msg="Dept does not exist")
        previous_menu = Dept.objects.filter(sort__lt=dept.sort, parent=dept.parent).order_by('-sort').first()
        if previous_menu:
            previous_menu.sort, dept.sort = dept.sort, previous_menu.sort
            previous_menu.save()
            dept.save()
        return SuccessResponse(data=[], msg="Moved up successfully")

    @action(methods=['POST'], detail=False, permission_classes=[IsAuthenticated])
    def move_down(self, request):
        """Move dept down"""
        dept_id = request.data['dept_id']
        try:
            dept = Dept.objects.get(id=dept_id)
        except Dept.DoesNotExist:
            return ErrorResponse(msg="Dept does not exist")
        next_menu = Dept.objects.filter(sort__gt=dept.sort, parent=dept.parent).order_by('sort').first()
        if next_menu:
            next_menu.sort, dept.sort = dept.sort, next_menu.sort
            next_menu.save()
            dept.save()
        return SuccessResponse(data=[], msg="Moved down successfully")

    @action(methods=['GET'], detail=False, permission_classes=[])
    def dept_info(self, request):
        """Dept info"""
        def inner(did, li):
            sub = Dept.objects.filter(parent_id=did)
            if not sub.exists():
                return li
            for i in sub:
                li.append(i.pk)
                inner(i, li)
            return li
        dept_id = request.query_params.get('dept_id')
        show_all = request.query_params.get('show_all')
        if dept_id is None:
            return ErrorResponse(msg="Dept does not exist")
        if not show_all:
            show_all = 0
        if int(show_all):  # Recursively query all sub-depts under current dept
            all_did = [dept_id]
            inner(dept_id, all_did)
            users = Users.objects.filter(dept_id__in=all_did)
        else:
            if dept_id != '':
                users = Users.objects.filter(dept_id=dept_id)
            else:
                users = Users.objects.none()
        dept_obj = Dept.objects.get(id=dept_id) if dept_id != '' else None
        sub_dept = Dept.objects.filter(parent_id=dept_obj.pk) if dept_id != '' else []
        data = {
            'dept_name': dept_obj and dept_obj.name,
            'dept_user': users.count(),
            'owner': dept_obj and dept_obj.owner,
            'description': dept_obj and dept_obj.description,
            'gender': {
                'male': users.filter(gender=1).count(),
                'female': users.filter(gender=2).count(),
                'unknown': users.filter(gender=0).count(),
            },
            'sub_dept_map': []
        }
        for dept in sub_dept:
            all_did = [dept.pk]
            inner(dept.pk, all_did)
            sub_data = {
                'name': dept.name,
                'count': Users.objects.filter(dept_id__in=all_did).count()
            }
            data['sub_dept_map'].append(sub_data)
        return SuccessResponse(data)