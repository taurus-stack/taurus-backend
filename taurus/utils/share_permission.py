# -*- coding: utf-8 -*-
"""
Shared permission core utility module

Functionality:
1. SharePermissionChecker - computes user's "effective permission set" for a resource
   (merges owner/public/direct share <User+Role+Dept>/link session four levels of sources)
2. ShareLinkService - link share activation, validation, access counting, session management
3. ShareVisibleQS - QueryFilter helper (adds "shared with me" visibility condition in get_queryset)
4. require_share_perm - decorator: ViewSet action-level permission check

Usage examples:
```python
from taurus.utils.share_permission import SharePermissionChecker, require_share_perm

# 1. Functional check
if not SharePermissionChecker.has_perm(request.user, 'script:edit_content', 'script', script_obj):
    raise PermissionDenied("No permission to edit script content")

# 2. Get user all permissions (for frontend button visibility)
my_perms = SharePermissionChecker.get_user_effective_perms(request.user, 'script', script_obj, request=request)

# 3. Decorator (for ViewSet actions)
class ScriptViewSet(CustomModelViewSet):
    @action(detail=True, methods=['post'])
    @require_share_perm('script:rollback_version')
    def rollback(self, request, pk=None):
        ...
```
"""
from __future__ import annotations

import secrets
from datetime import datetime
from functools import wraps
from typing import Dict, Iterable, List, Optional, Set, Tuple, TYPE_CHECKING, Union

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils import timezone

from dvadmin.system.models import Dept

# AvoidCircular import
if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser
    from rest_framework.request import Request


# ============================================================
# 1. Permission constant definitions (consistent with SharePermissionDef table, synced to library via init_share_perms command)
# ============================================================

ALL_SCRIPT_PERMS: Set[str] = {
    # view
    'script:view', 'script:view_version', 'script:view_audit', 'script:view_exec_history',
    # edit
    'script:edit', 'script:edit_content', 'script:edit_config', 'script:manage_version',
    # execute
    'script:trial_run', 'script:execute', 'script:create_task',
    # manage
    'script:copy', 'script:export', 'script:toggle_status',
    'script:archive', 'script:delete', 'script:submit_approve', 'script:manage_share',
}

ALL_WORKFLOW_PERMS: Set[str] = {
    # view
    'workflow:view', 'workflow:view_version', 'workflow:view_approval',
    'workflow:view_exec_history', 'workflow:view_risk',
    # edit
    'workflow:edit', 'workflow:edit_graph', 'workflow:edit_steps',
    'workflow:edit_hosts', 'workflow:publish', 'workflow:rollback',
    # execute
    'workflow:trial_run', 'workflow:execute', 'workflow:create_schedule', 'workflow:cancel_exec',
    # manage
    'workflow:copy', 'workflow:export', 'workflow:toggle_status',
    'workflow:delete', 'workflow:submit_approve', 'workflow:manage_share',
}

ALL_PERMS_BY_RESOURCE: Dict[str, Set[str]] = {
    'script': ALL_SCRIPT_PERMS,
    'workflow': ALL_WORKFLOW_PERMS,
}

# Default permissions for public resources (auth_type='Public')
# Public script: all logged-in users can view/execute/view versions/audit/execution history/dry run/save as copy (official scripts shared publicly for everyone to use & reuse)
DEFAULT_PUBLIC_SCRIPT_PERMS: Set[str] = {
    # view class
    'script:view', 'script:view_version', 'script:view_audit', 'script:view_exec_history',
    # execute class: dry run + full execution
    'script:trial_run', 'script:execute',
    # copy class: save as / copy own script
    'script:copy',
}
# Public workflow: all logged-in users can view/view version/approval history/execution history/risk assessment/dry run + copy for reuse
# Note: full execution (execute), export, edit/release/start-stop and other sensitive operations require explicit share authorization to avoid mis-execution
DEFAULT_PUBLIC_WORKFLOW_PERMS: Set[str] = {
    # view class
    'workflow:view', 'workflow:view_version', 'workflow:view_exec_history',
    'workflow:view_risk', 'workflow:view_approval',
    # execute class: dry run only (full execution requires explicit authorization, prevent public workflows from being mistakenly triggered to run production tasks)
    'workflow:trial_run',
    # copy class: copy own workflow (convenient for public template reuse)
    'workflow:copy',
}
DEFAULT_PUBLIC_PERMS_BY_RESOURCE: Dict[str, Set[str]] = {
    'script': DEFAULT_PUBLIC_SCRIPT_PERMS,
    'workflow': DEFAULT_PUBLIC_WORKFLOW_PERMS,
}

# Quick templates (consistent with frontend TEMPLATES, can be used for backend quick checks/defaults)
PERM_TEMPLATES: Dict[str, Dict[str, Optional[Set[str]]]] = {
    'script': {
        'view_only': {
            'script:view', 'script:view_version', 'script:view_exec_history',
        },
        'view_exec': {
            'script:view', 'script:view_version', 'script:view_exec_history',
            'script:trial_run', 'script:execute',
        },
        'editable': {
            'script:view', 'script:view_version', 'script:view_exec_history',
            'script:edit', 'script:edit_content', 'script:edit_config',
            'script:manage_version', 'script:trial_run', 'script:execute',
            'script:copy', 'script:export',
        },
        'full_manage': None,  # None = ALL_PERMS
    },
    'workflow': {
        'view_only': {
            'workflow:view', 'workflow:view_version', 'workflow:view_exec_history',
        },
        'view_exec': {
            'workflow:view', 'workflow:view_version', 'workflow:view_exec_history',
            'workflow:trial_run', 'workflow:execute',
        },
        'editable': {
            'workflow:view', 'workflow:view_version', 'workflow:view_exec_history',
            'workflow:edit', 'workflow:edit_graph', 'workflow:edit_steps',
            'workflow:edit_hosts', 'workflow:publish', 'workflow:rollback',
            'workflow:trial_run', 'workflow:execute', 'workflow:cancel_exec',
            'workflow:copy', 'workflow:export',
        },
        'full_manage': None,
    },
}


# Key for storing link share activation tokens in session
SHARE_LINK_SESSION_KEY = '_share_link_active_tokens'


# ============================================================
# 2. Core: permission computation
# ============================================================
class SharePermissionChecker:
    """Shared permission checker utility (purely functional, stateless)."""

    # ---------------- public API ----------------
    @classmethod
    def get_user_effective_perms(
        cls,
        user,
        resource_type: str,
        resource_obj,
        request: Optional["Request"] = None,
    ) -> Set[str]:
        """
        Get user's **effective permission code set** for a resource

        Permission sources are merged by priority:
          1. Super admin / resource creator → all permissions
          2. Resource is public (auth_type='Public') → merge default public permissions
          3. Direct share (user level / role level / dept level) → merge corresponding permissions
          4. Link share session (activated link in request.session) → merge link permissions

        :param user: Logged-in user object (pass AnonymousUser if not logged in)
        :param resource_type: 'script' | 'workflow'
        :param resource_obj: Script / Workflow instance
        :param request: DRF request object (for fetching activated share links in session; can be None)
        :return: Set of perm codes, e.g. {'script:view', 'script:edit'}
        """
        resource_type = resource_type.lower()
        if resource_type not in ALL_PERMS_BY_RESOURCE:
            raise ValueError(f"Unknown resource_type={resource_type}")
        all_perms = ALL_PERMS_BY_RESOURCE[resource_type]

        # 1) Not logged-in user: only public permissions + link-shared anyone-scope permissions
        if not user or (hasattr(user, 'is_authenticated') and not user.is_authenticated):
            perms: Set[str] = set()
            if getattr(resource_obj, 'auth_type', None) == 'public':
                perms.update(DEFAULT_PUBLIC_PERMS_BY_RESOURCE.get(resource_type, set()))
            perms.update(cls._get_session_share_link_perms(request, resource_type, resource_obj))
            return perms & all_perms

        # 2) Super admin or creator → full permissions
        if getattr(user, 'is_superuser', False):
            return set(all_perms)
        creator_id = getattr(resource_obj, 'creator_id', None)
        if creator_id and str(creator_id) == str(getattr(user, 'id', '')):
            return set(all_perms)

        perms = set()

        # 3) Public resource default permissions
        if getattr(resource_obj, 'auth_type', None) == 'public':
            perms.update(DEFAULT_PUBLIC_PERMS_BY_RESOURCE.get(resource_type, set()))

        # 4) Direct share
        perms.update(cls._get_direct_share_perms(user, resource_type, resource_obj))

        # 5) Link share (session-activated)
        perms.update(cls._get_session_share_link_perms(request, resource_type, resource_obj))

        # Filter: only keep valid permission codes
        return perms & all_perms

    @classmethod
    def has_perm(
        cls,
        user,
        resource_type: str,
        resource_obj,
        perm_code: str,
        request: Optional["Request"] = None,
    ) -> bool:
        """Check whether user has a single permission"""
        perms = cls.get_user_effective_perms(user, resource_type, resource_obj, request=request)
        return perm_code in perms

    @classmethod
    def has_all_perms(
        cls, user, resource_type: str, resource_obj, perm_codes: Iterable[str], request=None
    ) -> bool:
        perms = cls.get_user_effective_perms(user, resource_type, resource_obj, request=request)
        return all(p in perms for p in perm_codes)

    @classmethod
    def has_any_perm(
        cls, user, resource_type: str, resource_obj, perm_codes: Iterable[str], request=None
    ) -> bool:
        perms = cls.get_user_effective_perms(user, resource_type, resource_obj, request=request)
        return any(p in perms for p in perm_codes)

    # ---------------- internal implementation ----------------

    @classmethod
    def _get_direct_share_perms(cls, user, resource_type: str, resource_obj) -> Set[str]:
        """Get permissions from "direct share" sources (user/role/dept three levels)"""
        from taurus.models import ScriptSharePermission, WorkflowSharePermission

        ModelClass = ScriptSharePermission if resource_type == 'script' else WorkflowSharePermission
        resource_fk = 'script_id' if resource_type == 'script' else 'workflow_id'
        resource_id = resource_obj.id

        now = timezone.now()
        perms: Set[str] = set()

        # 4.1 User level (exact match on user.id)
        user_id = str(getattr(user, 'id', ''))
        user_shares = ModelClass.objects.filter(
            **{resource_fk: resource_id},
            subject_type='user',
            subject_id=user_id,
        ).filter(
            Q(expire_time__isnull=True) | Q(expire_time__gte=now)
        ).only('permissions')
        for s in user_shares:
            perms.update(s.permissions or [])

        # 4.2 Role level (any of user's roles matches)
        role_ids: List[str] = []
        if hasattr(user, 'role'):
            try:
                role_ids = [str(r.id) for r in user.role.all()]
            except Exception:
                pass
        if role_ids:
            role_shares = ModelClass.objects.filter(
                **{resource_fk: resource_id},
                subject_type='role',
                subject_id__in=role_ids,
            ).filter(
                Q(expire_time__isnull=True) | Q(expire_time__gte=now)
            ).only('permissions')
            for s in role_shares:
                perms.update(s.permissions or [])

        # 4.3 Dept level (user's dept is within the shared dept and its child dept tree)
        user_dept_id = getattr(user, 'dept_id', None)
        if user_dept_id is not None:
            try:
                dept_shares = ModelClass.objects.filter(
                    **{resource_fk: resource_id},
                    subject_type='dept',
                ).filter(
                    Q(expire_time__isnull=True) | Q(expire_time__gte=now)
                ).only('subject_id', 'permissions')

                for s in dept_shares:
                    target_dept_id = int(s.subject_id)
                    # Recursive: user's dept is considered matched if within "shared dept and its sub-depts"
                    dept_and_children = Dept.recursion_all_dept(target_dept_id)
                    if int(user_dept_id) in dept_and_children:
                        perms.update(s.permissions or [])
            except Exception:
                pass

        return perms

    @classmethod
    def _get_session_share_link_perms(
        cls, request: Optional["Request"], resource_type: str, resource_obj
    ) -> Set[str]:
        """Extract activated share links from request.session, match to current resource and return its permissions"""
        from taurus.models import ShareLink

        perms: Set[str] = set()
        if not request or not hasattr(request, 'session'):
            return perms

        active_tokens: List[str] = request.session.get(SHARE_LINK_SESSION_KEY, []) or []
        if not active_tokens:
            return perms

        now = timezone.now()
        resource_id = str(resource_obj.id)

        # Batch query to match tokens (avoid N+1)
        links = ShareLink.objects.filter(
            share_token__in=active_tokens,
            is_active=True,
            resource_type=resource_type,
        ).filter(
            Q(expire_time__isnull=True) | Q(expire_time__gte=now)
        ).only('resource_id', 'permissions', 'max_access_count', 'current_access_count')

        for link in links:
            if str(link.resource_id) != resource_id:
                continue
            # Access count limit (0 = no limit)
            if link.max_access_count > 0 and link.current_access_count >= link.max_access_count:
                continue
            perms.update(link.permissions or [])

        return perms


# ============================================================
# 3. Link share service
# ============================================================
class ShareLinkService:
    """Link share activation, validation, revocation, access logging"""

    # ---- Utilities ----
    @staticmethod
    def get_client_ip(request) -> str:
        """Extract client IP from request (compatible with X-Forwarded-For multi-level proxy)"""
        try:
            meta = getattr(request, 'META', None) or {}
            xff = meta.get('HTTP_X_FORWARDED_FOR') or ''
            if xff:
                ip = xff.split(',')[0].strip()
            else:
                ip = meta.get('HTTP_X_REAL_IP') or meta.get('REMOTE_ADDR') or ''
            return (ip or '')[:64]
        except Exception:
            return ''

    # ---- Generate ----
    @staticmethod
    def generate_share_token(length: int = 24) -> str:
        """Generate URL-safe random share token"""
        return secrets.token_urlsafe(length)

    # ---- Activate (called after user clicks link, writes session + counts + logs) ----
    @classmethod
    def activate_link(
        cls,
        share_token: str,
        request: Optional["Request"],
        client_ip: str = '',
        user_agent: str = '',
    ) -> Dict:
        """
        Activate share link (validation + write session + count + log)

        Returns: dict { success: bool, resource_type, resource_id, permissions, reason? }
        """
        from taurus.models import ShareLink, ShareLinkAccessLog

        now = timezone.now()

        try:
            link: "ShareLink" = ShareLink.objects.select_related('create_user').get(share_token=share_token)
        except ShareLink.DoesNotExist:
            cls._log_activate(None, request, client_ip, user_agent, False, 'Link not found')
            return {'success': False, 'reason': 'Share link does not exist or has been deleted'}

        user = getattr(request, 'user', None) if request else None

        # ---- Basic validity check ----
        if not link.is_active:
            cls._log_activate(link, request, client_ip, user_agent, False, 'Link manually revoked by creator')
            return {'success': False, 'reason': 'Share link has been revoked'}
        if link.expire_time and link.expire_time < now:
            cls._log_activate(link, request, client_ip, user_agent, False, 'Link expired')
            return {'success': False, 'reason': 'Share link has expired'}
        if link.max_access_count > 0 and link.current_access_count >= link.max_access_count:
            cls._log_activate(link, request, client_ip, user_agent, False, 'Max access count exceeded')
            return {'success': False, 'reason': 'Share link access count has been exhausted'}

        # ---- Access scope check ----
        is_authed = user and getattr(user, 'is_authenticated', False)
        if link.access_scope == 'authenticated' and not is_authed:
            cls._log_activate(link, request, client_ip, user_agent, False, 'Login required')
            return {'success': False, 'reason': 'This share link is only accessible to logged-in users, please login first'}

        # ---- Optional binding subject check ----
        if link.bind_subject_type and link.bind_subject_id:
            if not cls._check_bind_subject_match(user, link.bind_subject_type, link.bind_subject_id):
                cls._log_activate(link, request, client_ip, user_agent, False, 'Visitor not in bound scope')
                return {'success': False, 'reason': 'This share link is restricted to specified users/departments/roles'}

        # ---- Success: write session + count + log ----
        if request and hasattr(request, 'session'):
            tokens: List[str] = request.session.get(SHARE_LINK_SESSION_KEY, []) or []
            if link.share_token not in tokens:
                tokens.append(link.share_token)
                request.session[SHARE_LINK_SESSION_KEY] = tokens

        # Access count +1
        link.current_access_count += 1
        link.save(update_fields=['current_access_count'])

        # Access log
        access_user = user if (is_authed and hasattr(user, 'id')) else None
        ShareLinkAccessLog.objects.create(
            share_link=link,
            access_user=access_user,
            client_ip=client_ip,
            user_agent=(user_agent or '')[:500],
            access_success=True,
            fail_reason='',
        )

        # Query resource name (for activation success display & friendly redirect)
        from taurus.models import Script, Workflow
        resource_name = None
        try:
            if link.resource_type == 'script':
                resource_name = Script.objects.filter(id=link.resource_id).values_list('name', flat=True).first()
            elif link.resource_type == 'workflow':
                resource_name = Workflow.objects.filter(id=link.resource_id).values_list('name', flat=True).first()
        except Exception:
            pass

        return {
            'success': True,
            'resource_type': link.resource_type,
            'resource_id': link.resource_id,
            'resource_name': resource_name,
            'permissions': list(link.permissions or []),
            'access_scope': link.access_scope,
            'expire_time': link.expire_time.isoformat() if link.expire_time else None,
            'max_access_count': link.max_access_count,
            'current_access_count': link.current_access_count,
        }

    # ---- Revoke ----
    @staticmethod
    def revoke_link(link_id: int, operator_user=None) -> bool:
        """Manually revoke share link (set is_active=False)"""
        from taurus.models import ShareLink

        try:
            link = ShareLink.objects.get(id=link_id)
        except ShareLink.DoesNotExist:
            return False
        link.is_active = False
        update_fields = ['is_active']
        if operator_user:
            # Optionally record the revoker in the remark
            link.remark = (link.remark or '') + f"\n[Revoked] by {getattr(operator_user, 'username', '')} {timezone.now():%Y-%m-%d %H:%M}"
            update_fields.append('remark')
        link.save(update_fields=update_fields)
        return True

    # ---- Deregister a token from session ----
    @staticmethod
    def logout_token(request: "Request", share_token: str) -> None:
        if not hasattr(request, 'session'):
            return
        tokens: List[str] = request.session.get(SHARE_LINK_SESSION_KEY, []) or []
        if share_token in tokens:
            tokens.remove(share_token)
            request.session[SHARE_LINK_SESSION_KEY] = tokens

    # ---------------- internal helpers ----------------
    @classmethod
    def _log_activate(
        cls,
        link,
        request: Optional["Request"],
        client_ip: str,
        user_agent: str,
        success: bool,
        reason: str,
    ) -> None:
        from taurus.models import ShareLinkAccessLog

        if link is None:
            return
        user = getattr(request, 'user', None) if request else None
        access_user = None
        if user and getattr(user, 'is_authenticated', False) and hasattr(user, 'id'):
            access_user = user
        try:
            ShareLinkAccessLog.objects.create(
                share_link=link,
                access_user=access_user,
                client_ip=client_ip,
                user_agent=(user_agent or '')[:500],
                access_success=success,
                fail_reason=reason or '',
            )
        except Exception:
            pass

    @classmethod
    def _check_bind_subject_match(cls, user, bind_type: str, bind_id: str) -> bool:
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        bind_id = str(bind_id)
        if bind_type == 'user':
            return str(getattr(user, 'id', '')) == bind_id
        if bind_type == 'role':
            try:
                return any(str(r.id) == bind_id for r in user.role.all())
            except Exception:
                return False
        if bind_type == 'dept':
            try:
                user_dept_id = getattr(user, 'dept_id', None)
                if user_dept_id is None:
                    return False
                dept_and_children = Dept.recursion_all_dept(int(bind_id))
                return int(user_dept_id) in dept_and_children
            except Exception:
                return False
        return False


# ============================================================
# 4. Query visibility filter helper (for get_queryset)
# ============================================================
class ShareVisibleQS:
    """
    Used in ViewSet.get_queryset(): restrict list to only return resources that current user "has permission to see"

    Visibility condition = (creator == me) OR (public) OR (directly shared with me/my role/my dept) OR (activated share link matches in session)
    """

    @staticmethod
    def filter_visible_scripts(queryset, user, request=None, include_shared=False):
        """Script query filter: only return scripts visible to current user

        Args:
            include_shared: whether to include "shared with me" (direct permission share + link share) scripts
                - False (default): script library browsing scenario, only return (my created) OR (public)
                - True: full visibility, includes scripts shared with me
        """
        from taurus.models import ScriptSharePermission, ShareLink

        if getattr(user, 'is_superuser', False):
            return queryset.distinct()

        q_visible = Q()

        # My created
        if hasattr(user, 'id'):
            q_visible |= Q(creator_id=user.id)

        # Public
        q_visible |= Q(auth_type='public')

        if include_shared:
            # Directly shared with me (user/role/dept)
            now = timezone.now()
            now_cond = Q(expire_time__isnull=True) | Q(expire_time__gte=now)

            shared_script_ids: Set[int] = set()
            # User level
            if hasattr(user, 'id'):
                user_ids = ScriptSharePermission.objects.filter(
                    subject_type='user', subject_id=str(user.id)
                ).filter(now_cond).values_list('script_id', flat=True)
                for sid in user_ids:
                    try:
                        shared_script_ids.add(int(sid))
                    except (ValueError, TypeError):
                        pass

            # Role level
            try:
                role_ids = [str(r.id) for r in user.role.all()] if hasattr(user, 'role') else []
                if role_ids:
                    role_script_ids = ScriptSharePermission.objects.filter(
                        subject_type='role', subject_id__in=role_ids
                    ).filter(now_cond).values_list('script_id', flat=True)
                    for sid in role_script_ids:
                        try:
                            shared_script_ids.add(int(sid))
                        except (ValueError, TypeError):
                            pass
            except Exception:
                pass

            # Dept level
            user_dept_id = getattr(user, 'dept_id', None)
            if user_dept_id is not None:
                try:
                    dept_shares = ScriptSharePermission.objects.filter(
                        subject_type='dept',
                    ).filter(now_cond).values_list('subject_id', 'script_id')
                    for subj_dept_id, script_id in dept_shares:
                        try:
                            dept_and_children = Dept.recursion_all_dept(int(subj_dept_id))
                            if int(user_dept_id) in dept_and_children:
                                try:
                                    shared_script_ids.add(int(script_id))
                                except (ValueError, TypeError):
                                    pass
                        except Exception:
                            pass
                except Exception:
                    pass

            if shared_script_ids:
                q_visible |= Q(id__in=shared_script_ids)

            # Link share (resources corresponding to activated tokens in session are also visible)
            if request is not None and hasattr(request, 'session'):
                active_tokens = list(request.session.get('_share_link_active_tokens') or [])
                if active_tokens:
                    linked = ShareLink.objects.filter(
                        share_token__in=active_tokens,
                        resource_type='script',
                        is_active=True,
                    ).filter(now_cond).values_list('resource_id', 'max_access_count', 'current_access_count')
                    link_ids: Set[int] = set()
                    for rid, max_cnt, cur_cnt in linked:
                        if max_cnt and max_cnt > 0 and cur_cnt and cur_cnt >= max_cnt:
                            continue
                        try:
                            link_ids.add(int(rid))
                        except (ValueError, TypeError):
                            pass
                    if link_ids:
                        q_visible |= Q(id__in=link_ids)

        return queryset.filter(q_visible).distinct()

    @staticmethod
    def _shared_to_user_scripts_q(user, request=None):
        """Only return Q conditions for "directly shared with me (user/role/dept) OR activated link"
        Does NOT include (my created / public), used for frontend tab "shared with me" filter
        """
        from taurus.models import ScriptSharePermission, ShareLink
        from django.db.models import Q

        q = Q(pk__in=[])  # Empty condition, placeholder

        if not user or not getattr(user, 'is_authenticated', False):
            return q

        now = timezone.now()
        now_cond = Q(expire_time__isnull=True) | Q(expire_time__gte=now)
        shared_script_ids: Set[int] = set()

        # User level
        if hasattr(user, 'id'):
            uids = ScriptSharePermission.objects.filter(
                subject_type='user', subject_id=str(user.id)
            ).filter(now_cond).values_list('script_id', flat=True)
            for sid in uids:
                try:
                    shared_script_ids.add(int(sid))
                except (ValueError, TypeError):
                    pass

        # Role level
        try:
            role_ids = [str(r.id) for r in user.role.all()] if hasattr(user, 'role') else []
            if role_ids:
                rids = ScriptSharePermission.objects.filter(
                    subject_type='role', subject_id__in=role_ids
                ).filter(now_cond).values_list('script_id', flat=True)
                for sid in rids:
                    try:
                        shared_script_ids.add(int(sid))
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass

        # Dept level
        user_dept_id = getattr(user, 'dept_id', None)
        if user_dept_id is not None:
            try:
                dept_shares = ScriptSharePermission.objects.filter(
                    subject_type='dept',
                ).filter(now_cond).values_list('subject_id', 'script_id')
                for subj_dept_id, script_id in dept_shares:
                    try:
                        dept_and_children = Dept.recursion_all_dept(int(subj_dept_id))
                        if int(user_dept_id) in dept_and_children:
                            try:
                                shared_script_ids.add(int(script_id))
                            except (ValueError, TypeError):
                                pass
                    except Exception:
                        pass
            except Exception:
                pass

        # Link share activation (activated tokens in session)
        if request is not None and hasattr(request, 'session'):
            active_tokens = list(request.session.get('_share_link_active_tokens') or [])
            if active_tokens:
                linked = ShareLink.objects.filter(
                    share_token__in=active_tokens,
                    resource_type='script',
                    is_active=True,
                ).filter(now_cond).values_list('resource_id', 'max_access_count', 'current_access_count')
                for rid, max_cnt, cur_cnt in linked:
                    # Skip those exceeding access count limit
                    if max_cnt and max_cnt > 0 and cur_cnt and cur_cnt >= max_cnt:
                        continue
                    try:
                        shared_script_ids.add(int(rid))
                    except (ValueError, TypeError):
                        pass

        if shared_script_ids:
            return Q(id__in=shared_script_ids)
        return q

    @staticmethod
    def _shared_to_user_workflows_q(user, request=None):
        """Only return Q conditions for "directly shared with me (user/role/dept) OR activated link"
        Does NOT include (my created / public), used for frontend tab "shared with me" filter
        """
        from taurus.models import WorkflowSharePermission, ShareLink
        from django.db.models import Q

        q = Q(pk__in=[])  # Empty condition, placeholder

        if not user or not getattr(user, 'is_authenticated', False):
            return q

        now = timezone.now()
        now_cond = Q(expire_time__isnull=True) | Q(expire_time__gte=now)
        shared_wf_ids: Set[int] = set()

        # User level
        if hasattr(user, 'id'):
            uids = WorkflowSharePermission.objects.filter(
                subject_type='user', subject_id=str(user.id)
            ).filter(now_cond).values_list('workflow_id', flat=True)
            for wid in uids:
                try:
                    shared_wf_ids.add(int(wid))
                except (ValueError, TypeError):
                    pass

        # Role level
        try:
            role_ids = [str(r.id) for r in user.role.all()] if hasattr(user, 'role') else []
            if role_ids:
                rids = WorkflowSharePermission.objects.filter(
                    subject_type='role', subject_id__in=role_ids
                ).filter(now_cond).values_list('workflow_id', flat=True)
                for wid in rids:
                    try:
                        shared_wf_ids.add(int(wid))
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass

        # Dept level
        user_dept_id = getattr(user, 'dept_id', None)
        if user_dept_id is not None:
            try:
                dept_shares = WorkflowSharePermission.objects.filter(
                    subject_type='dept',
                ).filter(now_cond).values_list('subject_id', 'workflow_id')
                for subj_dept_id, wf_id in dept_shares:
                    try:
                        dept_and_children = Dept.recursion_all_dept(int(subj_dept_id))
                        if int(user_dept_id) in dept_and_children:
                            try:
                                shared_wf_ids.add(int(wf_id))
                            except (ValueError, TypeError):
                                pass
                    except Exception:
                        pass
            except Exception:
                pass

        # Link share activation (activated tokens in session)
        if request is not None and hasattr(request, 'session'):
            active_tokens = list(request.session.get('_share_link_active_tokens') or [])
            if active_tokens:
                linked = ShareLink.objects.filter(
                    share_token__in=active_tokens,
                    resource_type='workflow',
                    is_active=True,
                ).filter(now_cond).values_list('resource_id', 'max_access_count', 'current_access_count')
                for rid, max_cnt, cur_cnt in linked:
                    # Skip those exceeding access count limit
                    if max_cnt and max_cnt > 0 and cur_cnt and cur_cnt >= max_cnt:
                        continue
                    try:
                        shared_wf_ids.add(int(rid))
                    except (ValueError, TypeError):
                        pass

        if shared_wf_ids:
            return Q(id__in=shared_wf_ids)
        return q

    @staticmethod
    def filter_visible_workflows(queryset, user, request=None, include_shared=True):
        """Workflow query filter: only return workflows visible to current user

        Args:
            include_shared: whether to include "shared with me" (direct permission share + link share) workflows
                - True (default, backward compatible): full visibility, includes workflows shared with me
                - False: workflow library browsing scenario, only return (my created) OR (public)
        """
        from taurus.models import WorkflowSharePermission, ShareLink

        if getattr(user, 'is_superuser', False):
            return queryset.distinct()

        q_visible = Q()

        if hasattr(user, 'id'):
            q_visible |= Q(creator_id=user.id)
        q_visible |= Q(auth_type='public')

        if include_shared:
            now = timezone.now()
            now_cond = Q(expire_time__isnull=True) | Q(expire_time__gte=now)

            shared_wf_ids: Set[int] = set()

            if hasattr(user, 'id'):
                user_ids = WorkflowSharePermission.objects.filter(
                    subject_type='user', subject_id=str(user.id)
                ).filter(now_cond).values_list('workflow_id', flat=True)
                for wid in user_ids:
                    try:
                        shared_wf_ids.add(int(wid))
                    except (ValueError, TypeError):
                        pass

            try:
                role_ids = [str(r.id) for r in user.role.all()] if hasattr(user, 'role') else []
                if role_ids:
                    role_wf_ids = WorkflowSharePermission.objects.filter(
                        subject_type='role', subject_id__in=role_ids
                    ).filter(now_cond).values_list('workflow_id', flat=True)
                    for wid in role_wf_ids:
                        try:
                            shared_wf_ids.add(int(wid))
                        except (ValueError, TypeError):
                            pass
            except Exception:
                pass

            user_dept_id = getattr(user, 'dept_id', None)
            if user_dept_id is not None:
                try:
                    dept_shares = WorkflowSharePermission.objects.filter(
                        subject_type='dept',
                    ).filter(now_cond).values_list('subject_id', 'workflow_id')
                    for subj_dept_id, wf_id in dept_shares:
                        try:
                            dept_and_children = Dept.recursion_all_dept(int(subj_dept_id))
                            if int(user_dept_id) in dept_and_children:
                                try:
                                    shared_wf_ids.add(int(wf_id))
                                except (ValueError, TypeError):
                                    pass
                        except Exception:
                            pass
                except Exception:
                    pass

            if shared_wf_ids:
                q_visible |= Q(id__in=shared_wf_ids)

            # Link share (session activated): check is_active + validity period + access count
            if request is not None and hasattr(request, 'session'):
                active_tokens = list(request.session.get('_share_link_active_tokens') or [])
                if active_tokens:
                    linked = ShareLink.objects.filter(
                        share_token__in=active_tokens,
                        resource_type='workflow',
                        is_active=True,
                    ).filter(now_cond).values_list('resource_id', 'max_access_count', 'current_access_count')
                    link_ids: Set[int] = set()
                    for rid, max_cnt, cur_cnt in linked:
                        if max_cnt and max_cnt > 0 and cur_cnt and cur_cnt >= max_cnt:
                            continue
                        try:
                            link_ids.add(int(rid))
                        except (ValueError, TypeError):
                            pass
                    if link_ids:
                        q_visible |= Q(id__in=link_ids)

        return queryset.filter(q_visible).distinct()


# ============================================================
# 5. Decorator: ViewSet action permission check
# ============================================================
def require_share_perm(perm_code: str):
    """
    Decorator: used on DRF ViewSet detail actions to automatically check share permissions

    Usage example:
        class ScriptViewSet(CustomModelViewSet):
            @action(detail=True, methods=['post'])
            @require_share_perm('script:edit_content')
            def rollback(self, request, pk=None):
                ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(self, request, *args, **kwargs):
            resource_obj = self.get_object()
            resource_type = None
            if hasattr(resource_obj, '_meta'):
                model_name = resource_obj._meta.model_name.lower()
                if 'script' in model_name:
                    resource_type = 'script'
                elif 'workflow' in model_name:
                    resource_type = 'workflow'
            if resource_type is None:
                raise ValueError(f"require_share_perm: unrecognized resource type {type(resource_obj)}")

            ok = SharePermissionChecker.has_perm(
                request.user, resource_type, resource_obj, perm_code, request=request
            )
            if not ok:
                raise PermissionDenied(f"Missing share permission: {perm_code}")

            return view_func(self, request, *args, **kwargs)
        return wrapper
    return decorator