"""Share (permission definitions + share links) ViewSets + Service (EE only).
2 ViewSets:
  1. SharePermissionDefViewSet (字典管理 + 分组接口)
  2. ShareLinkViewSet (分享链接管理 + activate + resource_detail + access_logs + my_active_links)
"""
from __future__ import annotations

from django.utils import timezone
from django.utils.decorators import method_decorator
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny

from dvadmin.utils.viewset import CustomModelViewSet
from dvadmin.utils.json_response import (
    SuccessResponse, DetailResponse, ErrorResponse,
)

from taurus.models import (
    SharePermissionDef,
    ShareLink,
    ShareLinkAccessLog,
    Script,
    Workflow,
)
from taurus.editions import require_feature
from taurus.editions.features import F_SCRIPT_SHARING
from taurus.utils.share_permission import ShareLinkService

from taurus_ee.serializers.share_permission import (
    SharePermissionDefSerializer,
    ShareLinkSerializer,
    ShareLinkActivateSerializer,
    ShareLinkAccessLogSerializer,
)
from taurus.serializers import (  # 资源 detail 分享用，保留
    ScriptSerializer,
    WorkflowSerializer,
)


# ---------------------------------------------------------------------------
# 1. Share Permission Definitions (字典)
# ---------------------------------------------------------------------------
@method_decorator(require_feature(F_SCRIPT_SHARING), name='dispatch')
class SharePermissionDefViewSet(CustomModelViewSet):
    """分享权限字典管理（EE 专属）。"""
    queryset = SharePermissionDef.objects.all()
    serializer_class = SharePermissionDefSerializer
    search_fields = ['perm_code', 'perm_name', 'description']
    filterset_fields = ['resource_type', 'category', 'is_active']
    ordering_fields = ['sort', 'id', 'create_datetime']
    ordering = ['resource_type', 'category', 'sort', 'id']

    @action(detail=False, methods=['GET'], url_path='perm_defs')
    def perm_defs(self, request):
        resource_type = request.query_params.get('resource_type')
        qs = SharePermissionDef.objects.filter(is_active=True)
        if resource_type in ('script', 'workflow'):
            qs = qs.filter(resource_type=resource_type)
        qs = qs.order_by('resource_type', 'category', 'sort')
        category_display_map = dict(SharePermissionDef.CATEGORY_CHOICES)
        resource_display_map = dict(SharePermissionDef.RESOURCE_TYPE_CHOICES)
        grouped: dict = {}
        for item in qs:
            rk = item.resource_type
            ck = item.category
            grouped.setdefault(rk, {}).setdefault(ck, []).append(
                SharePermissionDefSerializer(item).data
            )
        data = []
        for rk, cats in grouped.items():
            for ck, perms in cats.items():
                data.append({
                    'resource_type': rk,
                    'resource_type_display': resource_display_map.get(rk, rk),
                    'category': ck,
                    'category_display': category_display_map.get(ck, ck),
                    'perms': perms,
                })
        return SuccessResponse(data=data, msg='Fetched successfully')


# ---------------------------------------------------------------------------
# 2. Share Links
# ---------------------------------------------------------------------------
# ⚠️  设计决策：整个类挂 require_feature(F_SCRIPT_SHARING)（CE 会在 dispatch 前被 EditionGate 403）
#    这样 activate/resource_detail 这两个 public endpoints 也统一由 EditionGate 拦截。
#    原因：CE 用户根本无法创建 share link（创建 action 属于本类），因此 CE 下没可能合法调用
#    这两个 public 接口；若有恶意请求，统一返回 "专属能力 403" 更安全。
# ---------------------------------------------------------------------------
@method_decorator(require_feature(F_SCRIPT_SHARING), name='dispatch')
class ShareLinkViewSet(CustomModelViewSet):
    """分享链接管理：CRUD + activate + revoke + toggle + access_logs（EE 专属）。"""
    queryset = ShareLink.objects.all()
    serializer_class = ShareLinkSerializer
    search_fields = ['share_token', 'resource_id', 'remark']
    filterset_fields = [
        'resource_type', 'access_scope', 'is_active',
        'create_user', 'bind_subject_type',
    ]
    ordering_fields = ['create_datetime', 'update_datetime', 'expire_time', 'current_access_count']
    ordering = ['-create_datetime']

    def get_queryset(self):
        qs = super().get_queryset()
        user = getattr(self.request, 'user', None)
        if user and not getattr(user, 'is_superuser', False):
            qs = qs.filter(create_user=user)
        return qs

    def get_serializer_context(self, *args, **kwargs):
        ctx = super().get_serializer_context(*args, **kwargs)
        qs = getattr(self, 'object_list', None) or self.filter_queryset(self.get_queryset())
        script_ids, workflow_ids = [], []
        for link in qs:
            if link.resource_type == 'script':
                script_ids.append(link.resource_id)
            elif link.resource_type == 'workflow':
                workflow_ids.append(link.resource_id)
        resource_names = {}
        if script_ids:
            for s in Script.objects.filter(id__in=script_ids).values('id', 'name'):
                resource_names[f"script:{s['id']}"] = s['name']
        if workflow_ids:
            for w in Workflow.objects.filter(id__in=workflow_ids).values('id', 'name'):
                resource_names[f"workflow:{w['id']}"] = w['name']
        ctx['resource_names'] = resource_names
        perm_defs = {
            p.perm_code: SharePermissionDefSerializer(p).data
            for p in SharePermissionDef.objects.filter(is_active=True)
        }
        ctx['perm_defs'] = perm_defs
        return ctx

    def perform_create(self, serializer):
        share_token = ShareLinkService.generate_share_token()
        serializer.save(
            create_user=self.request.user,
            share_token=share_token,
            current_access_count=0,
            is_active=True,
        )

    # ---- 公开入口（激活分享链接）----
    @action(detail=False, methods=['POST'], url_path='activate',
            permission_classes=[AllowAny])
    def activate(self, request):
        ser = ShareLinkActivateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        token = ser.validated_data['share_token']

        client_ip = ShareLinkService.get_client_ip(request)
        try:
            ua = request.META.get('HTTP_USER_AGENT', '')[:500]
        except Exception:  # noqa: BLE001
            ua = ''

        result = ShareLinkService.activate_link(
            token, request, client_ip=client_ip, user_agent=ua
        )
        if not result['success']:
            return ErrorResponse(msg=result.get('reason', 'Activation failed'), data=result)

        if hasattr(request, 'session'):
            active = list(request.session.get('_share_link_active_tokens') or [])
            if token not in active:
                active.append(token)
            request.session['_share_link_active_tokens'] = active

        return SuccessResponse(data=result, msg='Link activated successfully')

    # ---- 公开入口（通过 Token 获取资源详情）----
    @action(detail=False, methods=['GET'], url_path='resource-detail',
            permission_classes=[AllowAny])
    def resource_detail(self, request):
        share_token = request.query_params.get('share_token')
        if not share_token:
            return ErrorResponse(msg='Missing share_token parameter')

        now = timezone.now()
        try:
            link: ShareLink = ShareLink.objects.select_related('create_user').get(
                share_token=share_token
            )
        except ShareLink.DoesNotExist:
            return ErrorResponse(msg='Share link not found or deleted')

        if not link.is_active:
            return ErrorResponse(msg='Share link revoked')
        if link.expire_time and link.expire_time < now:
            return ErrorResponse(msg='Share link expired')
        if link.max_access_count > 0 and link.current_access_count >= link.max_access_count:
            return ErrorResponse(msg='Share link access limit reached')

        user = getattr(request, 'user', None)
        is_authed = user and getattr(user, 'is_authenticated', False)

        # ⚠️ 绝对不能用 HTTP 401 或 code=401（前端会强制登出）
        if link.access_scope == 'authenticated' and not is_authed:
            return ErrorResponse(
                msg='This share link requires login, please sign in',
                code=4001,
            )

        if link.bind_subject_type and link.bind_subject_id:
            if not ShareLinkService._check_bind_subject_match(
                user, link.bind_subject_type, link.bind_subject_id
            ):
                return ErrorResponse(
                    msg='This share link is restricted to specific users/depts/roles'
                )

        if hasattr(request, 'session'):
            active = list(request.session.get('_share_link_active_tokens') or [])
            if share_token not in active:
                active.append(share_token)
                request.session['_share_link_active_tokens'] = active

        if link.resource_type == 'script':
            try:
                obj = Script.objects.get(pk=link.resource_id)
            except Script.DoesNotExist:
                return ErrorResponse(msg='Shared script not found or deleted')
            detail = ScriptSerializer(obj).data
        elif link.resource_type == 'workflow':
            try:
                obj = Workflow.objects.get(pk=link.resource_id)
            except Workflow.DoesNotExist:
                return ErrorResponse(msg='Shared workflow not found or deleted')
            detail = WorkflowSerializer(obj).data
        else:
            return ErrorResponse(msg=f'Unknown resource type: {link.resource_type}')

        data = {
            'resource_type': link.resource_type,
            'resource_id': link.resource_id,
            'resource_name': (
                detail.get('name')
                if isinstance(detail, dict) else getattr(detail, 'name', None)
            ),
            'permissions': list(link.permissions or []),
            'detail': detail,
        }
        return SuccessResponse(data=data, msg='Fetched successfully')

    @action(detail=True, methods=['POST'], url_path='toggle-status')
    def toggle_status(self, request, pk=None):
        link = self.get_object()
        user = request.user
        if (not getattr(user, 'is_superuser', False)
                and link.create_user_id is not None
                and str(link.create_user_id) != str(getattr(user, 'pk', None))):
            raise PermissionDenied('Can only manage own created/shared links')
        target = request.data.get('status')
        if target not in ('active', 'revoked'):
            return ErrorResponse(msg='status must be active or revoked')
        link.is_active = (target == 'active')
        link.save()
        return DetailResponse(
            data=ShareLinkSerializer(link).data,
            msg='Status updated successfully',
        )

    @action(detail=True, methods=['POST'], url_path='revoke')
    def revoke(self, request, pk=None):
        link = self.get_object()
        user = request.user
        if (not getattr(user, 'is_superuser', False)
                and link.create_user_id is not None
                and str(link.create_user_id) != str(getattr(user, 'pk', None))):
            raise PermissionDenied('Can only manage own created/shared links')
        ok = ShareLinkService.revoke_link(link.pk, operator_user=request.user)
        if not ok:
            return ErrorResponse(msg='Revocation failed')
        link.refresh_from_db()
        return DetailResponse(
            data=ShareLinkSerializer(link).data,
            msg='Revoked successfully',
        )

    @action(detail=True, methods=['GET'], url_path='access-logs')
    def access_logs(self, request, pk=None):
        link = self.get_object()
        user = request.user
        if (not getattr(user, 'is_superuser', False)
                and link.create_user_id is not None
                and str(link.create_user_id) != str(getattr(user, 'pk', None))):
            raise PermissionDenied('Can only view own link access logs')
        qs = ShareLinkAccessLog.objects.filter(share_link=link).order_by('-create_datetime')
        page = self.paginate_queryset(qs)
        visitor_ids = [
            str(log.access_user_id) for log in qs if log.access_user_id
        ]
        visitor_map = {}
        if visitor_ids:
            from dvadmin.system.models import Users
            for u in Users.objects.filter(id__in=visitor_ids).values('id', 'username', 'name'):
                visitor_map[str(u['id'])] = u.get('name') or u['username']
        ctx = {'visitor_map': visitor_map}
        if page is not None:
            serializer = ShareLinkAccessLogSerializer(page, many=True, context=ctx)
            return self.get_paginated_response(serializer.data)
        serializer = ShareLinkAccessLogSerializer(qs, many=True, context=ctx)
        return SuccessResponse(data=serializer.data, msg='Fetched successfully')

    @action(detail=False, methods=['GET'], url_path='my-active-links')
    def my_active_links(self, request):
        active = []
        if hasattr(request, 'session'):
            active = list(request.session.get('_share_link_active_tokens') or [])
        qs = ShareLink.objects.filter(share_token__in=active)
        resource_names = {}
        script_ids = [l.resource_id for l in qs if l.resource_type == 'script']
        workflow_ids = [l.resource_id for l in qs if l.resource_type == 'workflow']
        for s in Script.objects.filter(id__in=script_ids).values('id', 'name'):
            resource_names[f"script:{s['id']}"] = s['name']
        for w in Workflow.objects.filter(id__in=workflow_ids).values('id', 'name'):
            resource_names[f"workflow:{w['id']}"] = w['name']
        ctx = {
            'resource_names': resource_names,
            'perm_defs': {
                p.perm_code: SharePermissionDefSerializer(p).data
                for p in SharePermissionDef.objects.filter(is_active=True)
            },
        }
        return SuccessResponse(
            data=ShareLinkSerializer(qs, many=True, context=ctx).data,
            msg='Fetched successfully',
        )


# ---------------------------------------------------------------------------
# Service: 供 ScriptViewSet / WorkflowViewSet 共享 3 个 Thin Wrapper actions 调用
# （先 Stub，M2.2 再完整实现）
# ---------------------------------------------------------------------------
class ShareService:
    """统一脚本/工作流 shares/share_detail/effective_perms 3 个动作。"""

    # TODO: M2.1.4 后续实现完整 shares 分页 / 增删改查，以及 effective_perms 渲染；
    # 目前只抛出 None，靠 ee_service_or_403 先拦 CE。
    pass
