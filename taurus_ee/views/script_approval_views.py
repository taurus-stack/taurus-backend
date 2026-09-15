"""Script approval ViewSets (EE only).
4 类：
  1. ScriptApproveViewSet（简单单级批准/拒绝）
  2. ScriptApprovalRuleViewSet（审批规则 CRUD + 规则测试）
  3. ScriptApprovalRuleNodeViewSet（规则节点 CRUD）
  4. ScriptApprovalInstanceViewSet（审批实例 + approve/reject/delegate/add-sign/cancel）
"""
from __future__ import annotations

from django.utils import timezone
from django.utils.decorators import method_decorator
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from dvadmin.utils.viewset import CustomModelViewSet
from dvadmin.utils.json_response import (
    SuccessResponse, DetailResponse, ErrorResponse,
)

from taurus.models import (
    ScriptApprove,
    ScriptApprovalRule,
    ScriptApprovalNode,
    ScriptApprovalInstance,
    Script,
)
from taurus.editions import require_feature
from taurus.editions.features import (
    F_SCRIPT_APPROVAL_FLOW,
)
from taurus_ee.serializers.script_approval import (
    ScriptApproveSerializer,
    ScriptApprovalRuleSerializer,
    ScriptApprovalNodeSerializer,
    ScriptApprovalInstanceSerializer,
    ScriptApprovalNodeExecutionSerializer,
)
from taurus_ee.services.script_approval_engine import (
    ApprovalRuleMatcher,
    ApprovalFlowEngine,
)


# ---------------------------------------------------------------------------
# 1. 简单单级审批（ScriptApprove）
# ---------------------------------------------------------------------------
@method_decorator(require_feature(F_SCRIPT_APPROVAL_FLOW), name='dispatch')
class ScriptApproveViewSet(CustomModelViewSet):
    """脚本单级审批申请管理（EE）。"""
    queryset = ScriptApprove.objects.all()
    serializer_class = ScriptApproveSerializer
    filterset_fields = ['script', 'status', 'risk_level', 'submitter']
    search_fields = ['submit_desc', 'approve_reason']
    ordering = ['-create_datetime']

    def perform_create(self, serializer):
        serializer.save(
            submitter=self.request.user,
            submitter_name=self.request.user.username,
            status='pending',
        )

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        approval = self.get_object()
        if approval.status != 'pending':
            return ErrorResponse(msg="This application has already been processed")
        approval.status = 'approved'
        approval.approver = request.user
        approval.approver_name = request.user.username
        approval.approve_time = timezone.now()
        approval.approve_reason = request.data.get('reason', '')
        approval.save()
        script = approval.script
        if script:
            script.status = 0  # Active
            script.save()
        return DetailResponse(data={'status': 'approved'}, msg="Approval approved")

    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, pk=None):
        approval = self.get_object()
        if approval.status != 'pending':
            return ErrorResponse(msg="This application has already been processed")
        approval.status = 'rejected'
        approval.approver = request.user
        approval.approver_name = request.user.username
        approval.approve_time = timezone.now()
        approval.approve_reason = request.data.get('reason', '')
        approval.save()
        return DetailResponse(data={'status': 'rejected'}, msg="ApprovalRejected")


# ---------------------------------------------------------------------------
# 2. 规则驱动审批：审批规则
# ---------------------------------------------------------------------------
@method_decorator(require_feature(F_SCRIPT_APPROVAL_FLOW), name='dispatch')
class ScriptApprovalRuleViewSet(CustomModelViewSet):
    """脚本多级审批规则管理。"""
    queryset = ScriptApprovalRule.objects.all()
    serializer_class = ScriptApprovalRuleSerializer
    filterset_fields = ['is_active']
    search_fields = ['name', 'description']
    ordering_fields = ['priority', 'create_datetime']
    ordering = ['priority', 'id']

    @action(detail=True, methods=['post'], url_path='nodes/order')
    def update_node_order(self, request, pk=None):
        rule = self.get_object()
        node_orders = request.data.get('node_orders', [])
        for item in node_orders:
            node = rule.nodes.filter(id=item['id']).first()
            if node:
                node.step_order = item['step_order']
                node.save()
        return SuccessResponse(msg="OrderUpdated successfully")

    @action(detail=False, methods=['post'], url_path='test-match')
    def test_match(self, request):
        script_id = request.data.get('script_id')
        try:
            script = Script.objects.get(id=script_id)
        except Script.DoesNotExist:
            return ErrorResponse(msg="Script not found")

        rule = ApprovalRuleMatcher.match(script, request.user)
        if rule:
            data = ScriptApprovalRuleSerializer(rule).data
            nodes = list(rule.nodes.order_by('step_order').values(
                'id', 'node_name', 'approver_type', 'approval_mode', 'step_order'
            ))
            data['nodes'] = nodes
            return DetailResponse(data=data, msg="Match successful")
        return DetailResponse(data=None, msg="No matching rule, approval not required")


# ---------------------------------------------------------------------------
# 3. 规则节点（节）
# ---------------------------------------------------------------------------
@method_decorator(require_feature(F_SCRIPT_APPROVAL_FLOW), name='dispatch')
class ScriptApprovalRuleNodeViewSet(CustomModelViewSet):
    """审批规则节点管理。"""
    queryset = ScriptApprovalNode.objects.all()
    serializer_class = ScriptApprovalNodeSerializer
    filterset_fields = ['rule', 'approver_type', 'approval_mode']
    search_fields = ['node_name']
    ordering_fields = ['step_order', 'create_datetime']
    ordering = ['step_order', 'id']


# ---------------------------------------------------------------------------
# 4. 审批流程实例（核心：包含完整 5 种 actions）
# ---------------------------------------------------------------------------
@method_decorator(require_feature(F_SCRIPT_APPROVAL_FLOW), name='dispatch')
class ScriptApprovalInstanceViewSet(CustomModelViewSet):
    """审批流程实例管理。"""
    queryset = ScriptApprovalInstance.objects.all()
    serializer_class = ScriptApprovalInstanceSerializer
    filterset_fields = ['script', 'status', 'rule', 'submitter']
    search_fields = ['submit_desc', 'rule_name']
    ordering_fields = ['create_datetime', 'finish_time']
    ordering = ['-create_datetime']
    permission_classes = [IsAuthenticated]
    # 跨部门可见性由 get_queryset(candidate_approvers) 控制，避免部门级 Filter 误拦截
    extra_filter_class = []

    def get_queryset(self):
        queryset = super().get_queryset()
        view_type = self.request.query_params.get('view_type', '')
        user = self.request.user

        if view_type == 'mine':
            queryset = queryset.filter(submitter=user)
        elif view_type == 'pending_me':
            from taurus.models import ScriptApprovalNodeExecution
            pending_execs = ScriptApprovalNodeExecution.objects.filter(status='pending')
            instance_ids = []
            for exec in pending_execs:
                candidate_ids = [a['user_id'] for a in (exec.candidate_approvers or [])]
                if user.id in candidate_ids:
                    if exec.instance.status == 'pending':
                        nodes = list(exec.instance.node_executions.order_by('step_order'))
                        current_node = nodes[exec.instance.current_node_index] if nodes else None
                        if current_node and current_node.id == exec.id:
                            instance_ids.append(exec.instance_id)
            queryset = queryset.filter(id__in=instance_ids)

        return queryset

    @action(detail=True, methods=['get'], url_path='nodes')
    def get_nodes(self, request, pk=None):
        instance = self.get_object()
        nodes = instance.node_executions.order_by('step_order')
        serializer = ScriptApprovalNodeExecutionSerializer(nodes, many=True)
        return SuccessResponse(data=serializer.data, msg="Retrieved successfully")

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        try:
            instance = ApprovalFlowEngine.approve(
                instance_id=pk,
                approver=request.user,
                reason=request.data.get('reason', ''),
            )
            return DetailResponse(
                data=ScriptApprovalInstanceSerializer(instance).data,
                msg="Approval approved",
            )
        except ValueError as e:
            return ErrorResponse(msg=str(e))

    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, pk=None):
        try:
            instance = ApprovalFlowEngine.reject(
                instance_id=pk,
                approver=request.user,
                reason=request.data.get('reason', ''),
            )
            return DetailResponse(
                data=ScriptApprovalInstanceSerializer(instance).data,
                msg="Rejected",
            )
        except ValueError as e:
            return ErrorResponse(msg=str(e))

    @action(detail=True, methods=['post'], url_path='delegate')
    def delegate(self, request, pk=None):
        try:
            instance = ApprovalFlowEngine.delegate(
                instance_id=pk,
                from_user=request.user,
                to_user_id=request.data.get('to_user_id'),
                reason=request.data.get('reason', ''),
            )
            return DetailResponse(
                data=ScriptApprovalInstanceSerializer(instance).data,
                msg="Delegate succeeded",
            )
        except ValueError as e:
            return ErrorResponse(msg=str(e))

    @action(detail=True, methods=['post'], url_path='add-sign')
    def add_sign(self, request, pk=None):
        try:
            instance = ApprovalFlowEngine.add_sign(
                instance_id=pk,
                operator=request.user,
                added_user_ids=request.data.get('user_ids', []),
                reason=request.data.get('reason', ''),
            )
            return DetailResponse(
                data=ScriptApprovalInstanceSerializer(instance).data,
                msg="Added reviewer successfully",
            )
        except ValueError as e:
            return ErrorResponse(msg=str(e))

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        try:
            instance = ApprovalFlowEngine.cancel(
                instance_id=pk,
                operator=request.user,
            )
            return DetailResponse(
                data=ScriptApprovalInstanceSerializer(instance).data,
                msg="Withdraw succeeded",
            )
        except ValueError as e:
            return ErrorResponse(msg=str(e))

    @action(detail=False, methods=['get'], url_path='stats/count')
    def stats_count(self, request):
        user = request.user
        from taurus.models import ScriptApprovalNodeExecution

        pending_me_count = 0
        pending_execs = ScriptApprovalNodeExecution.objects.filter(status='pending')
        for exec in pending_execs:
            candidate_ids = [a['user_id'] for a in (exec.candidate_approvers or [])]
            if user.id in candidate_ids:
                if exec.instance.status == 'pending':
                    nodes = list(exec.instance.node_executions.order_by('step_order'))
                    current_node = nodes[exec.instance.current_node_index] if nodes else None
                    if current_node and current_node.id == exec.id:
                        pending_me_count += 1

        my_pending = self.get_queryset().filter(
            submitter=user, status='pending'
        ).count()

        return DetailResponse(data={
            'pending_me': pending_me_count,
            'my_pending': my_pending,
        }, msg="Retrieved successfully")
