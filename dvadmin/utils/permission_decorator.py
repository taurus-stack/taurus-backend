"""
Permission decorator module - directly linked to roles

Reference implementation from os_vue3_admin, permissions directly associated with roles, independent of menus.

Usage:
1. Decorate a single action method:
    from dvadmin.utils.permission_decorator import require_perm

    class UserViewSet(CustomModelViewSet):
        @require_perm('user:create', name='Create user', module='user')
        def create(self, request, *args, **kwargs):
            ...

2. Decorate the entire ViewSet (batch-add permissions to standard CRUD methods):
    from dvadmin.utils.permission_decorator import require_viewset_perms

    @require_viewset_perms(
        list='user:list',
        retrieve='user:retrieve',
        create='user:create',
        update='user:update',
        destroy='user:destroy',
        module='user',
    )
    class UserViewSet(CustomModelViewSet):
        ...

3. Undecorated interfaces default to the original CustomPermission logic (backward compatible with legacy code)
"""
import functools
import inspect
import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

from rest_framework.exceptions import PermissionDenied

logger = logging.getLogger(__name__)

METHOD_LIST = ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"]
METHOD_INT_MAP = {m: i for i, m in enumerate(METHOD_LIST)}


@dataclass
class PermissionEntry:
    """Permission entry"""
    code: str
    name: str = ""
    module: str = ""
    description: str = ""
    view_class: str = ""
    view_func: str = ""
    methods: Set[str] = field(default_factory=set)
    roles: List[str] = field(default_factory=list)


class PermissionRegistry:
    """Permission code registry: global singleton, stores all permissions registered by @require_perm decorators"""

    _entries: Dict[str, PermissionEntry] = {}
    _view_action_map: Dict[str, str] = {}

    @classmethod
    def register(cls, perm_code: str, entry: PermissionEntry):
        """Register permission code"""
        if perm_code in cls._entries:
            existing = cls._entries[perm_code]
            existing.methods.update(entry.methods)
            if not existing.name and entry.name:
                existing.name = entry.name
            if not existing.module and entry.module:
                existing.module = entry.module
            logger.debug(f"Permission code already exists, merging info: {perm_code}")
        else:
            cls._entries[perm_code] = entry
            logger.debug(f"Registered permission code: {perm_code}")

    @classmethod
    def bind_view_perm(cls, view_class_name: str, action_name: str, perm_code: str):
        """Bind view class, action and permission code"""
        key = f"{view_class_name}.{action_name}"
        cls._view_action_map[key] = perm_code

    @classmethod
    def get_view_perm(cls, view_class_name: str, action_name: str) -> Optional[str]:
        """Get the permission code corresponding to a view action"""
        key = f"{view_class_name}.{action_name}"
        return cls._view_action_map.get(key)

    @classmethod
    def all_entries(cls) -> Dict[str, PermissionEntry]:
        """Get all registered permissions"""
        return dict(cls._entries)

    @classmethod
    def clear(cls):
        """Clear the registry"""
        cls._entries.clear()
        cls._view_action_map.clear()


def require_perm(
    perm_code: str,
    name: str = "",
    module: str = "",
    description: str = "",
    methods: Optional[List[str]] = None,
    roles: Optional[List[str]] = None,
):
    """
    Interface permission decorator
    """

    def decorator(func: Callable) -> Callable:
        view_class_name = ""

        entry = PermissionEntry(
            code=perm_code,
            name=name or perm_code,
            module=module,
            description=description,
            view_func=func.__name__,
            view_class=view_class_name,
            methods=set(methods) if methods else set(),
            roles=list(roles) if roles else [],
        )
        PermissionRegistry.register(perm_code, entry)

        @functools.wraps(func)
        def wrapper(self, request, *args, **kwargs):
            if getattr(request.user, "is_superuser", False):
                return func(self, request, *args, **kwargs)
            if not _check_user_perm(request.user, perm_code):
                raise PermissionDenied(f"Missing permission: {perm_code}")
            return func(self, request, *args, **kwargs)

        wrapper._perm_code = perm_code
        wrapper._perm_name = name or perm_code
        wrapper._perm_module = module
        wrapper._perm_roles = list(roles) if roles else []
        return wrapper

    return decorator


def _check_user_perm(user, perm_code: str) -> bool:
    """
    Check if user has the specified permission code

    Supports two permission tables:
    1. RolePermission (new table, directly links roles and permission codes)
    2. RoleMenuButtonPermission (legacy table, linked via menu buttons)
    """
    if not user or not user.is_authenticated:
        return False

    if getattr(user, "is_superuser", False):
        return True

    role_ids = list(user.role.values_list("id", flat=True))
    if not role_ids:
        return False

    # Check new permission table first
    from dvadmin.system.models import RolePermission

    has_new = RolePermission.objects.filter(
        role__id__in=role_ids, permission__code=perm_code, permission__status=True
    ).exists()
    if has_new:
        return True

    # Backward compatible with legacy permission table
    from dvadmin.system.models import RoleMenuButtonPermission

    has_old = RoleMenuButtonPermission.objects.filter(
        role__id__in=role_ids, menu_button__value=perm_code
    ).exists()
    return has_old


def require_viewset_perms(
    list: Optional[str] = None,
    retrieve: Optional[str] = None,
    create: Optional[str] = None,
    update: Optional[str] = None,
    destroy: Optional[str] = None,
    partial_update: Optional[str] = None,
    module: str = "",
    roles: Optional[List[str]] = None,
    **extra: str,
):
    """
    Class-level ViewSet permission decorator

    Usage:
        @require_viewset_perms(
            list='user:list',
            create='user:create',
            update='user:update',
            destroy='user:delete',
            module='user',
            roles=['admin', 'operator'],
        )
        class UserViewSet(CustomModelViewSet):
            ...
    """

    def decorator(cls):
        config = {
            "module": module,
        }
        if list:
            config["list"] = list
        if retrieve:
            config["retrieve"] = retrieve
        if create:
            config["create"] = create
        if update:
            config["update"] = update
        if destroy:
            config["destroy"] = destroy
        if partial_update:
            config["partial_update"] = partial_update
        for k, v in extra.items():
            config[k] = v

        cls._viewset_perms = config

        # Wrap permission check for each action
        names = {
            "list": "List query",
            "retrieve": "Detail query",
            "create": "Create",
            "update": "Update",
            "destroy": "Delete",
            "partial_update": "Partial update",
        }
        method_map = {
            "list": ["GET"],
            "retrieve": ["GET"],
            "create": ["POST"],
            "update": ["PUT", "PATCH"],
            "destroy": ["DELETE"],
            "partial_update": ["PATCH", "PUT"],
        }

        for action_name, perm_code in config.items():
            if action_name == "module":
                continue
            if hasattr(cls, action_name):
                original = getattr(cls, action_name)
                if callable(original) and not getattr(original, "_perm_code", None):
                    wrapped = require_perm(
                        perm_code=perm_code,
                        name=names.get(action_name, perm_code),
                        module=module,
                        methods=method_map.get(action_name),
                        roles=roles,
                    )(original)
                    setattr(cls, action_name, wrapped)
                    PermissionRegistry.bind_view_perm(cls.__name__, action_name, perm_code)
        return cls

    return decorator


def get_user_permission_codes(user) -> List[str]:
    """Get all user permission codes (merges new and legacy permission tables)"""
    if not user or not user.is_authenticated:
        return []

    if getattr(user, "is_superuser", False):
        from dvadmin.system.models import PermissionCode

        perms = list(PermissionCode.objects.filter(status=True).values_list("code", flat=True))
    else:
        role_ids = list(user.role.values_list("id", flat=True))
        if not role_ids:
            return []

        from dvadmin.system.models import RoleMenuButtonPermission, RolePermission

        new_perms = set(
            RolePermission.objects.filter(
                role__id__in=role_ids, permission__status=True
            ).values_list("permission__code", flat=True).distinct()
        )
        old_perms = set(
            RoleMenuButtonPermission.objects.filter(
                role__id__in=role_ids
            ).values_list("menu_button__value", flat=True).distinct()
        )
        perms = list(new_perms | old_perms)

    return list(expand_aliases(set(perms)))


_LEGACY_ALIAS_TABLE = [
    ("create", "Create"),
    ("update", "Update"),
    ("destroy", "Delete"),
    ("list", "Search"),
    ("retrieve", "Retrieve"),
    ("partial_update", "Update"),
    ("update", "Permission"),
    ("update", "Edit"),
    ("destroy", "Del"),
    ("destroy", "Remove"),
]


def _expand_one_alias(code: str) -> set:
    """Generate backward-compatible aliases for a single permission code.

    Rules:
      - user:create  ->  user:Create
      - user:update  ->  user:Update
      - user:destroy ->  user:Delete
      - user:list    ->  user:Search
      - user:retrieve -> user:Retrieve
      - Preserve capitalized module name variants (Host:..., ProgramInstallTemplate:...)
    """
    result = {code}
    if ":" in code:
        mod, act = code.split(":", 1)
        for std, legacy in _LEGACY_ALIAS_TABLE:
            if act == std:
                result.add(f"{mod}:{legacy}")
        result.add(f"{mod[0].upper()}{mod[1:]}:{act[0].upper()}{act[1:]}" if act else code)
    return result


def expand_aliases(codes: set) -> set:
    """Expand aliases for the entire permission code set (used for frontend auth() legacy naming compatibility)"""
    result = set(codes)
    for c in codes:
        result |= _expand_one_alias(c)
    return result