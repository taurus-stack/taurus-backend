"""WorkflowDAG serializer + WorkflowViewSet EE-only action helpers (DAG publish/rollback/versions)

Serializers:
  1. WorkflowDAGVersionSerializer — CE 版 model=None 懒加载 (L808) → EE 版直接绑定真实模型

Services:
  1. WorkflowDAGService — 把 WorkflowViewSet.actions 的 EE 版实现体抽离（publish_dag_version/dag_versions/rollback_dag/get_stats/risk_assessment/manifests/validate_step + submit_approve/_create_approval），taurus 侧 wrapper 通过 ee_service_or_403 调用
  2. WorkflowRiskService — risk_assessment 独立暴露（方便其他模块调用）
"""
from __future__ import annotations

from rest_framework import serializers
from django.utils import timezone
from django.db.models import Count, Sum

from dvadmin.utils.serializers import CustomModelSerializer
from dvadmin.utils.json_response import SuccessResponse, DetailResponse, ErrorResponse


class WorkflowDAGVersionSerializer(CustomModelSerializer):
    class Meta:
        from taurus.workflow.models import WorkflowDAGVersion as _Model
        model = _Model
        fields = [
            'id', 'workflow', 'version', 'definition', 'global_envs',
            'release_note', 'create_datetime',
        ]
        read_only_fields = ['create_datetime']


# ---------------------------------------------------------------------------
# Services: DAG 引擎 + 风险评估
# ---------------------------------------------------------------------------
class WorkflowRiskService:
    """Workflow risk assessment（EE 专属，和脚本审批风险评估对齐）。

    这里直接 delegate 到 WorkflowViewSet._evaluate_workflow_risk（因为它用到 workflow.*
    上大量内联属性与方法，直接复用 taurus 侧静态方法实现即可，避免重复维护一份）。
    """

    @staticmethod
    def evaluate(workflow):
        from taurus.views import WorkflowViewSet as _V
        risk_level, risk_points = _V._evaluate_workflow_risk(workflow)
        return risk_level, risk_points


class WorkflowDAGService:
    """WorkflowViewSet 的 7 个 EE action 的实现体（供 EE 模式下调用）。

    每个方法的签名保持和 WorkflowViewSet.action 一致：(service_instance, viewset, request, **kwargs)，
    以方便 taurus 侧 Thin Wrapper 简单转发。
    """

    # ---- publish_version / dag_versions / rollback_dag ----

    @staticmethod
    def dag_versions(viewset, request, pk=None):
        from taurus.workflow.models import WorkflowDAGVersion
        workflow = viewset.get_object()
        versions = WorkflowDAGVersion.objects.filter(
            workflow=workflow,
        ).order_by('-version')
        serializer = WorkflowDAGVersionSerializer(versions, many=True)
        return SuccessResponse(data=serializer.data)

    @staticmethod
    def rollback_dag(viewset, request, pk=None, version_id=None):
        from taurus.workflow.models import WorkflowDAGVersion
        workflow = viewset.get_object()
        try:
            target_ver = WorkflowDAGVersion.objects.get(pk=version_id, workflow=workflow)
        except WorkflowDAGVersion.DoesNotExist:
            return ErrorResponse(msg='Target version not found')

        workflow.dag_published_version = target_ver
        workflow.graph_definition = target_ver.definition
        workflow.global_envs = target_ver.global_envs
        workflow.save(update_fields=['dag_published_version',
                                     'graph_definition',
                                     'global_envs',
                                     'update_datetime'])
        return SuccessResponse(
            data=WorkflowDAGVersionSerializer(target_ver).data,
            msg=f'Rolled back to v{target_ver.version}',
        )

    # ---- get_stats / risk_assessment / manifests / validate_step ----

    @staticmethod
    def get_stats(viewset, request):
        from django.utils import timezone
        from taurus.utils.share_permission import ShareVisibleQS
        from taurus.models import Workflow as _WF
        from taurus.models import WorkflowApprove as _WFApprove, WorkflowApprovalInstance
        from taurus.models import WorkflowExecution

        user = request.user
        wf_qs = _WF.objects.all()
        if not getattr(user, 'is_superuser', False):
            wf_qs = ShareVisibleQS.filter_visible_workflows(wf_qs, user, request)
        total = wf_qs.count()
        public_count = wf_qs.filter(auth_type='public').count()
        private_count = wf_qs.filter(auth_type='private').count()
        need_audit_count = wf_qs.filter(need_audit=True).count()

        old_pending = _WFApprove.objects.filter(
            status='pending', workflow__in=wf_qs.values('pk')
        ).count()
        new_pending = WorkflowApprovalInstance.objects.filter(
            status__in=['pending', 'approving'], workflow__in=wf_qs.values('pk')
        ).count()
        pending_approve = old_pending + new_pending

        normal_count = wf_qs.filter(status=0).count()
        disabled_count = wf_qs.filter(status=1).count()
        archived_count = wf_qs.filter(status=3).count()
        dag_count = wf_qs.filter(workflow_mode='dag').count()
        linear_count = wf_qs.filter(workflow_mode='linear').count()

        today = timezone.now().date()
        visible_wf_ids = wf_qs.values('pk')
        today_exec = WorkflowExecution.objects.filter(
            start_time__date=today,
            workflow__in=visible_wf_ids,
        ).exclude(trigger_type='dryrun').count()
        total_exec = WorkflowExecution.objects.filter(
            workflow__in=visible_wf_ids,
        ).exclude(trigger_type='dryrun').count()
        exec_running = WorkflowExecution.objects.filter(
            workflow__in=visible_wf_ids, status=1
        ).count()
        exec_success = WorkflowExecution.objects.filter(
            workflow__in=visible_wf_ids, status=2
        ).count()
        exec_failed = WorkflowExecution.objects.filter(
            workflow__in=visible_wf_ids, status=3
        ).count()
        exec_cancelled = WorkflowExecution.objects.filter(
            workflow__in=visible_wf_ids, status=4
        ).count()
        exec_pending = WorkflowExecution.objects.filter(
            workflow__in=visible_wf_ids, status=0
        ).count()

        return SuccessResponse(data={
            'total': total,
            'public_count': public_count,
            'private_count': private_count,
            'need_audit_count': need_audit_count,
            'pending_approve': pending_approve,
            'normal_count': normal_count,
            'published': normal_count,
            'draft': 0,
            'disabled_count': disabled_count,
            'archived_count': archived_count,
            'dag_count': dag_count,
            'linear_count': linear_count,
            'today_exec': today_exec,
            'total_exec': total_exec,
            'executing': exec_running,
            'exec_success': exec_success,
            'exec_failed': exec_failed,
            'exec_cancelled': exec_cancelled,
            'exec_pending': exec_pending,
            'status_distribution': list(
                wf_qs.values('status').annotate(count=Count('status')).order_by('status')
            ),
        }, msg='Fetched successfully')

    @staticmethod
    def risk_assessment(viewset, request, pk=None):
        from taurus.views import WorkflowViewSet as _WFV
        from django.utils import timezone as _tz

        workflow = viewset.get_object()
        if request.method == 'POST':
            data = request.data or {}
            for k in ('graph_definition', 'global_envs', 'auth_type', 'need_audit'):
                if k in data:
                    setattr(workflow, k, data[k])
            if 'category_id' in data or 'category' in data:
                cid = data.get('category_id') or data.get('category')
                if cid in (None, '', 'null'):
                    workflow.category = None
                else:
                    from taurus.models import WorkflowCategory
                    try:
                        workflow.category = WorkflowCategory.objects.get(pk=cid)
                    except Exception:
                        pass
        risk_level, risk_points = _WFV._evaluate_workflow_risk(workflow)
        stats = {
            'high': sum(1 for p in risk_points if '[high]' in p),
            'warning': sum(1 for p in risk_points if '[warning]' in p),
            'info': sum(1 for p in risk_points if '[tip]' in p),
            'total': len(risk_points),
        }
        if risk_level == 'high':
            suggestion = 'Suggestion: High-risk workflows require dual review, canary on 1-3 hosts first, confirm rollback plan'
        elif risk_level == 'medium':
            suggestion = 'Suggestion: Medium-risk workflows need approver attention on destructive actions, recommend staging test'
        else:
            suggestion = 'Suggestion: Low-risk workflows still need target hosts, account permissions and window confirmed'
        if request.method == 'GET' and request.query_params.get('sync_pending') == '1':
            from taurus.models import WorkflowApprove
            WorkflowApprove.objects.filter(workflow=workflow, status='pending').update(
                risk_level=risk_level, risk_points=risk_points,
                update_datetime=timezone.now()
            )
        return SuccessResponse(data={
            'workflow_id': workflow.id,
            'risk_level': risk_level,
            'risk_level_display': {'low': 'Low', 'medium': 'Medium', 'high': 'High'}.get(risk_level, risk_level),
            'risk_points': risk_points,
            'stats': stats,
            'suggestion': suggestion,
            'assessed_at': _tz.now().isoformat(),
        }, msg='Evaluation complete')

    @staticmethod
    def manifests(viewset, request):
        from taurus.workflow.engine.registry import get_registry
        registry = get_registry()
        manifests_data = registry.manifest_all()
        manifests_data = [
            m for m in manifests_data
            if not (m.get('node_type') or '').startswith('testing_')
        ]
        return SuccessResponse(data=manifests_data)

    @staticmethod
    def validate_step(viewset, request):
        import logging
        from taurus.workflow.engine.registry import get_registry
        logger = logging.getLogger(__name__)
        registry = get_registry()
        body = request.data or {}
        node_type = (body.get('node_type') or '').strip()
        config = body.get('config') or {}
        if not node_type:
            return SuccessResponse(data={'valid': False, 'errors': {'node_type': ['node_type is required']}})
        manifest = registry.get_manifest(node_type)
        if manifest is None:
            return SuccessResponse(data={'valid': False, 'errors': {'node_type': [f'Unknown node_type: {node_type}']}})
        errors = {}
        schema = manifest.get('config_schema') or {}
        for field_name, field_def in (schema.get('properties') or {}).items():
            if field_def.get('required') and field_name not in config:
                errors.setdefault(field_name, []).append('This field is required')
        return SuccessResponse(data={'valid': len(errors) == 0, 'errors': errors})

    # ---- submit_approve / _create_approval ----

    @staticmethod
    def submit_approve(viewset, request, pk=None):
        workflow = viewset.get_object()
        has_old = workflow.approvals.filter(status='pending').exists()
        has_new = workflow.approval_instances.filter(
            status__in=['pending', 'approving']
        ).exists()
        if has_old or has_new:
            return ErrorResponse(msg='There is already a pending approval, please do not resubmit')
        submit_desc = (request.data.get('submit_desc', '') or '')[:500]
        viewset._create_approval(
            workflow, request,
            submit_desc or 'Manual approval submission'
        )
        workflow.status = 2
        workflow.save(update_fields=['status', 'update_datetime'])
        return SuccessResponse(data={'id': workflow.id}, msg='Submitted for approval')

    @staticmethod
    def create_approval(viewset, workflow, request, submit_desc='', override_approvers=None):
        """taurus 侧 WorkflowViewSet._create_approval 的 EE 实现版。"""
        from taurus_ee.services.workflow_approval_engine import WorkflowApprovalFlowEngine
        submitter = (
            request.user if hasattr(request, 'user')
            and getattr(request.user, 'is_authenticated', False) else None
        )
        if not submitter:
            return None
        instance = WorkflowApprovalFlowEngine.start_approval(
            workflow=workflow,
            submitter=submitter,
            submit_desc=submit_desc or 'Submit workflow for approval',
            override_approvers=override_approvers,
        )
        return instance
