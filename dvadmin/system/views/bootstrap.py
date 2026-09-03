from django.db import connection
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from application import dispatch
from dvadmin.system.models import (
    Dept,
    Menu,
    MenuButton,
    RoleMenuButtonPermission,
    RoleMenuPermission,
    SystemConfig,
)
from dvadmin.system.permission_models import PermissionCode, RolePermission
from dvadmin.system.views.menu import WebRouterSerializer
from dvadmin.utils.json_response import DetailResponse


class BootstrapViewSet(APIView):
    """
    Merged initialization interface, combines 6 independent requests into 1 to reduce join overhead on weak networks.
    return: user_info / menu / btn_permission / system_config / dept / dictionary
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        user_info = self._get_user_info(user)
        menu = self._get_menu(user)
        btn_permission = self._get_btn_permission(user)
        system_config = self._get_system_config()
        dept = self._get_dept()
        dictionary = self._get_dictionary()

        return DetailResponse(data={
            "user_info": user_info,
            "menu": menu,
            "btn_permission": btn_permission,
            "system_config": system_config,
            "dept": dept,
            "dictionary": dictionary,
        })

    @staticmethod
    def _get_user_info(user):
        result = {
            "id": user.id,
            "username": user.username,
            "name": user.name,
            "mobile": user.mobile,
            "user_type": user.user_type,
            "gender": user.gender,
            "email": user.email,
            "avatar": user.avatar,
            "dept": user.dept_id,
            "is_superuser": user.is_superuser,
            "role": user.role.values_list("id", flat=True),
        }
        if hasattr(connection, "tenant"):
            result["tenant_id"] = connection.tenant and connection.tenant.id
            result["tenant_name"] = connection.tenant and connection.tenant.name
        dept = getattr(user, "dept", None)
        if dept:
            result["dept_info"] = {"dept_id": dept.id, "dept_name": dept.name}
        else:
            result["dept_info"] = {"dept_id": None, "dept_name": "No department"}
        role = getattr(user, "role", None)
        if role:
            result["role_info"] = role.values("id", "name", "key")
        return result

    @staticmethod
    def _get_menu(user):
        if user.is_superuser:
            queryset = Menu.objects.filter(status=1).order_by('sort', 'id')
        else:
            role_list = user.role.values_list("id", flat=True)
            menu_list = RoleMenuPermission.objects.filter(role__in=role_list).values_list("menu_id", flat=True)
            queryset = Menu.objects.filter(id__in=menu_list, status=1).order_by('sort', 'id')
        serializer = WebRouterSerializer(queryset, many=True)
        return serializer.data

    @staticmethod
    def _get_btn_permission(user):
        from dvadmin.utils.permission_decorator import get_user_permission_codes
        return get_user_permission_codes(user)

    @staticmethod
    def _get_system_config():
        data = dispatch.get_system_config()
        if not data:
            dispatch.refresh_system_config()
            data = dispatch.get_system_config()
        backend_config = [
            f"{ele.get('parent__key')}.{ele.get('key')}"
            for ele in SystemConfig.objects.filter(status=False, parent_id__isnull=False).values("parent__key", "key")
        ]
        return dict(filter(lambda x: x[0] not in backend_config, data.items()))

    @staticmethod
    def _get_dept():
        return list(Dept.objects.filter(status=True).order_by("sort").values("name", "id", "parent"))

    @staticmethod
    def _get_dictionary():
        data = [ele for ele in dispatch.get_dictionary_config().values()]
        if not data:
            dispatch.refresh_dictionary()
            data = [ele for ele in dispatch.get_dictionary_config().values()]
        return data