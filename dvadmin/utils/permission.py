# -*- coding: utf-8 -*-

"""
@author: Yuan Xiaotian
@contact: QQ:1638245306
@Created on: 2021/6/6 10:30
@Remark: Custom permissions
"""
import re
import logging

from django.contrib.auth.models import AnonymousUser
from django.db.models import F
from rest_framework.permissions import BasePermission

from dvadmin.system.models import ApiWhiteList, RoleMenuButtonPermission
from dvadmin.utils.permission_decorator import PermissionRegistry, _check_user_perm

logger = logging.getLogger(__name__)


def ValidationApi(reqApi, validApi):
    """
    Verify whether current user has API permission
    :param reqApi: Currently requested API
    :param validApi: API used for validation
    :return: True or False
    """
    if validApi is not None:
        valid_api = validApi.replace('{id}', '.*?')
        matchObj = re.match(valid_api, reqApi, re.M | re.I)
        if matchObj:
            return True
        else:
            return False
    else:
        return False


class AnonymousUserPermission(BasePermission):
    """
    Anonymous user permission
    """

    def has_permission(self, request, view):
        if isinstance(request.user, AnonymousUser):
            return False
        return True


def ReUUID(api):
    """
    Replace the uuid in the API
    :param api:
    :return:
    """
    pattern = re.compile(r'[a-f\d]{4}(?:[a-f\d]{4}-){4}[a-f\d]{12}/$')
    m = pattern.search(api)
    if m:
        res = api.replace(m.group(0), ".*/")
        return res
    else:
        return None


class CustomPermission(BasePermission):
    """Custom permission

    Strategy (during permission decorator migration):
    1. Super admin passes through directly
    2. Whitelist passes through directly
    3. If View/Action declares permission codes via @require_viewset_perms / @require_perm -> strictly validate permission codes
    4. If no permission code declared (legacy business / new modules not annotated) -> **default allow**, controlled by business-layer
       get_queryset / IsAuthenticated and other mechanisms for isolation. Avoid business failures during migration
       such as admin not being able to see approval records.
    """

    def has_permission(self, request, view):
        if isinstance(request.user, AnonymousUser):
            return False
        if request.user.is_superuser:
            return True

        api = request.path
        method = request.method
        method_list = ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH']
        method_idx = method_list.index(method) if method in method_list else 0

        api_white_list = ApiWhiteList.objects.values(permission__api=F('url'), permission__method=F('method'))
        api_white_list = [
            str(item.get('permission__api').replace('{id}', '([a-zA-Z0-9-]+)')) + ":" + str(
                item.get('permission__method')) + '$' for item in api_white_list if item.get('permission__api')]
        new_api = api + ":" + str(method_idx)
        for item in api_white_list:
            if re.match(item, new_api, re.M | re.I):
                return True

        if not hasattr(request.user, "role"):
            return False

        view_class_name = view.__class__.__name__
        action_name = getattr(view, 'action', None)
        method_perm_code = None
        if action_name:
            method_perm_code = PermissionRegistry.get_view_perm(view_class_name, action_name)
        if method_perm_code is None:
            view_handler = getattr(view, action_name, None) if action_name else None
            if view_handler is not None:
                method_perm_code = getattr(view_handler, '_perm_code', None)

        if method_perm_code:
            logger.debug(f"[Decorator permission] {view_class_name}.{action_name} -> perm_code={method_perm_code}")
            if _check_user_perm(request.user, method_perm_code):
                return True
            return False

        logger.info(f"[Default allow] {view_class_name}.{action_name} has no decorator permission code declared, access allowed (business layer controls itself)")
        return True