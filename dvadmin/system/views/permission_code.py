"""
Permission code management view
"""
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from dvadmin.system.models import PermissionCode, RolePermission, Role
from dvadmin.utils.json_response import DetailResponse, ErrorResponse
from dvadmin.utils.serializers import CustomModelSerializer
from dvadmin.utils.viewset import CustomModelViewSet


class PermissionCodeSerializer(CustomModelSerializer):
    """
    Permission code - Serializer
    """
    class Meta:
        model = PermissionCode
        fields = "__all__"
        read_only_fields = ["id"]


class PermissionCodeCreateUpdateSerializer(CustomModelSerializer):
    """
    Permission code create/update - Serializer
    """
    class Meta:
        model = PermissionCode
        fields = "__all__"


class RolePermissionSerializer(CustomModelSerializer):
    """
    Role permission association - Serializer
    """
    permission_code = serializers.CharField(source='permission.code', read_only=True)
    permission_name = serializers.CharField(source='permission.name', read_only=True)
    permission_module = serializers.CharField(source='permission.module', read_only=True)

    class Meta:
        model = RolePermission
        fields = "__all__"
        read_only_fields = ["id"]


class PermissionCodeViewSet(CustomModelViewSet):
    """
    Permission code management interface
    """
    queryset = PermissionCode.objects.all()
    serializer_class = PermissionCodeSerializer
    create_serializer_class = PermissionCodeCreateUpdateSerializer
    update_serializer_class = PermissionCodeCreateUpdateSerializer
    search_fields = ['code', 'name', 'module']
    filterset_fields = ['module', 'status']
    extra_filter_class = []

    @action(methods=['GET'], detail=False, permission_classes=[IsAuthenticated])
    def get_modules(self, request):
        """
        Fetch all module list
        """
        modules = PermissionCode.objects.values_list('module', flat=True).distinct()
        modules = [m for m in modules if m]  # Filter empty values
        return DetailResponse(data=modules)

    @action(methods=['GET'], detail=False, permission_classes=[IsAuthenticated])
    def get_role_permissions(self, request):
        """
        Fetch role's permission code list
        :param request: role_id
        :return: Permission code list (with isCheck flag)
        """
        role_id = request.query_params.get('role_id')
        if not role_id:
            return ErrorResponse(msg="Role ID not found")

        # Fetch all permission codes, group by module
        permissions = PermissionCode.objects.filter(status=True).order_by('module', 'code')
        
        # Fetch role's existing permissions
        role_perm_codes = set(
            RolePermission.objects.filter(role_id=role_id).values_list('permission__code', flat=True)
        )

        # Group by module
        modules_dict = {}
        for perm in permissions:
            module = perm.module or 'Uncategorized'
            if module not in modules_dict:
                modules_dict[module] = []
            
            modules_dict[module].append({
                'id': perm.id,
                'code': perm.code,
                'name': perm.name,
                'module': perm.module,
                'description': perm.description,
                'isCheck': perm.code in role_perm_codes
            })

        # Convert to list format
        data = [
            {'module': module, 'permissions': perms}
            for module, perms in modules_dict.items()
        ]

        return DetailResponse(data=data)

    @action(methods=['PUT'], detail=False, permission_classes=[IsAuthenticated])
    def set_role_permissions(self, request):
        """
        Set role's permission codes
        :param request: role_id, permission_codes (list)
        :return:
        """
        role_id = request.data.get('role_id')
        permission_codes = request.data.get('permission_codes', [])

        if not role_id:
            return ErrorResponse(msg="Role ID not found")

        # Validate role exists
        try:
            role = Role.objects.get(id=role_id)
        except Role.DoesNotExist:
            return ErrorResponse(msg="Role does not exist")

        # Delete role's existing permissions
        RolePermission.objects.filter(role=role).delete()

        # Batch create new permission associations
        if permission_codes:
            permissions = PermissionCode.objects.filter(code__in=permission_codes, status=True)
            role_permissions = [
                RolePermission(role=role, permission=perm)
                for perm in permissions
            ]
            RolePermission.objects.bulk_create(role_permissions)

        return DetailResponse(msg="Permission set successfully")


class RolePermissionViewSet(CustomModelViewSet):
    """
    Role permission association management interface
    """
    queryset = RolePermission.objects.all()
    serializer_class = RolePermissionSerializer
    extra_filter_class = []
    filterset_fields = ['role', 'permission']