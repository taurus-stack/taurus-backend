"""Workflow approval ViewSets (EE-only).

4 ViewSets：
  1. WorkflowApproveViewSet               — 兼容旧前端 WorkflowApprove 中心（用 Model=WorkflowApproveCompatSerializer
                                            输出字段；create 用 WorkflowApproveCreateSerializer → 提交即 start_approval）
  2. WorkflowApprovalRuleViewSet          — 规则定义（update_node_order + test-match action）
  3. WorkflowApprovalRuleNodeViewSet      — 规则节点 CRUD
  4. WorkflowApprovalInstanceViewSet      — 实例（view_type=pending/pending_me/mine + 9 actions: approve/reject/cancel/delegate/add_sign/nodes/stats_count/stats_counts）

所有 ViewSet 外层都在 taurus.views.py 再套一层 Thin Wrapper + Double @require_feature。
"""
from __future__ import annotations

from django.db.models import Count, Q
from django.utils import timezone
from django.utils.decorators import method_decorator
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError

from dvadmin.utils.serializers import CustomModelSerializer
from dvadmin.utils.json_response import SuccessResponse, DetailResponse, ErrorResponse
from dvadmin.utils.viewset import CustomModelViewSet

from taurus.editions.loader import require_feature
from taurus.editions.features import F_WORKFLOW_APPROVAL_FLOW
from taurus.models import (
    WorkflowApprove,
    WorkflowApprovalRule,
    WorkflowApprovalNode,
    WorkflowApprovalInstance,
    WorkflowApprovalNodeExecution,
)
_WFApproveInstModel = WorkflowApprovalInstance
from taurus_ee.serializers.workflow_approval import (
    WorkflowApproveSerializer,
    WorkflowApproveCreateSerializer,
    WorkflowApproveApproveSerializer,
    WorkflowApproveRejectSerializer,
    WorkflowApproveCompatSerializer,
    WorkflowApprovalRuleSerializer,
    WorkflowApprovalNodeSerializer,
    WorkflowApprovalInstanceSerializer,
    WorkflowApprovalNodeExecutionSerializer,
)
from taurus_ee.services.workflow_approval_engine import (
    WorkflowApprovalRuleMatcher,
    WorkflowApprovalFlowEngine,
)


# ---------------------------------------------------------------------------
# 1. WorkflowApproveViewSet（兼容旧中心）
# ---------------------------------------------------------------------------
@method_decorator(require_feature(F_WORKFLOW_APPROVAL_FLOW), name='dispatch')
class WorkflowApproveViewSet(CustomModelViewSet):
    """Legacy WorkflowApprove center（内部切换到新 Model=WorkflowApprovalInstance）。"""

    model: type
    queryset = _WFApproveInstModel.objects.all()
    serializer_class = WorkflowApproveCompatSerializer
    create_serializer_class = WorkflowApproveCreateSerializer

    @property
    def model(self):
        return _WFApproveInstModel

    def get_serializer_class(self):
        if self.action == 'create':
            return self.create_serializer_class
        return self.serializer_class

    def get_queryset(self):
        base = self.model.objects.all().select_related('workflow', 'submitter')
        if getattr(self.request.user, 'is_superuser', False):
            return base.order_by('-create_datetime')
        view_type = self.request.query_params.get('view_type', '')
        if view_type == 'pending':
            base = base.filter(status__in=['pending', 'approving'])
        elif view_type == 'pending_me':
            from django.contrib.auth import get_user_model as _gum
            uid = self.request.user.id
            base = base.filter(
                status__in=['pending', 'approving'],
                node_executions__candidate_approvers__contains=[{"user_id": uid}]
            ).distinct()
        elif view_type == 'mine':
            base = base.filter(submitter_id=self.request.user.id)
        else:
            base = base.filter(Q(submitter_id=self.request.user.id) |
                               Q(node_executions__candidate_approvers__contains=[{"user_id": self.request.user.id}])
                               ).distinct()
        return base.order_by('-create_datetime')

    def perform_create(self, serializer):
        workflow = serializer.validated_data['workflow']
        submit_desc = (serializer.validated_data.get('submit_desc', '')
                       or 'Submit workflow for approval')
        WorkflowApprovalFlowEngine.start_approval(
            workflow=workflow,
            submitter=self.request.user,
            submit_desc=submit_desc,
        )

    @action(detail=True, methods=['post'])
    def approve(self, request, *args, **kwargs):
        sz = WorkflowApproveApproveSerializer(data=request.data)
        sz.is_valid(raise_exception=True)
        try:
            instance = WorkflowApprovalFlowEngine.approve(
                kwargs.get('pk'),
                approver=request.user,
                reason=sz.validated_data.get('approve_reason', ''),
            )
        except ValueError as exc:
            return ErrorResponse(msg=str(exc))
        return DetailResponse(data=WorkflowApproveCompatSerializer(instance).data,
                              msg='Approved')

    @action(detail=True, methods=['post'])
    def reject(self, request, *args, **kwargs):
        sz = WorkflowApproveRejectSerializer(data=request.data)
        sz.is_valid(raise_exception=True)
        try:
            instance = WorkflowApprovalFlowEngine.reject(
                kwargs.get('pk'),
                approver=request.user,
                reason=sz.validated_data.get('approve_reason', ''),
            )
        except ValueError as exc:
            return ErrorResponse(msg=str(exc))
        return DetailResponse(data=WorkflowApproveCompatSerializer(instance).data,
                              msg='Rejected')


# ---------------------------------------------------------------------------
# 2. WorkflowApprovalRuleViewSet
# ---------------------------------------------------------------------------
@method_decorator(require_feature(F_WORKFLOW_APPROVAL_FLOW), name='dispatch')
class WorkflowApprovalRuleViewSet(CustomModelViewSet):
    model = WorkflowApprovalRule
    queryset = WorkflowApprovalRule.objects.all()
    serializer_class = WorkflowApprovalRuleSerializer

    @action(detail=False, methods=['post'], url_path='update-node-order')
    def update_node_order(self, request, *args, **kwargs):
        orders = request.data.get('orders') or []
        for item in orders:
            try:
                node = WorkflowApprovalNode.objects.get(pk=item['id'])
                node.step_order = item['step_order']
                node.save(update_fields=['step_order', 'update_datetime'])
            except (WorkflowApprovalNode.DoesNotExist, KeyError, ValueError):
                return ErrorResponse(msg=f"Invalid item: {item}")
        return SuccessResponse(msg='Order updated')

    @action(detail=True, methods=['post'], url_path='test-match')
    def test_match(self, request, pk=None, *args, **kwargs):
        rule = self.get_object()
        workflow_id = request.data.get('workflow_id')
        if not workflow_id:
            return ErrorResponse(msg='workflow_id is required')
        from taurus.models import Workflow
        try:
            workflow = Workflow.objects.get(pk=workflow_id)
        except Workflow.DoesNotExist:
            return ErrorResponse(msg='Workflow not found')
        matches = WorkflowApprovalRuleMatcher._check_rule(
            rule, workflow, request.user,
        )
        return SuccessResponse(data={'matched': matches})


# ---------------------------------------------------------------------------
# 3. WorkflowApprovalRuleNodeViewSet
# ---------------------------------------------------------------------------
@method_decorator(require_feature(F_WORKFLOW_APPROVAL_FLOW), name='dispatch')
class WorkflowApprovalRuleNodeViewSet(CustomModelViewSet):
    model = WorkflowApprovalNode
    queryset = WorkflowApprovalNode.objects.all()
    serializer_class = WorkflowApprovalNodeSerializer

    def perform_create(self, serializer):
        rule_id = self.request.data.get('rule') or serializer.validated_data.get('rule_id')
        if serializer.validated_data.get('rule') or rule_id:
            serializer.save()
        else:
            raise ValidationError({'rule': ['This field is required']})


# ---------------------------------------------------------------------------
# 4. WorkflowApprovalInstanceViewSet
# ---------------------------------------------------------------------------
@method_decorator(require_feature(F_WORKFLOW_APPROVAL_FLOW), name='dispatch')
class WorkflowApprovalInstanceViewSet(CustomModelViewSet):
    model = WorkflowApprovalInstance
    queryset = WorkflowApprovalInstance.objects.all()
    serializer_class = WorkflowApprovalInstanceSerializer
    extra_filter_backends = []  # 和 L9895 原实现对齐

    def get_queryset(self):
        base = super().get_queryset().select_related('workflow', 'submitter')
        if getattr(self.request.user, 'is_superuser', False):
            return base.order_by('-create_datetime')
        view_type = self.request.query_params.get('view_type', '')
        if view_type == 'pending':
            return base.filter(status__in=['pending', 'approving']).order_by('-create_datetime')
        if view_type == 'pending_me':
            uid = self.request.user.id
            return base.filter(
                status__in=['pending', 'approving'],
                node_executions__candidate_approvers__contains=[{"user_id": uid}]
            ).distinct().order_by('-create_datetime')
        if view_type == 'mine':
            return base.filter(submitter_id=self.request.user.id).order_by('-create_datetime')
        return base.filter(Q(submitter_id=self.request.user.id) |
                           Q(node_executions__candidate_approvers__contains=[{"user_id": self.request.user.id}])
                           ).distinct().order_by('-create_datetime')

    @action(detail=True, methods=['post'])
    def approve(self, request, *args, **kwargs):
        try:
            instance = WorkflowApprovalFlowEngine.approve(
                kwargs.get('pk'),
                approver=request.user,
                reason=request.data.get('reason', ''),
            )
        except ValueError as exc:
            return ErrorResponse(msg=str(exc))
        return DetailResponse(data=self.serializer_class(instance).data, msg='Approved')

    @action(detail=True, methods=['post'])
    def reject(self, request, *args, **kwargs):
        try:
            instance = WorkflowApprovalFlowEngine.reject(
                kwargs.get('pk'),
                approver=request.user,
                reason=request.data.get('reason', ''),
            )
        except ValueError as exc:
            return ErrorResponse(msg=str(exc))
        return DetailResponse(data=self.serializer_class(instance).data, msg='Rejected')

    @action(detail=True, methods=['post'])
    def delegate(self, request, *args, **kwargs):
        try:
            instance = WorkflowApprovalFlowEngine.delegate(
                kwargs.get('pk'),
                approver=request.user,
                to_user_id=request.data.get('to_user_id'),
                reason=request.data.get('reason', ''),
            )
        except ValueError as exc:
            return ErrorResponse(msg=str(exc))
        return DetailResponse(data=self.serializer_class(instance).data, msg='Delegated')

    @action(detail=True, methods=['post'], url_path='add-sign')
    def add_sign(self, request, *args, **kwargs):
        try:
            instance = WorkflowApprovalFlowEngine.add_sign(
                kwargs.get('pk'),
                approver=request.user,
                user_ids=request.data.get('user_ids', []),
                reason=request.data.get('reason', ''),
            )
        except ValueError as exc:
            return ErrorResponse(msg=str(exc))
        return DetailResponse(data=self.serializer_class(instance).data, msg='Signed off')

    @action(detail=True, methods=['post'])
    def cancel(self, request, *args, **kwargs):
        try:
            instance = WorkflowApprovalFlowEngine.cancel(
                kwargs.get('pk'), operator=request.user,
            )
        except ValueError as exc:
            return ErrorResponse(msg=str(exc))
        return DetailResponse(data=self.serializer_class(instance).data, msg='Withdrawn')

    @action(detail=True)
    def nodes(self, request, *args, **kwargs):
        instance = self.get_object()
        node_execs = instance.node_executions.order_by('step_order')
        return DetailResponse(
            data={
                'instance': self.serializer_class(instance).data,
                'nodes': WorkflowApprovalNodeExecutionSerializer(node_execs, many=True).data,
            }
        )

    @action(detail=False, url_path='stats-count')
    def stats_count(self, request, *args, **kwargs):
        stats = {'pending': 0, 'pending_me': 0, 'mine': 0, 'approved': 0,
                 'rejected': 0, 'revoked': 0, 'cancelled': 0}
        stats['pending'] = self.model.objects.filter(
            status__in=['pending', 'approving']
        ).count()
        stats['approved'] = self.model.objects.filter(status='approved').count()
        stats['rejected'] = self.model.objects.filter(status='rejected').count()
        stats['cancelled'] = self.model.objects.filter(status='cancelled').count()
        stats['revoked'] = self.model.objects.filter(status='revoked').count()
        if request.user.is_authenticated:
            uid = request.user.id
            stats['mine'] = self.model.objects.filter(submitter_id=uid).count()
            stats['pending_me'] = self.model.objects.filter(
                status__in=['pending', 'approving'],
                node_executions__candidate_approvers__contains=[{"user_id": uid}]
            ).distinct().count()
        return SuccessResponse(data=stats)

    @action(detail=False, url_path='stats-counts')
    def stats_counts(self, request, *args, **kwargs):
        return self.stats_count(request, *args, **kwargs)
