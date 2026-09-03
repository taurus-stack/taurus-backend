from django.db import models
from django.utils import timezone
from django.conf import settings
from django.http import FileResponse, HttpResponse
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.exceptions import PermissionDenied
import logging
import os
import glob
import re
from datetime import datetime  # noqa: F401 (required for HostLogViewSet.receive log_time parsing & WF scheduling calc)


from taurus.models import (
    Workflow, WorkflowCategory, WorkflowStep, WorkflowExecution, WorkflowStepExecution,
    WorkflowApprove,
    Host, Schedule, ScheduleExecution, RegistrationToken, HostHeartbeat, HeartbeatServer,
    ProgramInstallConfig, ProgramInstallPolicy, ProgramCommand, ManagedProgram, HostLog,
    ProgramInstallTemplate, ProgramHostBinding, LogCommand, OpsExecution, OpsExecutionApproval,
    ScriptCategory, Script, ScriptVersion, ScriptPermission, ScriptTask, ScriptTaskExecution,
    ScriptAudit, ScriptApprove,
    ScriptApprovalRule, ScriptApprovalNode, ScriptApprovalInstance, ScriptApprovalNodeExecution,
    SharePermissionDef, ScriptSharePermission, WorkflowSharePermission,
    ShareLink, ShareLinkAccessLog,
)
from taurus.serializers import (
    WorkflowSerializer, WorkflowListSerializer, WorkflowExportSerializer, WorkflowStepSerializer,
    WorkflowCategorySerializer, WorkflowCategoryCreateSerializer, WorkflowCategoryUpdateSerializer,
    WorkflowApproveSerializer, WorkflowApproveCreateSerializer,
    WorkflowApproveApproveSerializer, WorkflowApproveRejectSerializer,
    WorkflowExecutionSerializer, ScheduleSerializer, ScheduleListSerializer, ScheduleExecutionSerializer,
    HostSerializer, ExecutorRegisterSerializer, RegistrationTokenSerializer,
    SupervisorHeartbeatSerializer, HostHeartbeatSerializer, HeartbeatServerSerializer,
    ProgramInstallConfigSerializer, ProgramInstallConfigCreateSerializer, ProgramInstallConfigUpdateSerializer,
    ProgramInstallPolicySerializer, ProgramInstallPolicyCreateSerializer, ProgramInstallPolicyUpdateSerializer,
    ProgramCommandSerializer, ProgramCommandCreateSerializer, ProgramCommandUpdateSerializer,
    HostLogSerializer, HostLogReceiveSerializer, ManagedProgramSerializer,
    ProgramInstallTemplateSerializer, ProgramInstallTemplateCreateSerializer, ProgramInstallTemplateUpdateSerializer,
    ProgramHostBindingSerializer, ProgramHostBindingCreateSerializer, ProgramHostBindingUpdateSerializer,
    LogCommandSerializer, LogCommandCreateSerializer,
    OpsCommandExecuteSerializer, OpsScriptExecuteSerializer, OpsFileUploadSerializer,
    OpsBackendTempUploadSerializer, OpsBatchBackendTempUploadSerializer, OpsFileListSerializer, OpsFileDownloadSerializer,
    OpsExecutionSerializer, OpsExecutionListSerializer,
    OpsExecutionApprovalSerializer, OpsExecutionApprovalListSerializer,
    OpsExecutionApprovalCreateSerializer, OpsExecutionApprovalActionSerializer,
    ScriptCategorySerializer, ScriptCategoryCreateSerializer, ScriptCategoryUpdateSerializer,
    ScriptSerializer, ScriptListSerializer, ScriptCreateSerializer, ScriptUpdateSerializer,
    ScriptVersionSerializer, ScriptPermissionSerializer,
    ScriptTaskSerializer, ScriptTaskCreateSerializer, ScriptTaskUpdateSerializer,
    ScriptTaskExecutionSerializer,
    ScriptAuditSerializer, ScriptApproveSerializer,
    ScriptApprovalRuleSerializer, ScriptApprovalNodeSerializer,
    ScriptApprovalInstanceSerializer, ScriptApprovalNodeExecutionSerializer,
    SharePermissionDefSerializer,
    ScriptSharePermissionSerializer, WorkflowSharePermissionSerializer,
    SharePermissionBatchCreateSerializer, ShareLinkSerializer,
    ShareLinkActivateSerializer, ShareLinkAccessLogSerializer,
    TaskCenterItemSerializer,
)
from taurus.utils.share_permission import (
    SharePermissionChecker,
    ShareLinkService,
    ShareVisibleQS,
    require_share_perm,
)
from taurus.ca_manager import CAManager
from taurus.config_crypto import encrypt_value
from dvadmin.utils.viewset import CustomModelViewSet
from dvadmin.utils.serializers import CustomModelSerializer
from dvadmin.utils.json_response import SuccessResponse, ErrorResponse, DetailResponse
from dvadmin.utils.filters import CoreModelFilterBankend
from rest_framework import viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status as drf_status
from rest_framework.permissions import AllowAny

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([AllowAny])
def workflow_callback_view(request, token):
    """HTTP Callback receiver. (For external systems to callback http_callback nodes).

    URL: POST /api/taurus/workflow/callback/{token}/
    Body: {
        "status": "success" | "failed",
        "data": {...},
        "error": "Error message (when status=failed)"
    }

    Process:
    1. Use token to locate the node execution instance in WorkflowNodeExecution.adapter_state
    2. Build RenderedNodeConfig based on that instance (consistent with Engine runner)
    3. Call adapter.inject_external_event(cfg, adapter_state, event)
    4. Write UnitOutput back to WorkflowNodeExecution (adapter_state / output / status / error_message)
    """
    from taurus.workflow.engine.registry import get_registry
    from taurus.workflow.engine.runner import _apply_unit_output, _build_rendered_cfg
    from taurus.workflow.models import WorkflowNodeExecution
    from taurus.workflow.engine.schemas import (
        STATUS_FAILED, STATUS_SUCCESS, STATUS_RUNNING, STATUS_CANCELLED,
    )

    data = request.data if isinstance(request.data, dict) else request.data.dict()
    callback_status = data.get('status', 'success')
    payload = data.get('data') or {}
    error_msg = data.get('error') or ''

    try:
        node_exec = WorkflowNodeExecution.objects.filter(
            adapter_state__token=token
        ).first()

        if not node_exec:
            return Response(
                {'ok': False, 'error': 'Token did not find corresponding WorkflowNodeExecution record'},
                status=drf_status.HTTP_404_NOT_FOUND,
            )

        registry = get_registry()
        adapter = registry.instantiate(node_exec.node_type)

        cfg = _build_rendered_cfg(node_exec, params=dict(node_exec.rendered_params or {}))

        event = {
            'token': token,
            'status': callback_status,
            'data': payload,
            'error': error_msg,
        }
        uo = adapter.inject_external_event(
            cfg=cfg,
            adapter_state=node_exec.adapter_state,
            event=event,
        )

        save_fields = _apply_unit_output(node_exec, uo, initial=False)
        if save_fields:
            node_exec.save(update_fields=save_fields)

        return Response({
            'ok': True,
            'token': token,
            'status': callback_status,
            'node_status': uo.status,
            'summary': uo.summary,
        })

    except Exception as exc:
        logger.exception("workflow_callback_view processing failed")
        return Response(
            {'ok': False, 'error': str(exc)},
            status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


# ---------- Celery Beat Scheduled task Register/Deregister (module-level functions, shared by WorkflowViewSet and ScheduleViewSet) ----------

logger_schedule = logging.getLogger('taurus.schedule')


def register_schedule_to_celery(schedule):
    """Register Schedule record to Celery Beat, return True means successful registration"""
    try:
        from django_celery_beat.models import PeriodicTask, CrontabSchedule, IntervalSchedule
        from taurus.tasks import execute_schedule_task

        PeriodicTask.objects.filter(name=f'schedule_{schedule.id}').delete()

        if schedule.status != 1:
            return False

        if schedule.schedule_type == 'cron':
            if not schedule.cron_expression:
                return False
            parts = schedule.cron_expression.split()
            if len(parts) != 5:
                return False
            minute, hour, day_of_month, month_of_year, day_of_week = parts
            crontab = CrontabSchedule.objects.create(
                minute=minute, hour=hour, day_of_month=day_of_month,
                month_of_year=month_of_year, day_of_week=day_of_week,
                day_of_year='*', timezone='Asia/Shanghai',
            )
            PeriodicTask.objects.create(
                name=f'schedule_{schedule.id}',
                task=execute_schedule_task.name,
                crontab=crontab,
                args=f'[{schedule.id}]',
                enabled=True,
            )

        elif schedule.schedule_type == 'interval':
            if not schedule.interval_seconds:
                return False
            interval, _ = IntervalSchedule.objects.get_or_create(
                every=schedule.interval_seconds,
                period=IntervalSchedule.SECONDS,
            )
            PeriodicTask.objects.create(
                name=f'schedule_{schedule.id}',
                task=execute_schedule_task.name,
                interval=interval,
                args=f'[{schedule.id}]',
                enabled=True,
            )

        elif schedule.schedule_type == 'once':
            if not schedule.run_once_at:
                return False
            if schedule.run_once_at < timezone.now():
                return False
            crontab = CrontabSchedule.objects.create(
                minute=str(schedule.run_once_at.minute),
                hour=str(schedule.run_once_at.hour),
                day_of_month=str(schedule.run_once_at.day),
                month_of_year=str(schedule.run_once_at.month),
                day_of_week='*', day_of_year='*',
                timezone='Asia/Shanghai',
            )
            PeriodicTask.objects.create(
                name=f'schedule_{schedule.id}',
                task=execute_schedule_task.name,
                crontab=crontab,
                args=f'[{schedule.id}]',
                enabled=True,
                one_off=True,
            )
        else:
            return False

        schedule.save()
        logger_schedule.info(f"Registered scheduled task schedule_id={schedule.id} type={schedule.schedule_type}")
        return True

    except Exception as e:
        logger_schedule.error(f"Failed to register scheduled task schedule_id={schedule.id}: {str(e)}")
        return False


def unregister_schedule_from_celery(schedule):
    """Deregister Schedule record from Celery Beat"""
    try:
        from django_celery_beat.models import PeriodicTask
        PeriodicTask.objects.filter(name=f'schedule_{schedule.id}').delete()
        logger_schedule.info(f"Deregistered scheduled task schedule_id={schedule.id}")
        return True
    except Exception as e:
        logger_schedule.error(f"Failed to deregister scheduled task schedule_id={schedule.id}: {str(e)}")
        return False


class WorkflowViewSet(CustomModelViewSet):
    """Workflow management (referenced from ScriptLibrary management pattern: Public/Private + Status machine + Approval flow)"""
    queryset = Workflow.objects.all()
    serializer_class = WorkflowSerializer
    search_fields = ['name', 'description']
    filterset_fields = ['status', 'share', 'category', 'auth_type', 'need_audit']
    ordering_fields = ['create_datetime', 'update_datetime', 'exec_count']
    ordering = ['-create_datetime']

    # Export: use dedicated WorkflowExportSerializer (format all datetime to strings) to avoid openpyxl writing
    # float values branch TypeError: float() argument must be a string or a real number, not 'datetime.datetime'
    export_serializer_class = WorkflowExportSerializer
    export_field_label = {
        'name': 'Workflow Name',
        'category_name': 'Category',
        'share': 'Visibility',
        'need_audit': 'Require Approval',
        'status_display': 'Status',
        'pending_approve_count': 'Pending Approvals',
        'steps_count': 'Node Count',
        'creator_name': 'Owner',
        'exec_count': 'Execution Count',
        'last_exec_time': 'Last Execution Time',
        'auth_type_display': 'Auth Type',
        'workflow_mode': 'Orchestration Mode',
        'create_datetime': 'Created At',
        'update_datetime': 'Updated At',
    }
    export_column_width = 30

    def _get_descendant_category_ids(self, category_id):
        """Fetch a category ID and all its child category IDs"""
        ids = [category_id]
        children = WorkflowCategory.objects.filter(parent_id=category_id)
        for child in children:
            ids.extend(self._get_descendant_category_ids(child.id))
        return ids

    def _is_public_category(self, category):
        """Similar to ScriptLibrary semantics: Public workflows usually correspond to a "Public" category directory;
        here we primarily use the category's own name convention or the workflow's need_audit/auth_type fields,
        returning True will force approval."""
        if not category:
            return False
        current = category
        visited = set()
        while current and current.id not in visited:
            visited.add(current.id)
            if current.name == 'Public Workflow':
                return True
            current = current.parent
        return False

    @staticmethod
    def _evaluate_workflow_risk(workflow):
        """Workflow risk assessment (aligned with ScriptLibrary risk grading pattern: low/medium/high + risk details)

        Assessment scope:
        1) DAG node types (destructive operation keywords in command/script/file_op)
        2) Script nodes: if associated with a Script object, inherit its risk_level
        3) Linear steps: content keywords in associated templates (bash/shell, etc.)
        4) Target host scale (number of hosts)
        5) Failure strategy (continue / fail_fast) and whether it amplifies risk propagation
        6) Global environment variables: whether they contain sensitive keywords (password/token/secret/key)
        7) Whether approval is required but no category approver is configured (organizational risk)
        """
        risk_points = []

        nodes = []
        edges = []
        if isinstance(workflow.graph_definition, dict):
            nodes = workflow.graph_definition.get('nodes') or []
            edges = workflow.graph_definition.get('edges') or []

        # Host scale
        try:
            host_count = workflow.hosts.count() if workflow.pk else 0
        except Exception:
            host_count = 0
        if host_count >= 100:
            risk_points.append(f'[high] Batch target host count {host_count} (>=100), error propagation risk is extremely high')
        elif host_count >= 30:
            risk_points.append(f'[warning] Batch target host count {host_count} (>=30), please note execution order and rollback plan')
        elif host_count >= 10:
            risk_points.append(f'[tip] Batch target host count {host_count}, suggest canary verification first')

        # Global environment variables sensitive keywords
        global_envs = workflow.global_envs or {}
        env_str = str(global_envs).lower()
        sensitive_env_keys = ['password', 'passwd', 'token', 'secret', 'api_key', 'apikey',
                              'access_key', 'private_key', 'authorization']
        found_sensitive = [k for k in sensitive_env_keys if k in env_str]
        if found_sensitive:
            risk_points.append(f'[high] Global environment variables suspected to contain sensitive fields: {",".join(found_sensitive)}, recommend using a secrets management service')

        # Destructive command keywords (command / script content strings / file_op config universal match)
        dangerous_patterns = [
            ('rm -rf', 'Includes rm -rf forced deletion'),
            ('rm -r', 'Includes rm -r recursive deletion'),
            ('sudo ', 'Includes sudo privilege escalation'),
            ('mkfs', 'Includes mkfs disk formatting'),
            ('dd if=', 'Includes dd raw disk write'),
            ('/dev/sd', 'Includes direct disk device write'),
            ('shutdown', 'Includes shutdown command'),
            ('reboot', 'Includes reboot command'),
            ('init 0', 'Includes init 0 shutdown'),
            ('init 6', 'Includes init 6 reboot'),
            ('drop table', 'Includes SQL DROP TABLE'),
            ('drop database', 'Includes SQL DROP DATABASE'),
            ('truncate table', 'Includes SQL TRUNCATE TABLE'),
            ('delete from', 'Includes SQL DELETE FROM (no WHERE risk)'),
            ('systemctl stop', 'Includes systemctl stop service stop'),
            ('kubectl delete', 'Includes kubectl delete K8s resource deletion'),
            ('docker rm -f', 'Includes docker rm -f forced container deletion'),
            ('chmod -R 777', 'Includes chmod -R 777 permission relaxation'),
            ('chown -R', 'Includes chown -R recursive owner change'),
            ('iptables -F', 'Includes iptables -F firewall flush'),
        ]

        def _scan_text(text, label=''):
            if not text:
                return []
            hits = []
            s = str(text).lower()
            for pattern, desc in dangerous_patterns:
                if pattern.lower() in s:
                    hits.append(f'[high] {label}{desc}')
            return hits

        # Scan DAG nodes
        dangerous_node_types = {'command', 'script', 'file_op', 'program'}
        for node in nodes:
            if not isinstance(node, dict):
                continue
            ntype = (node.get('node_type') or '').lower()
            nkey = node.get('node_key') or node.get('id') or 'node'
            label = f'Node[{nkey}/{ntype}] '
            # High risk type base markers
            if ntype == 'file_op':
                op = (node.get('config') or {}).get('operation')
                if op in ('delete', 'rm', 'replace', 'overwrite', 'mv'):
                    risk_points.append(f'[warning] {label}File operation: {op}, may overwrite/delete production files')
            if ntype == 'program':
                act = (node.get('config') or {}).get('action')
                if act in ('stop', 'restart', 'uninstall', 'remove'):
                    risk_points.append(f'[warning] {label}Program action: {act}, will affect service availability')
            if ntype == 'sub_workflow':
                risk_points.append(f'[tip] {label}Sub-workflow nesting, recommend separate review of sub-workflow risk')
            if ntype == 'loop':
                max_iter = (node.get('config') or {}).get('max_iterations')
                try:
                    if max_iter and int(max_iter) > 100:
                        risk_points.append(f'[warning] {label}Loop count too high ({max_iter}), amplified execution risk exists')
                except (TypeError, ValueError):
                    pass
            # Scan command/script content in node config
            cfg = node.get('config') or {}
            for k in ('command', 'content', 'script_content', 'inline_script', 'code', 'args', 'expression'):
                v = cfg.get(k)
                if isinstance(v, str):
                    risk_points.extend(_scan_text(v, label))
            # Scan env variables
            for k, v in (cfg.get('envs') or cfg.get('env_vars') or {}).items():
                if isinstance(v, str):
                    risk_points.extend(_scan_text(f'{k}={v}', label))

        # Linear mode: scan WorkflowStep.template.content
        if workflow.pk:
            try:
                for step in workflow.steps.all():
                    label = f'Step[{step.step_name or step.id}] '
                    tpl = getattr(step, 'template', None)
                    if tpl is not None:
                        for attr in ('content', 'script_content', 'command'):
                            v = getattr(tpl, attr, None)
                            if isinstance(v, str):
                                risk_points.extend(_scan_text(v, label))
                    envs = getattr(step, 'step_envs', None) or {}
                    for k, v in (envs.items() if isinstance(envs, dict) else []):
                        if isinstance(v, str):
                            risk_points.extend(_scan_text(f'{k}={v}', label))
                    # Inherit Script risk when associated with Script model
                    script_obj = None
                    for rel in ('script', 'script_version__script'):
                        if hasattr(step, rel):
                            script_obj = getattr(step, rel, None)
                            break
                    if script_obj is None and tpl is not None:
                        from taurus.models import Script
                        if isinstance(tpl, Script):
                            script_obj = tpl
                    if script_obj is not None:
                        sl = getattr(script_obj, 'risk_level', '') or ''
                        if sl == 'high':
                            risk_points.append(f'[high] {label}Associated script is high-risk level')
                        elif sl == 'medium':
                            risk_points.append(f'[warning] {label}Associated script is medium-risk level')
            except Exception:
                pass

        # DAG edges: continue on failure (non fail_fast) may propagate
        continue_count = 0
        for e in edges:
            if not isinstance(e, dict):
                continue
            cond = str(e.get('condition') or '')
            if cond in ('on_failure', 'always') or cond.lower() in ('on_failure', 'always'):
                continue_count += 1
        if continue_count >= 3:
            risk_points.append(f'[warning] {continue_count} "continue on failure" connections exist, failures may skip alerts and propagate')

        # Public workflow but category has no configured approver (organizational risk)
        if workflow.auth_type == 'public' or getattr(workflow, 'need_audit', False):
            cat = getattr(workflow, 'category', None)
            has_reviewer = False
            cur = cat
            visited = set()
            while cur and getattr(cur, 'id', None) not in visited:
                visited.add(cur.id)
                try:
                    if cur.reviewers.exists():
                        has_reviewer = True
                        break
                except Exception:
                    pass
                cur = getattr(cur, 'parent', None)
            if not has_reviewer:
                risk_points.append('[tip] Public workflow/requires approval, but the category does not have an approver configured, please contact the category administrator')

        # Deduplicate (same risk entry may be hit by multiple nodes)
        deduped = []
        seen = set()
        for p in risk_points:
            if p not in seen:
                seen.add(p)
                deduped.append(p)

        high_count = sum(1 for p in deduped if '[high]' in p)
        warn_count = sum(1 for p in deduped if '[warning]' in p)
        if high_count >= 2 or (high_count >= 1 and warn_count >= 2):
            risk_level = 'high'
        elif high_count >= 1 or warn_count >= 2:
            risk_level = 'medium'
        elif warn_count >= 1 or len(deduped) >= 1:
            risk_level = 'medium' if len(deduped) >= 3 else 'low'
        else:
            risk_level = 'low'
        return risk_level, deduped

    def _create_approval(self, workflow, request, submit_desc='', override_approvers=None):
        """Create orchestration approval record (using rule-driven approval engine)

        override_approvers: Optional, Dictionary format: {
            'approver_ids': [1,2,3],          # Or-sign person ID list
            'countersign_ids': [4,5],         # Countersign person ID list
            'approval_mode': 'any' | 'all',   # Review mode
        }

        Edition Gate: CE mode → ee_service_or_403 直接 403，不会实际生成审批实例。
        """
        # [Edition Gate] CE mode: F_WORKFLOW_APPROVAL_FLOW 禁用，直接停止审批生成
        try:
            from taurus_ee.utils.gate import ee_service_or_403
        except ImportError:
            from taurus.ee_fallback import ee_service_or_403
        from taurus.editions.features import F_WORKFLOW_APPROVAL_FLOW
        dag_svc = ee_service_or_403('workflow_dag_service', F_WORKFLOW_APPROVAL_FLOW)
        return dag_svc.create_approval(self, workflow, request, submit_desc, override_approvers)

    def get_queryset(self):
        """
        Visibility rules (referenced from ScriptLibrary management):
          - list action (Tab switch scenario):
              * All/Mine/Public Tab: workflows I created + Public workflows (workflows shared to me only appear in "Shared to Me" Tab)
              * shared_to_me Tab: directly shared to me + link-shared activated workflows (excluding my created and Public workflows)
              * shared_by_me Tab: workflows shared by me to others (I created and have share records or share links)
          - retrieve / update / destroy and other detail actions:
              * Full visibility (my created + Public + shared to me), ensure get_object can retrieve the object
        mine parameter: show only mine; category parameter: recursive filter
        """
        from django.db.models import Subquery, OuterRef, Count, Max
        queryset = super().get_queryset()
        user = getattr(self.request, 'user', None)
        view = self.request.query_params.get('view')
        action = getattr(self, 'action', None)

        if action == 'list':
            if view == 'shared_to_me':
                q_shared = ShareVisibleQS._shared_to_user_workflows_q(user, self.request)
                queryset = queryset.filter(q_shared).exclude(creator=user).exclude(auth_type='public')
            else:
                if user and not getattr(user, 'is_superuser', False):
                    queryset = ShareVisibleQS.filter_visible_workflows(queryset, user, self.request, include_shared=False)
            if view == 'shared_by_me':
                from taurus.models import WorkflowSharePermission, ShareLink, Workflow
                from django.db.models import Q as _Q
                now = timezone.now()
                now_cond = _Q(expire_time__isnull=True) | _Q(expire_time__gte=now)
                perm_wf_ids = set(
                    WorkflowSharePermission.objects.filter(workflow__creator=user)
                    .exclude(subject_type='user', subject_id=user.id if user and hasattr(user, 'id') else '')
                    .filter(now_cond)
                    .values_list('workflow_id', flat=True)
                )
                link_resource_ids = ShareLink.objects.filter(
                    resource_type='workflow',
                    is_active=True,
                ).filter(now_cond).values_list('resource_id', flat=True)
                link_rids_int = set()
                for rid in link_resource_ids:
                    try:
                        link_rids_int.add(int(rid))
                    except (ValueError, TypeError):
                        pass
                if link_rids_int:
                    link_wf_ids = set(
                        Workflow.objects.filter(
                            creator=user,
                            id__in=link_rids_int,
                        ).values_list('id', flat=True)
                    )
                else:
                    link_wf_ids = set()
                all_ids = perm_wf_ids | link_wf_ids
                if all_ids:
                    queryset = queryset.filter(id__in=all_ids, creator=user)
                else:
                    queryset = queryset.none()
        else:
            if user and not getattr(user, 'is_superuser', False):
                queryset = ShareVisibleQS.filter_visible_workflows(queryset, user, self.request, include_shared=True)

        if self.request.query_params.get('mine') == 'true':
            queryset = queryset.filter(creator=self.request.user)
        auth_type = self.request.query_params.get('auth_type')
        if auth_type:
            queryset = queryset.filter(auth_type=auth_type)
        category_id = self.request.query_params.get('category_id') or self.request.query_params.get('category')
        if category_id:
            try:
                all_ids = self._get_descendant_category_ids(int(category_id))
                queryset = queryset.filter(category_id__in=all_ids)
            except (ValueError, TypeError):
                pass
        if getattr(self, 'action', None) == 'list':
            from taurus.models import WorkflowExecution
            cnt_qs = (
                WorkflowExecution.objects
                .filter(workflow=OuterRef('pk'))
                .exclude(trigger_type='dryrun')
                .order_by()
                .values('workflow')
                .annotate(c=Count('pk'))
                .values('c')
            )
            time_qs = (
                WorkflowExecution.objects
                .filter(workflow=OuterRef('pk'))
                .exclude(trigger_type='dryrun')
                .order_by()
                .values('workflow')
                .annotate(m=Max('start_time'))
                .values('m')
            )
            queryset = queryset.annotate(
                _actual_exec_count=Subquery(cnt_qs, output_field=models.IntegerField()),
                _actual_last_exec_time=Subquery(time_qs, output_field=models.DateTimeField()),
            )
        return queryset

    def get_serializer_class(self):
        if self.action == 'list':
            return WorkflowListSerializer
        return WorkflowSerializer

    def _prefetch_share_info_for_list(self, wf_items, user, request):
        """Batch inject share_summary + _current_perms for list rows, avoid N+1 queries (aligned with ScriptViewSet mechanism)"""
        from django.utils import timezone
        from django.contrib.auth import get_user_model
        from taurus.models import (
            WorkflowSharePermission, ShareLink, SharePermissionDef,
        )
        from dvadmin.system.models import Role, Dept
        from taurus.utils.share_permission import SharePermissionChecker, ALL_WORKFLOW_PERMS

        User = get_user_model()
        if not wf_items:
            return
        wf_ids = [w.id for w in wf_items]
        wf_by_id = {w.id: w for w in wf_items}

        # ===== 1. Direct share =====
        now = timezone.now()
        now_cond = models.Q(expire_time__isnull=True) | models.Q(expire_time__gte=now)
        perms_qs = WorkflowSharePermission.objects.filter(
            workflow_id__in=wf_ids
        ).filter(now_cond).order_by('-create_datetime')
        perms_by_wf: dict = {wid: [] for wid in wf_ids}
        subject_user_ids: set = set()
        subject_role_ids: set = set()
        subject_dept_ids: set = set()
        for sp in perms_qs:
            perms_by_wf.setdefault(sp.workflow_id, []).append(sp)
            if sp.subject_type == 'user':
                try:
                    subject_user_ids.add(int(sp.subject_id))
                except (ValueError, TypeError):
                    pass
            elif sp.subject_type == 'role':
                try:
                    subject_role_ids.add(int(sp.subject_id))
                except (ValueError, TypeError):
                    pass
            elif sp.subject_type == 'dept':
                try:
                    subject_dept_ids.add(int(sp.subject_id))
                except (ValueError, TypeError):
                    pass

        # Subject name map
        user_names: dict = {}
        if subject_user_ids:
            user_names = {
                str(u.id): u.username for u in User.objects.filter(id__in=subject_user_ids).only('id', 'username')
            }
        role_names: dict = {}
        if subject_role_ids:
            role_names = {
                str(r.id): r.name for r in Role.objects.filter(id__in=subject_role_ids).only('id', 'name')
            }
        dept_names: dict = {}
        if subject_dept_ids:
            dept_names = {
                str(d.id): d.name for d in Dept.objects.filter(id__in=subject_dept_ids).only('id', 'name')
            }
        subject_type_label = {'user': 'User', 'role': 'Role', 'dept': 'Department'}

        # ===== 2. Share link =====
        links_qs = ShareLink.objects.filter(
            resource_type='workflow',
            resource_id__in=[str(x) for x in wf_ids] + [int(x) for x in wf_ids],
            is_active=True,
        ).filter(now_cond).order_by('-create_datetime')
        links_by_wf: dict = {wid: [] for wid in wf_ids}
        for lk in links_qs:
            try:
                w_id = int(lk.resource_id)
            except (ValueError, TypeError):
                continue
            if w_id in links_by_wf:
                links_by_wf[w_id].append(lk)

        # ===== 3. Aggregate share_summary =====
        for wf in wf_items:
            wid = wf.id
            direct_list = perms_by_wf.get(wid, []) or []
            link_list = links_by_wf.get(wid, []) or []
            subjects_out = []
            for sp in direct_list:
                if sp.subject_type == 'user':
                    name = user_names.get(str(sp.subject_id), f"User#{sp.subject_id}")
                elif sp.subject_type == 'role':
                    name = role_names.get(str(sp.subject_id), f"Role#{sp.subject_id}")
                elif sp.subject_type == 'dept':
                    name = dept_names.get(str(sp.subject_id), f"Dept#{sp.subject_id}")
                else:
                    name = f"{sp.subject_type}#{sp.subject_id}"
                perms_list = list(sp.permissions or [])
                perm_display_list = []
                for pc in perms_list[:5]:
                    try:
                        perm_display_list.append(pc.split(':', 1)[-1])
                    except Exception:
                        perm_display_list.append(pc)
                subjects_out.append({
                    'subject_type': sp.subject_type,
                    'subject_type_label': subject_type_label.get(sp.subject_type, sp.subject_type),
                    'subject_id': str(sp.subject_id),
                    'subject_name': name,
                    'perm_count': len(perms_list),
                    'perm_sample': perm_display_list,
                    'expire_time': sp.expire_time.isoformat() if sp.expire_time else None,
                })
            links_out = []
            for lk in link_list:
                scope_label = 'Anyone' if lk.access_scope == 'anyone' else 'Logged-in only'
                bind_info = ''
                if lk.bind_subject_type:
                    type_map = {'user': 'User', 'role': 'Role', 'dept': 'Department'}
                    bind_info = f" bound to {type_map.get(lk.bind_subject_type, lk.bind_subject_type)}"
                links_out.append({
                    'id': lk.id,
                    'share_token': lk.share_token or '',
                    'name': lk.remark or f"Share link {scope_label}{bind_info}",
                    'scope': lk.access_scope or 'authenticated',
                    'access_count': lk.current_access_count or 0,
                    'max_access': lk.max_access_count or 0,
                    'expire_time': lk.expire_time.isoformat() if lk.expire_time else None,
                    'has_password': False,
                    'is_active': bool(lk.is_active),
                })
            setattr(wf, '_share_summary', {
                'total': len(subjects_out) + len(links_out),
                'direct_count': len(subjects_out),
                'link_count': len(links_out),
                'subjects': subjects_out,
                'links': links_out,
            })

        # ===== 4. Current user effective perms (avoid N+1 per-script query) =====
        perm_aliases = {
            'workflow:execute': ['workflow:trial_run'],
            'workflow:edit': ['workflow:edit_graph', 'workflow:edit_steps', 'workflow:edit_hosts'],
        }
        perm_defs_all = sorted(list(ALL_WORKFLOW_PERMS))
        for wf in wf_items:
            owner = bool(getattr(user, 'is_superuser', False)) or (
                getattr(wf, 'creator_id', None) is not None
                and str(wf.creator_id) == str(getattr(user, 'pk', None))
            )
            if owner:
                setattr(wf, '_current_perms', list(perm_defs_all))
            else:
                perms = set(SharePermissionChecker.get_user_effective_perms(
                    user, 'workflow', wf, request=request
                ))
                expanded = set(perms)
                for p in perms:
                    for alias in perm_aliases.get(p, []):
                        expanded.add(alias)
                setattr(wf, '_current_perms', list(expanded))

    def list(self, request, *args, **kwargs):
        """List: after applying existing annotations, batch inject current_perms for frontend per-button permission judgment"""
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            self._prefetch_share_info_for_list(page, request.user, request)
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        data_list = list(queryset)
        self._prefetch_share_info_for_list(data_list, request.user, request)
        serializer = self.get_serializer(data_list, many=True)
        return SuccessResponse(data=serializer.data, msg='Fetched successfully')

    def perform_create(self, serializer):
        # === 配额校验：社区版 max_workflows（10 条上限），EE 不限 ===
        from taurus.editions.loader import check_quota as _check_quota
        from taurus.models import Workflow as _WorkflowModel
        _current_total = _WorkflowModel.objects.count()
        _check_quota('max_workflows', _current_total, '工作流')
        workflow = serializer.save(creator=self.request.user)
        # Public workflow or belonging to public workflow category: auto-set to pending approval and create approval record
        if workflow.auth_type == 'public' or workflow.need_audit or self._is_public_category(workflow.category):
            workflow.status = 2  # Pending approval
            workflow.save(update_fields=['status', 'update_datetime'])
            self._create_approval(workflow, self.request, 'New public workflow submitted for review')
        # Sync schedule config to Schedule model and register Celery Beat
        self._sync_workflow_schedule(workflow)

    def perform_update(self, serializer):
        old = self.get_object()
        was_public = (old.auth_type == 'public' or self._is_public_category(old.category) or old.need_audit)
        workflow = serializer.save()
        now_is_public = (workflow.auth_type == 'public' or self._is_public_category(workflow.category) or workflow.need_audit)
        # Converted to public or public workflow key content changed: re-approval
        if now_is_public and (not was_public or old.graph_definition != workflow.graph_definition):
            workflow.status = 2
            workflow.save(update_fields=['status', 'update_datetime'])
            self._create_approval(workflow, self.request, 'Resubmitted for review after changes')
        # Sync schedule config to Schedule model and register Celery Beat
        self._sync_workflow_schedule(workflow)

    def _sync_workflow_schedule(self, workflow):
        """Sync workflow/schedule config to Schedule model.
        Dispatch execution is handled by taurus-scheduler scanning the DB; Celery Beat registration is no longer used."""
        from taurus.models import Schedule

        try:
            existing = Schedule.objects.filter(workflow=workflow, target_type='workflow').first()

            if not workflow.has_schedule or not workflow.schedule_enabled:
                if existing:
                    existing.status = 0
                    existing.save(update_fields=['status'])
                return

            if not existing:
                Schedule.objects.create(
                    name=f'{workflow.name} Scheduled task',
                    description=workflow.description or '',
                    schedule_type=workflow.schedule_type or 'once',
                    cron_expression=workflow.cron_expression,
                    interval_seconds=workflow.interval_seconds,
                    run_once_at=workflow.run_once_at,
                    target_type='workflow',
                    workflow=workflow,
                    status=1,
                    creator=getattr(workflow, 'creator', None),
                )
            else:
                existing.name = f'{workflow.name} scheduled task'
                existing.schedule_type = workflow.schedule_type or 'once'
                existing.cron_expression = workflow.cron_expression
                existing.interval_seconds = workflow.interval_seconds
                existing.run_once_at = workflow.run_once_at
                existing.status = 1
                existing.save(update_fields=['name', 'schedule_type', 'cron_expression',
                                           'interval_seconds', 'run_once_at', 'status'])

        except Exception as e:
            logger_schedule.error(f"Failed to sync workflow scheduled task workflow_id={workflow.id}: {str(e)}")

    # ---------- Original step CRUD / Execute / Steps ----------
    @action(detail=True, methods=['post'])
    @require_share_perm('workflow:edit_steps')
    def add_step(self, request, pk=None):
        workflow = self.get_object()
        serializer = WorkflowStepSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(workflow=workflow)
            return SuccessResponse(data=serializer.data, msg="Step added successfully")
        return ErrorResponse(data=serializer.errors, msg="Step addition failed")

    @action(detail=True, methods=['put'])
    @require_share_perm('workflow:edit_steps')
    def update_step(self, request, pk=None):
        step_id = request.data.get('step_id')
        if not step_id:
            return ErrorResponse(msg="Missing step ID")
        try:
            step = WorkflowStep.objects.get(id=step_id, workflow=self.get_object())
        except WorkflowStep.DoesNotExist:
            return ErrorResponse(msg="Step not found")
        serializer = WorkflowStepSerializer(step, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return SuccessResponse(data=serializer.data, msg="Step updated successfully")
        return ErrorResponse(data=serializer.errors, msg="Step update failed")

    @action(detail=True, methods=['delete'])
    @require_share_perm('workflow:edit_steps')
    def delete_step(self, request, pk=None):
        step_id = request.data.get('step_id')
        if not step_id:
            return ErrorResponse(msg="Missing step ID")
        try:
            step = WorkflowStep.objects.get(id=step_id, workflow=self.get_object())
            step.delete()
            return SuccessResponse(msg="Step deleted successfully")
        except WorkflowStep.DoesNotExist:
            return ErrorResponse(msg="Step not found")

    @action(detail=True, methods=['post'])
    @require_share_perm('workflow:execute')
    def execute(self, request, pk=None):
        from django.utils import timezone
        workflow = self.get_object()
        host_ids = request.data.get('host_ids', [])
        if not host_ids:
            return ErrorResponse(msg="Please select at least one host")
        hosts = Host.objects.filter(id__in=host_ids)
        if hosts.count() != len(host_ids):
            return ErrorResponse(msg="Some hosts do not exist")
        steps = workflow.steps.order_by('step_order')
        if not steps.exists():
            return ErrorResponse(msg="Workflow has no configured steps")

        # Rule-driven approval interception: if approval rule is matched, block execution and require approval flow
        from taurus.views import WorkflowApprovalRuleMatcher
        submitter = request.user if hasattr(request, 'user') and request.user.is_authenticated else None
        if submitter:
            matched_rule = WorkflowApprovalRuleMatcher.match(workflow, submitter)
            if matched_rule is not None:
                # Auto-create approval instance
                instance = WorkflowApprovalFlowEngine.start_approval(
                    workflow=workflow,
                    submitter=submitter,
                    submit_desc=request.data.get('submit_desc', '')[:500] or 'Auto-approval before execution',
                )
                return SuccessResponse(
                    data={
                        'need_approval': True,
                        'instance_id': instance.id if instance else None,
                        'rule_id': matched_rule.id,
                        'rule_name': matched_rule.name,
                        'status': workflow.status,
                    },
                    msg=f"Approval rule triggered [{matched_rule.name}], execution requires approval first"
                )
            # need_audit fallback (rule not matched but marked as requiring approval)
            if getattr(workflow, 'need_audit', False):
                pending = workflow.approvals.filter(status='pending').exists()
                from taurus.models import WorkflowApprovalInstance
                pending_new = WorkflowApprovalInstance.objects.filter(
                    workflow=workflow, status__in=['pending', 'approving']
                ).exists()
                if not (pending or pending_new):
                    instance = WorkflowApprovalFlowEngine.start_approval(
                        workflow=workflow,
                        submitter=submitter,
                        submit_desc=request.data.get('submit_desc', '')[:500] or 'Auto-approval before execution',
                    )
                    return SuccessResponse(
                        data={
                            'need_approval': True,
                            'instance_id': instance.id if instance else None,
                            'status': workflow.status,
                        },
                        msg="This workflow requires approval, please wait for approval before execution"
                    )
                if workflow.status == 2:
                    return ErrorResponse(msg="This workflow is pending approval, please wait for approval before execution")

        execution = WorkflowExecution.objects.create(
            workflow=workflow,
            status=1,
            start_time=timezone.now(),
            context={'global_envs': request.data.get('global_envs', {})}
        )
        for host in hosts:
            for step in steps:
                WorkflowStepExecution.objects.create(
                    execution=execution, step=step, status=0, host=host
                )
        # Execution counter
        workflow.exec_count = (workflow.exec_count or 0) + 1
        workflow.last_exec_time = timezone.now()
        workflow.save(update_fields=['exec_count', 'last_exec_time', 'update_datetime'])
        return SuccessResponse(data={'execution_id': execution.id}, msg="Workflow execution started")

    @action(detail=True, methods=['get'])
    @require_share_perm('workflow:view')
    def steps(self, request, pk=None):
        workflow = self.get_object()
        steps = workflow.steps.order_by('step_order')
        serializer = WorkflowStepSerializer(steps, many=True)
        return SuccessResponse(data=serializer.data)

    # ---------- New actions: status toggle / copy / commit approval / stats ----------
    @action(detail=True, methods=['post'])
    @require_share_perm('workflow:toggle_status')
    def toggle_status(self, request, pk=None):
        """Enable <-> Disable toggle (State machine: 0 Active <-> 1 Disabled)"""
        workflow = self.get_object()
        if workflow.status == 0:
            workflow.status = 1
            label = 'Disabled'
        elif workflow.status == 1:
            workflow.status = 0
            label = 'Enabled'
        elif workflow.status == 2:
            return ErrorResponse(msg='Pending approval status, cannot enable/disable')
        elif workflow.status == 3:
            return ErrorResponse(msg='Archived status, cannot enable/disable')
        else:
            return ErrorResponse(msg='Unknown status')
        workflow.save(update_fields=['status', 'update_datetime'])
        return SuccessResponse(data={'id': workflow.id, 'status': workflow.status}, msg=f'{label} successfully')

    @action(detail=True, methods=['post'], url_path='toggle-schedule-enabled')
    @require_share_perm('workflow:toggle_status')
    def toggle_schedule_enabled(self, request, pk=None):
        """Scheduled trigger enable/disable toggle"""
        workflow = self.get_object()
        if not workflow.has_schedule:
            return ErrorResponse(msg='This workflow has no scheduled trigger configured')
        workflow.schedule_enabled = not workflow.schedule_enabled
        workflow.save(update_fields=['schedule_enabled', 'update_datetime'])
        label = 'Enabled' if workflow.schedule_enabled else 'Disabled'
        return SuccessResponse(
            data={'id': workflow.id, 'schedule_enabled': workflow.schedule_enabled},
            msg=f'Scheduled trigger {label}'
        )

    @action(detail=True, methods=['post'])
    @require_share_perm('workflow:copy')
    def copy_workflow(self, request, pk=None):
        """Copy workflow (new workflow defaults to Private + Active)"""
        # === 配额校验：社区版 max_workflows（10 条上限），EE 不限 ===
        from taurus.editions.loader import check_quota as _check_quota
        from taurus.models import Workflow as _WorkflowModel
        _current_total = _WorkflowModel.objects.count()
        _check_quota('max_workflows', _current_total, '工作流')
        workflow = self.get_object()
        new_wf = Workflow.objects.create(
            name=f'{workflow.name} (Copy)',
            description=workflow.description,
            category=workflow.category,
            global_envs=workflow.global_envs,
            status=0,
            share=False,
            auth_type='private',
            need_audit=False,
            workflow_mode=workflow.workflow_mode,
            graph_definition=workflow.graph_definition,
            graph_version=0,
            creator=request.user,
        )
        # Copy hosts association
        new_wf.hosts.set(workflow.hosts.all())
        # Copy steps
        for s in workflow.steps.all():
            WorkflowStep.objects.create(
                workflow=new_wf, template=s.template, step_name=s.step_name,
                step_order=s.step_order, step_envs=s.step_envs, step_args=s.step_args,
                on_failure=s.on_failure, timeout=s.timeout, creator=request.user,
            )
        return SuccessResponse(data={'id': new_wf.id, 'name': new_wf.name}, msg='Workflow copied successfully')

    @action(detail=True, methods=['post'])
    @require_share_perm('workflow:submit_approve')
    def submit_approve(self, request, pk=None):
        """Manually commit approval — [Edition Gate: F_WORKFLOW_APPROVAL_FLOW]"""
        from taurus.editions.loader import has_feature
        from taurus.editions.features import F_WORKFLOW_APPROVAL_FLOW
        svc = _get_workflow_dag_service_or_none(F_WORKFLOW_APPROVAL_FLOW)
        if svc is None or not has_feature(F_WORKFLOW_APPROVAL_FLOW):
            return ErrorResponse(msg='审批流为企业版专属功能，请升级企业版解锁')
        return svc.submit_approve(self, request, pk)

    @action(detail=False, methods=['get'], url_path='stats')
    def get_stats(self, request):
        """Workflow statistics — [Edition Gate: F_WORKFLOW_DAG_ENGINE] (CE fallback using local ORM aggregation)"""
        from taurus.editions.features import F_WORKFLOW_DAG_ENGINE
        svc = _get_workflow_dag_service_or_none(F_WORKFLOW_DAG_ENGINE)
        if svc is not None:
            try:
                return svc.get_stats(self, request)
            except Exception:
                pass
        return _ce_workflow_stats_response()

    @action(detail=True, methods=['get', 'post'])
    @require_share_perm('workflow:view_risk')
    def risk_assessment(self, request, pk=None):
        """Workflow risk assessment — [Edition Gate: F_WORKFLOW_RISK_ASSESSMENT] (CE: return placeholder)"""
        from taurus.editions.loader import has_feature
        from taurus.editions.features import F_WORKFLOW_RISK_ASSESSMENT
        svc = _get_workflow_dag_service_or_none(F_WORKFLOW_RISK_ASSESSMENT)
        if svc is not None and has_feature(F_WORKFLOW_RISK_ASSESSMENT):
            return svc.risk_assessment(self, request, pk)
        return SuccessResponse(data={
            'risk_level': 'not_available',
            'score': 0,
            'items': [],
            'detail': '风险评估为企业版专属功能',
        })

    @action(detail=False, methods=['get'], url_path='manifests')
    def manifests(self, request):
        """Manifest list — [Edition Gate: F_WORKFLOW_DAG_ENGINE] (CE: return empty list; frontend loads built-ins locally)"""
        from taurus.editions.features import F_WORKFLOW_DAG_ENGINE
        svc = _get_workflow_dag_service_or_none(F_WORKFLOW_DAG_ENGINE)
        if svc is not None:
            try:
                return svc.manifests(self, request)
            except Exception:
                pass
        return SuccessResponse(data=[])

    @action(detail=False, methods=['post'], url_path='validate_step')
    def validate_step(self, request):
        """Validate node config — [Edition Gate: F_WORKFLOW_DAG_ENGINE] (CE: lenient pass)"""
        from taurus.editions.features import F_WORKFLOW_DAG_ENGINE
        svc = _get_workflow_dag_service_or_none(F_WORKFLOW_DAG_ENGINE)
        if svc is not None:
            try:
                return svc.validate_step(self, request)
            except Exception:
                pass
        return SuccessResponse(data={'valid': True, 'errors': [], 'warnings': []})

    @action(detail=True, methods=['post'])
    @require_share_perm('workflow:publish')
    def publish(self, request, pk=None):
        """Release DAG Version:Check → Frozen snapshot → create WorkflowDAGVersion"""
        from taurus.workflow.engine.dag_validator import validate_dag, WorkflowDAG
        from taurus.workflow.models import WorkflowDAGVersion

        workflow = self.get_object()
        definition = request.data.get('definition')
        if not definition or not isinstance(definition, dict):
            return ErrorResponse(msg='Missing definition or invalid format')

        raw_nodes = definition.get('nodes') or []
        raw_edges = definition.get('edges') or []

        # Normalize:
        # - edges support both new fields {from_key, to_key} and old fields {from, to} (backward compatibility)
        # - Filter out edges with missing source/target or half-entry edges (e.g. frontend just dragged a connection line, not yet saved / dirty data)
        normalized_nodes = []
        for i, n in enumerate(raw_nodes):
            if not isinstance(n, dict):
                continue
            nk = n.get('node_key') or n.get('id') or f'node_{i}'
            norm = dict(n)
            norm['node_key'] = str(nk)
            if not norm.get('node_type'):
                norm['node_type'] = 'noop'
            normalized_nodes.append(norm)

        normalized_edges = []
        for i, e in enumerate(raw_edges):
            if not isinstance(e, dict):
                continue
            fk = e.get('from_key') if e.get('from_key') is not None else e.get('from')
            tk = e.get('to_key') if e.get('to_key') is not None else e.get('to')
            if not fk or not tk:
                continue
            norm = dict(e)
            norm['from_key'] = str(fk)
            norm['to_key'] = str(tk)
            norm.setdefault('condition', '')
            normalized_edges.append(norm)

        # Write normalized definition back (WorkflowDAGVersion stores as-is), ensuring field consistency during rollback
        definition['nodes'] = normalized_nodes
        definition['edges'] = normalized_edges

        dag = WorkflowDAG(nodes=normalized_nodes, edges=normalized_edges)
        result = validate_dag(dag, strict=True)
        if not result.is_valid:
            errors_list = [f'{ptr}: {msg}' for ptr, msg in result.iter_errors()]
            return ErrorResponse(msg='DAG validation failed', data={'errors': errors_list})

        current_max = WorkflowDAGVersion.objects.filter(workflow=workflow).aggregate(
            max_ver=models.Max('version')
        )['max_ver'] or 0

        global_envs = request.data.get('global_envs') or {}
        release_note = request.data.get('release_note', '')[:500]

        dag_ver = WorkflowDAGVersion.objects.create(
            workflow=workflow,
            version=current_max + 1,
            definition=definition,
            global_envs=global_envs,
            release_note=release_note,
            creator_id=request.user.pk,
        )
        workflow.dag_published_version = dag_ver
        # Public workflow / need_audit / belonging to public category: after release, enter approval flow (aligned with perform_create logic)
        need_approval = (
            workflow.auth_type == 'public'
            or workflow.need_audit
            or self._is_public_category(workflow.category)
        )
        if need_approval:
            workflow.status = 2  # Pending approval
            workflow.save(update_fields=['dag_published_version', 'status', 'update_datetime'])
            # Check whether a pending approval instance already exists (avoid duplicate caused by update obj trigger perform_update)
            from taurus.models import WorkflowApprovalInstance
            existing_pending = None
            try:
                pending_old = workflow.approvals.filter(status='pending').exists()
                existing_pending = WorkflowApprovalInstance.objects.filter(
                    workflow=workflow,
                    status__in=('pending', 'approving'),
                ).order_by('-created_at').first()
                if pending_old and not existing_pending:
                    existing_pending = workflow.approvals.filter(status='pending').first()
            except Exception:
                existing_pending = None
            if existing_pending:
                approval_instance = existing_pending
            else:
                # Read frontend popup-selected approver config from request body (use preferentially, override workflow.custom_approver_ids)
                override_approvers = None
                try:
                    _approver_ids = request.data.get('approver_ids')
                    _countersign_ids = request.data.get('countersign_ids')
                    _approval_mode = request.data.get('approval_mode')
                    _submit_desc = request.data.get('submit_desc') or ''
                    _has_any = isinstance(_approver_ids, list) and len(_approver_ids) > 0
                    _has_all = isinstance(_countersign_ids, list) and len(_countersign_ids) > 0
                    if _has_any or _has_all:
                        override_approvers = {
                            'approver_ids': list(_approver_ids) if _has_any else [],
                            'countersign_ids': list(_countersign_ids) if _has_all else [],
                            'approval_mode': 'all' if (_approval_mode == 'all' or _has_all) else 'any',
                        }
                except Exception:
                    override_approvers = None
                    _submit_desc = ''
                submit_desc_full = f'Submit v{dag_ver.version} for approval'
                if _submit_desc:
                    submit_desc_full = f'{submit_desc_full}：{str(_submit_desc)[:400]}'
                approval_instance = self._create_approval(
                    workflow, request, submit_desc_full, override_approvers=override_approvers
                )
            from taurus.serializers import WorkflowDAGVersionSerializer
            return SuccessResponse(
                data={
                    'version': WorkflowDAGVersionSerializer(dag_ver).data,
                    'need_approval': True,
                    'approval_instance_id': getattr(approval_instance, 'id', None),
                    'status': 2,
                },
                msg=f'Version v{dag_ver.version} submitted for approval',
            )
        workflow.status = 0  # Active
        workflow.save(update_fields=['dag_published_version', 'status', 'update_datetime'])

        # After successful release, sync schedule config to Schedule model
        self._sync_workflow_schedule(workflow)

        from taurus.serializers import WorkflowDAGVersionSerializer
        return SuccessResponse(
            data={
                'version': WorkflowDAGVersionSerializer(dag_ver).data,
                'need_approval': False,
                'status': 0,
            },
            msg=f'Published v{dag_ver.version}',
        )

    @action(detail=True, methods=['get'])
    @require_share_perm('workflow:view_version')
    def dag_versions(self, request, pk=None):
        """DAG version history — [Edition Gate: F_WORKFLOW_DAG_VERSIONING]. CE falls back to raw ORM query."""
        from taurus.editions.features import F_WORKFLOW_DAG_VERSIONING
        svc = _get_workflow_dag_service_or_none(F_WORKFLOW_DAG_VERSIONING)
        if svc is not None:
            return svc.dag_versions(self, request, pk)
        # ---- CE: direct ORM listing on shared WorkflowDAGVersion table ----
        from taurus.workflow.models import WorkflowDAGVersion
        from dvadmin.utils.json_response import SuccessResponse
        wf = self.get_object()
        qs = WorkflowDAGVersion.objects.filter(workflow_id=wf.id).order_by('-version')
        published_id = wf.dag_published_version_id
        items = []
        for v in qs:
            creator = getattr(v, 'creator', None)
            items.append({
                'id': v.id,
                'version': v.version,
                'release_note': v.release_note or '',
                'is_published': published_id is not None and v.id == published_id,
                'node_count': len((v.definition or {}).get('nodes', [])),
                'edge_count': len((v.definition or {}).get('edges', [])),
                'creator_id': getattr(creator, 'id', None),
                'creator_name': getattr(creator, 'name', None) or getattr(creator, 'username', None),
                'create_datetime': v.create_datetime.isoformat() if v.create_datetime else None,
                'update_datetime': v.update_datetime.isoformat() if v.update_datetime else None,
            })
        return SuccessResponse(data=items, msg='success', page=1, limit=max(1, len(items)), total=len(items))

    @action(detail=True, methods=['post'], url_path='rollback-dag/(?P<version_id>[^/.]+)')
    @require_share_perm('workflow:rollback')
    def rollback_dag(self, request, pk=None, version_id=None):
        """Rollback DAG to a published snapshot — EE-only. On CE returns friendly error toast."""
        from taurus.editions.features import F_WORKFLOW_DAG_VERSIONING
        svc = _get_workflow_dag_service_or_none(F_WORKFLOW_DAG_VERSIONING)
        if svc is not None:
            return svc.rollback_dag(self, request, pk, version_id)
        return ErrorResponse(msg='工作流版本回滚为企业版专属功能，升级到企业版即可解锁。')

    @action(detail=True, methods=['post'])
    def trigger(self, request, pk=None):
        """Trigger DAG workflow execution (dry run workflow: trial_run / formal execution workflow: execute, checked separately)"""
        from taurus.workflow.engine.runner import WorkflowRunner, WorkflowRunnerError
        from taurus.utils.share_permission import SharePermissionChecker
        from rest_framework.exceptions import PermissionDenied

        workflow = self.get_object()
        if workflow.dag_published_version_id is None:
            return ErrorResponse(msg='Workflow not published, please publish DAG version first')

        trigger_params = request.data.get('trigger_params') or {}
        fail_strategy = request.data.get('fail_strategy') or None
        trigger_type = request.data.get('trigger_type', 'manual')

        # Dry run (dryrun) uses independent permission workflow: trial_run, but workflow:execute is also allowed (dry run and execution/child set)
        # Other trigger types (manual/schedule/api/link etc.) use workflow:execute
        required_perms = ['workflow:execute']
        if trigger_type == 'dryrun':
            required_perms = ['workflow:trial_run', 'workflow:execute']
        if not getattr(request.user, 'is_superuser', False):
            owner = (
                getattr(workflow, 'creator_id', None) is not None
                and str(workflow.creator_id) == str(getattr(request.user, 'pk', None))
            )
            if not owner and not any(
                SharePermissionChecker.has_perm(request.user, 'workflow', workflow, p, request=request)
                for p in required_perms
            ):
                raise PermissionDenied(f'Missing share permission, need one of: {"/".join(required_perms)}')

        runner = WorkflowRunner()
        try:
            r = runner.trigger_workflow(
                workflow,
                trigger_params=trigger_params,
                trigger_type=trigger_type,
                user_id=request.user.pk,
                fail_strategy=fail_strategy,
            )
        except WorkflowRunnerError as exc:
            return ErrorResponse(msg=str(exc))

        return SuccessResponse(
            data={'execution_id': r.execution_id, 'dag_version_id': r.dag_version_id},
            msg='Workflow triggered',
        )

    # ============================================================
    # Share-related actions
    # ============================================================

    @action(detail=False, methods=['GET'], url_path='perm-defs')
    def perm_defs(self, request):
        """Fetch workflow type permission definition dictionary (grouped by category)"""
        qs = SharePermissionDef.objects.filter(
            resource_type='workflow', is_active=True
        ).order_by('category', 'sort')
        grouped = {}
        category_display_map = dict(SharePermissionDef.CATEGORY_CHOICES)
        for item in qs:
            grouped.setdefault(item.category, []).append(
                SharePermissionDefSerializer(item).data
            )
        data = [
            {
                'category': k,
                'category_display': category_display_map.get(k, k),
                'perms': v,
            }
            for k, v in grouped.items()
        ]
        return SuccessResponse(data=data, msg='Fetched successfully')

    @action(detail=True, methods=['GET', 'POST'], url_path='shares')
    def shares(self, request, pk=None):
        """Shares list/create — [Edition Gate: F_WORKFLOW_SHARING (Double Guard)]"""
        try:
            from taurus_ee.utils.gate import ee_service_or_403
        except ImportError:
            from taurus.ee_fallback import ee_service_or_403
        from taurus.editions.features import F_WORKFLOW_SHARING
        ee_service_or_403('share_service', F_WORKFLOW_SHARING)
        """
        GET: list direct share records for this workflow
        POST: batch create direct shares (supports multiple subjects in one call)
        Required: workflow:manage_share permission
        """
        workflow = self.get_object()
        if not SharePermissionChecker.has_perm(request.user, 'workflow', workflow, 'workflow:manage_share', request=request):
            raise PermissionDenied('You do not have permission to manage workflow share')
        if request.method == 'GET':
            queryset = WorkflowSharePermission.objects.filter(workflow=workflow
            ).order_by('-create_datetime')
            page = self.paginate_queryset(queryset)
            perm_defs = {
                p.perm_code: SharePermissionDefSerializer(p).data
                for p in SharePermissionDef.objects.filter(resource_type='workflow', is_active=True)
            }
            from dvadmin.system.models import Users, Role, Dept
            subject_ids = {'user': [], 'role': [], 'dept': []}
            for sp in queryset:
                subject_ids[sp.subject_type].append(sp.subject_id)
            subject_info_map = {}
            if subject_ids['user']:
                for u in Users.objects.filter(id__in=subject_ids['user']).values('id', 'username', 'name'):
                    subject_info_map[f"user:{u['id']}"] = {
                        'id': u['id'], 'name': u.get('name') or u['username'], 'username': u['username'],
                    }
            if subject_ids['role']:
                for r in Role.objects.filter(id__in=subject_ids['role']).values('id', 'name', 'key'):
                    subject_info_map[f"role:{r['id']}"] = {
                        'id': r['id'], 'name': r['name'], 'key': r.get('key', ''),
                    }
            if subject_ids['dept']:
                for d in Dept.objects.filter(id__in=subject_ids['dept']).values('id', 'name', 'key'):
                    subject_info_map[f"dept:{d['id']}"] = {
                        'id': d['id'], 'name': d['name'], 'dept_code': d.get('key', ''),
                    }
            ctx = {'perm_defs': perm_defs, 'subject_info_map': subject_info_map}
            if page is not None:
                serializer = WorkflowSharePermissionSerializer(page, many=True, context=ctx)
                return self.get_paginated_response(serializer.data)
            serializer = WorkflowSharePermissionSerializer(queryset, many=True, context=ctx)
            return SuccessResponse(data=serializer.data, msg='Fetched successfully')
        # POST: batch create / update_or_create
        ser = SharePermissionBatchCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        vd = ser.validated_data
        created = []
        for sub in vd['subjects']:
            stype = str(sub.get('subject_type', '')).strip()
            sid = str(sub.get('subject_id', '')).strip()
            if not stype or not sid or stype not in ('user', 'role', 'dept'):
                continue
            sname = sub.get('subject_name') or sub.get('name') or ''
            obj, _ = WorkflowSharePermission.objects.update_or_create(
                workflow=workflow, subject_type=stype, subject_id=sid,
                defaults={
                    'subject_name_cache': sname,
                    'permissions': vd['permissions'],
                    'expire_time': vd.get('expire_time'),
                    'remark': vd.get('remark') or '',
                    'grant_user': request.user if getattr(request.user, 'id', None) else None,
                }
            )
            created.append(obj.id)
        return SuccessResponse(data={'created_ids': created}, msg='Share created successfully')

    @action(detail=True, methods=['PUT', 'DELETE'], url_path=r'shares/(?P<share_id>[^/.]+)')
    def share_detail(self, request, pk=None, share_id=None):
        """Modify/delete workflow share — [Edition Gate: F_WORKFLOW_SHARING (Double Guard)]"""
        try:
            from taurus_ee.utils.gate import ee_service_or_403
        except ImportError:
            from taurus.ee_fallback import ee_service_or_403
        from taurus.editions.features import F_WORKFLOW_SHARING
        ee_service_or_403('share_service', F_WORKFLOW_SHARING)
        workflow = self.get_object()
        if not SharePermissionChecker.has_perm(request.user, 'workflow', workflow, 'workflow:manage_share', request=request):
            raise PermissionDenied('You do not have permission to manage workflow share')
        try:
            share = WorkflowSharePermission.objects.get(pk=share_id, workflow=workflow)
        except WorkflowSharePermission.DoesNotExist:
            return ErrorResponse(msg='Share record not found', status=404)
        if request.method == 'DELETE':
            share.delete()
            return SuccessResponse(msg='Deleted successfully')
        # PUT
        perms = request.data.get('permissions')
        if perms is None:
            return ErrorResponse(msg='permissions field is required')
        share.permissions = perms
        share.expire_time = request.data.get('expire_time', share.expire_time)
        share.remark = request.data.get('remark', share.remark)
        share.save()
        return DetailResponse(data=WorkflowSharePermissionSerializer(share).data, msg='Updated successfully')

    @action(detail=True, methods=['GET'], url_path='effective-perms')
    @require_share_perm('workflow:view')
    def effective_perms(self, request, pk=None):
        """Effective permissions — [Edition Gate: F_WORKFLOW_SHARING (Double Guard)]"""
        try:
            from taurus_ee.utils.gate import ee_service_or_403
        except ImportError:
            from taurus.ee_fallback import ee_service_or_403
        from taurus.editions.features import F_WORKFLOW_SHARING
        ee_service_or_403('share_service', F_WORKFLOW_SHARING)
        workflow = self.get_object()
        perms = SharePermissionChecker.get_user_effective_perms(
            request.user, 'workflow', workflow, request=request
        )
        perm_defs = {
            p.perm_code: SharePermissionDefSerializer(p).data
            for p in SharePermissionDef.objects.filter(resource_type='workflow', is_active=True)
        }
        details = [perm_defs[p] for p in perms if p in perm_defs]
        is_owner = bool(getattr(request.user, 'is_superuser', False))
        if not is_owner and workflow.creator_id is not None:
            is_owner = (str(workflow.creator_id) == str(getattr(request.user, 'pk', None)))
        data = {
            'permissions': sorted(list(perms)),
            'details': details,
            'is_owner': is_owner,
        }
        return SuccessResponse(data=data, msg='Fetched successfully')

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        if not request.user.is_superuser:
            if not SharePermissionChecker.has_perm(request.user, "workflow", obj, "workflow:view", request=request):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Missing share permission: workflow:view")
        # 同 list 逻辑:注入 _current_perms 供EditserverPer钮级Permission控制
        self._prefetch_share_info_for_list([obj], request.user, request)
        serializer = self.get_serializer(obj)
        return SuccessResponse(data=serializer.data, msg='Fetched successfully')

    def update(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            obj = self.get_object()
            if not SharePermissionChecker.has_perm(request.user, "workflow", obj, "workflow:edit", request=request):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Missing share permission: workflow:edit")
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            obj = self.get_object()
            if not SharePermissionChecker.has_perm(request.user, "workflow", obj, "workflow:edit", request=request):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Missing share permission: workflow:edit")
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            obj = self.get_object()
            if not SharePermissionChecker.has_perm(request.user, "workflow", obj, "workflow:delete", request=request):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Missing share permission: workflow:delete")
        return super().destroy(request, *args, **kwargs)



def _get_workflow_dag_service_or_none(feature_code: str | None = None):
    """Try to get EE workflow_dag_service; return None if taurus_ee missing / service unregistered / feature gated off.

    Unlike ``ee_service_or_403`` this helper **NEVER** raises ``PermissionDenied`` (HTTP 403).
    Caller is responsible for providing a silent CE-safe fallback so the page keeps rendering.
    """
    try:
        from taurus.editions.loader import has_feature as _has_feature
        if feature_code is not None and not _has_feature(feature_code):
            return None
        from taurus_ee.utils.registry import ee_registry as _ee_registry
    except Exception:
        return None
    try:
        svc = _ee_registry.get_service('workflow_dag_service')
    except Exception:
        svc = None
    return svc if svc is not None else None


def _ce_workflow_stats_response():
    """Workflow list page statistics — lightweight ORM fallback for Community Edition.

    The EE ``workflow_dag_service.get_stats`` computes richer insights; on CE we at least
    match the 8 fields the dashboard stat-cards display so the page paints correctly.
    """
    from django.utils import timezone as _tz
    from taurus.models import Workflow, WorkflowExecution
    from dvadmin.utils.json_response import SuccessResponse

    wf_qs = Workflow.objects.all()
    total = wf_qs.count()
    public_count = wf_qs.filter(share=True).count()
    draft_count = wf_qs.filter(status=0).count()
    published_count = wf_qs.filter(status=1).count()
    pending_approve = wf_qs.filter(status=2).count()
    normal_count = published_count
    dag_count = wf_qs.filter(workflow_mode='dag').count()
    linear_count = wf_qs.filter(workflow_mode='linear').count()

    exec_qs = WorkflowExecution.objects.all()
    total_exec = exec_qs.count()
    today_start = _tz.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_exec = exec_qs.filter(start_time__gte=today_start).count()

    return SuccessResponse(data={
        'total': total,
        'public_count': public_count,
        'draft_count': draft_count,
        'published_count': published_count,
        'pending_approve': pending_approve,
        'approval_pending_count': pending_approve,
        'normal_count': normal_count,
        'today_exec': today_exec,
        'total_exec': total_exec,
        'dag_count': dag_count,
        'linear_count': linear_count,
    })


class WorkflowCategoryViewSet(CustomModelViewSet):
    """流程Category管理(tree形结构, support CRUD + tree interface + initialize默认Category)"""
    queryset = WorkflowCategory.objects.all()
    serializer_class = WorkflowCategorySerializer
    create_serializer_class = WorkflowCategoryCreateSerializer
    update_serializer_class = WorkflowCategoryUpdateSerializer
    search_fields = ['name', 'category_code']
    filterset_fields = ['parent', 'is_system']
    ordering_fields = ['sort', 'id', 'create_datetime']
    ordering = ['sort', 'id']

    import_field_dict = {
        'name': 'Category name',
        'parent': 'Parent category',
        'category_code': 'Category code',
        'sort': 'Sort order',
        'remark': 'Remark',
    }
    import_serializer_class = WorkflowCategoryCreateSerializer
    update_template_serializer_class = WorkflowCategorySerializer
    export_field_label = {
        'name': 'Category name',
        'category_code': 'Category code',
        'sort': 'Sort order',
        'is_system': 'Is system built-in',
        'workflow_count': 'Workflow count',
        'remark': 'Remark',
        'create_datetime': 'Created time',
    }
    export_serializer_class = WorkflowCategorySerializer

    def get_serializer_class(self):
        if self.action == 'create':
            return WorkflowCategoryCreateSerializer
        if self.action == 'update' or self.action == 'partial_update':
            return WorkflowCategoryUpdateSerializer
        return WorkflowCategorySerializer

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            tree = self.build_tree(serializer.data)
            return self.get_paginated_response(tree)
        serializer = self.get_serializer(queryset, many=True)
        tree = self.build_tree(serializer.data)
        return SuccessResponse(data=tree, msg="Retrieved successfully")

    def build_tree(self, data):
        data_list = list(data)
        item_map = {item['id']: item for item in data_list}
        result = []
        for item in data_list:
            parent = item.get('parent')
            if parent is None:
                result.append(item)
                continue
            parent_id = parent
            if hasattr(parent, 'id'):
                parent_id = parent.id
            try:
                parent_id = int(parent_id)
            except (TypeError, ValueError):
                result.append(item)
                continue
            parent_item = item_map.get(parent_id)
            if parent_item:
                if 'children' not in parent_item:
                    parent_item['children'] = []
                parent_item['children'].append(item)
            else:
                result.append(item)
        return result

    @action(detail=False, methods=['get'], url_path='tree')
    def get_tree(self, request, *args, **kwargs):
        """Fetch完整tree形结构(不Pagination).returnFrontend左侧tree结构, 首 节.为虚拟/「All流程」"""
        queryset = self.filter_queryset(self.get_queryset()).order_by('sort', 'id')
        serializer = WorkflowCategorySerializer(queryset, many=True, context={'request': request})
        category_list = serializer.data

        item_map = {}
        for item in category_list:
            item['parent'] = item.get('parent')
            item['children'] = []
            item_map[item['id']] = item

        def _get_all_child_ids(cat_id):
            ids = [cat_id]
            for item in category_list:
                if item['parent'] == cat_id:
                    ids.extend(_get_all_child_ids(item['id']))
            return ids

        from taurus.models import Workflow
        from taurus.utils.share_permission import ShareVisibleQS
        from django.db.models import Q as _Q
        user = request.user
        count_wf_qs = Workflow.objects.all()
        # Categorytree统计口径:只统计「我create/ + Public/」, 分享给我/流程不计入Category计数
        # (与list「All」Tab, ScriptlibraryCategorytree统计口径保持一致)
        if not getattr(user, 'is_superuser', False):
            q_count = _Q()
            if hasattr(user, 'id'):
                q_count |= _Q(creator_id=user.id)
            q_count |= _Q(auth_type='public')
            count_wf_qs = count_wf_qs.filter(q_count).distinct()
        total_count = count_wf_qs.count()
        for item in category_list:
            child_ids = _get_all_child_ids(item['id'])
            item['workflow_count'] = count_wf_qs.filter(category_id__in=child_ids).count()

        tree_roots = []
        for item in category_list:
            parent_id = item.get('parent')
            if parent_id is None:
                tree_roots.append(item)
            elif parent_id in item_map:
                item_map[parent_id]['children'].append(item)
            else:
                tree_roots.append(item)

        result = [{
            'id': 'all',
            'name': 'All workflows',
            'category_code': 'all',
            'sort': 0,
            'is_system': True,
            'remark': 'Virtual root showing all workflows',
            'workflow_count': total_count,
            'children': tree_roots,
            'virtual_root': True,
        }]
        return SuccessResponse(data=result, msg="Retrieved successfully")

    @action(detail=False, methods=['post'], url_path='ensure-defaults')
    def ensure_defaults(self, request, *args, **kwargs):
        """确保默认流程Category存在(initializeinterface, 可重复调用Idempotency)"""
        defaults = [
            ('Release & Deploy', 'deploy', 10, None, 'App release, config push workflows'),
            ('App Release', 'deploy-app', 10, 'deploy', 'App code release and deploy'),
            ('Config Push', 'deploy-config', 20, 'deploy', 'Config file distribution'),
            ('Inspect & Ops', 'inspect', 20, None, 'Daily inspect and ops workflows'),
            ('Scale Out/In', 'expand', 30, None, 'Node/instance scaling workflows'),
            ('DR Switch', 'dr', 40, None, 'Disaster recovery workflows'),
            ('Env Init', 'init', 50, None, 'New environment init, baseline config workflows'),
        ]
        created_count = 0
        code_to_obj = {}
        for name, code, sort, parent_code, remark in defaults:
            if WorkflowCategory.objects.filter(category_code=code).exists():
                obj = WorkflowCategory.objects.get(category_code=code)
                code_to_obj[code] = obj
                continue
            parent = code_to_obj.get(parent_code) if parent_code else None
            obj = WorkflowCategory.objects.create(
                name=name,
                category_code=code,
                sort=sort,
                is_system=True,
                remark=remark,
                parent=parent,
            )
            code_to_obj[code] = obj
            created_count += 1
        return SuccessResponse(
            data={'created': created_count, 'total': WorkflowCategory.objects.count()},
            msg=f'Default categories initialized, {created_count} created'
        )

    def perform_destroy(self, instance):
        if instance.is_system:
            from rest_framework.exceptions import ValidationError
            raise ValidationError("System category cannot be deleted")
        child_count = WorkflowCategory.objects.filter(parent=instance).count()
        if child_count > 0:
            from rest_framework.exceptions import ValidationError
            raise ValidationError(f"Has {child_count} child categories, please delete them first")
        workflow_count = Workflow.objects.filter(category=instance).count()
        if workflow_count > 0:
            from rest_framework.exceptions import ValidationError
            raise ValidationError(f"Category has {workflow_count} workflows, please remove them first")
        return super().perform_destroy(instance)


from rest_framework import serializers as _wf_approve_drf_sz
from taurus.models import WorkflowApprovalInstance as _WFApproveInstModel


class WorkflowApproveCompatSerializer(CustomModelSerializer):
    """兼容旧Approval中心面:use新Model WorkflowApprovalInstance, 但OutputField名与旧 WorkflowApproveSerializer 一致"""

    workflow_name = _wf_approve_drf_sz.CharField(source='workflow.name', read_only=True, default='')
    submitter_name_display = _wf_approve_drf_sz.CharField(source='submitter.username', read_only=True, default='')
    approver = _wf_approve_drf_sz.SerializerMethodField()
    approver_name = _wf_approve_drf_sz.SerializerMethodField()
    approver_name_display = _wf_approve_drf_sz.SerializerMethodField()
    approve_time = _wf_approve_drf_sz.SerializerMethodField()
    approve_reason = _wf_approve_drf_sz.SerializerMethodField()
    status_display = _wf_approve_drf_sz.SerializerMethodField()
    risk_level_display = _wf_approve_drf_sz.SerializerMethodField()
    workflow_creator = _wf_approve_drf_sz.CharField(source='workflow.creator.username', read_only=True, default='')
    workflow_auth_type = _wf_approve_drf_sz.CharField(source='workflow.auth_type', read_only=True, default='')
    workflow_category_name = _wf_approve_drf_sz.SerializerMethodField()
    creator = _wf_approve_drf_sz.SerializerMethodField()
    modifier = _wf_approve_drf_sz.SerializerMethodField()
    status = _wf_approve_drf_sz.SerializerMethodField()
    workflow = _wf_approve_drf_sz.SerializerMethodField()
    category_name = _wf_approve_drf_sz.SerializerMethodField()
    auth_type = _wf_approve_drf_sz.SerializerMethodField()
    candidate_approvers = _wf_approve_drf_sz.SerializerMethodField()

    class Meta:
        model = _WFApproveInstModel
        fields = [
            'id', 'workflow', 'workflow_name', 'workflow_version',
            'risk_level', 'risk_level_display', 'risk_points',
            'status', 'status_display',
            'submitter', 'submitter_name', 'submitter_name_display',
            'submit_desc',
            'approver', 'approver_name', 'approver_name_display',
            'approve_time', 'approve_reason',
            'workflow_creator', 'workflow_auth_type', 'workflow_category_name',
            'category_name', 'auth_type',
            'candidate_approvers',
            'create_datetime', 'update_datetime', 'creator', 'modifier',
        ]
        read_only_fields = ['id', 'create_datetime', 'update_datetime']

    def get_workflow(self, obj):
        wf = getattr(obj, 'workflow', None)
        if wf is None:
            return None
        cat_name = '-'
        cat_id = getattr(wf, 'category_id', None)
        if getattr(wf, 'category', None):
            cat_name = getattr(wf.category, 'name', '-')
        creator_name = ''
        if getattr(wf, 'creator', None):
            creator_name = getattr(wf.creator, 'username', '') or ''
        return {
            'id': getattr(wf, 'id', None),
            'name': getattr(wf, 'name', ''),
            'category_name': cat_name,
            'category_id': cat_id,
            'auth_type': getattr(wf, 'auth_type', ''),
            'creator': creator_name,
            'workflow_mode': getattr(wf, 'workflow_mode', ''),
            'exec_count': getattr(wf, 'exec_count', 0) or 0,
            'last_exec_time': getattr(wf, 'last_exec_time', None),
            'hosts_count': getattr(wf, 'hosts_count', None),
        }

    def get_category_name(self, obj):
        wf = getattr(obj, 'workflow', None)
        if wf and getattr(wf, 'category', None):
            return getattr(wf.category, 'name', '-')
        return '-'

    def get_auth_type(self, obj):
        wf = getattr(obj, 'workflow', None)
        return getattr(wf, 'auth_type', '') or ''

    def _resolve_last_record(self, obj):
        from taurus.models import WorkflowApprovalNodeExecution
        nodes = list(WorkflowApprovalNodeExecution.objects.filter(instance=obj).order_by('step_order', 'id'))
        last_record = None
        last_node_time = None
        for n in nodes:
            records = list(n.approval_records or [])
            for r in records:
                if r.get('action') in ('approve', 'reject'):
                    last_record = r
                    last_node_time = getattr(n, 'finish_time', None)
        return last_record, last_node_time

    def get_status(self, obj):
        raw = getattr(obj, 'status', '')
        if raw in ('pending', 'approving'):
            return 'pending'
        if raw in ('approved', 'completed'):
            return 'approved'
        if raw in ('rejected', 'revoked'):
            return 'rejected'
        return raw or ''

    def get_status_display(self, obj):
        st = self.get_status(obj)
        return {'pending': 'Pending', 'approved': 'Approved', 'rejected': 'Rejected'}.get(st, st or '')

    def get_risk_level_display(self, obj):
        v = getattr(obj, 'risk_level', '') or ''
        return {'low': 'Low', 'medium': 'Medium', 'high': 'High'}.get(v, v or '')

    def get_workflow_category_name(self, obj):
        wf = getattr(obj, 'workflow', None)
        if wf and getattr(wf, 'category', None):
            return getattr(wf.category, 'name', '-')
        return '-'

    def _to_user_id(self, val):
        if val is None:
            return None
        if isinstance(val, int):
            return val
        try:
            return int(val)
        except Exception:
            return None

    def get_approver(self, obj):
        last_record, _ = self._resolve_last_record(obj)
        if last_record:
            return self._to_user_id(last_record.get('user_id'))
        return None

    def get_approver_name(self, obj):
        last_record, _ = self._resolve_last_record(obj)
        if last_record:
            return last_record.get('username') or ''
        return ''

    def get_approver_name_display(self, obj):
        return self.get_approver_name(obj)

    def get_approve_time(self, obj):
        from django.utils.dateparse import parse_datetime as _parse_dt
        last_record, node_finish = self._resolve_last_record(obj)
        if last_record:
            t = last_record.get('operate_time') or None
            if t:
                try:
                    return _parse_dt(str(t)) or node_finish
                except Exception:
                    return node_finish
            return node_finish
        if getattr(obj, 'finish_time', None):
            return obj.finish_time
        return None

    def get_approve_reason(self, obj):
        last_record, _ = self._resolve_last_record(obj)
        if last_record:
            return last_record.get('reason') or ''
        return ''

    def get_creator(self, obj):
        return self._to_user_id(getattr(obj, 'submitter_id', None) or getattr(obj, 'submitter', None) and obj.submitter_id)

    def get_modifier(self, obj):
        last_record, _ = self._resolve_last_record(obj)
        if last_record:
            return self._to_user_id(last_record.get('user_id'))
        return self.get_creator(obj)

    def get_candidate_approvers(self, obj):
        """FetchcurrentPending approval节./候选Approverlist"""
        from taurus.models import WorkflowApprovalNodeExecution
        if obj.status not in ('pending', 'approving'):
            return []
        nodes = list(
            WorkflowApprovalNodeExecution.objects.filter(instance=obj, status='pending').order_by('step_order')
        )
        if not nodes:
            return []
        current = None
        try:
            current = nodes[getattr(obj, 'current_node_index', 0)]
        except Exception:
            current = nodes[0] if nodes else None
        if current:
            return current.candidate_approvers or []
        return []


# ---------------------------------------------------------------------------
# [M2.2 Wrapper] Workflow approval ViewSets & Engines (EE 专属实现移入 taurus_ee.*)
# Thin Wrapper: basename/path 100% 不变；外层再挂 @require_feature Double Gate。
# ---------------------------------------------------------------------------
from django.utils.decorators import method_decorator as _wfapp_method_decorator
from taurus.editions.loader import require_feature as _wfapp_require_feature
from taurus.editions.features import F_WORKFLOW_APPROVAL_FLOW as _F_WFAPP

try:
    from taurus_ee.views.workflow_approval_views import (
        WorkflowApproveViewSet as _EEWorkflowApproveViewSet,
        WorkflowApprovalRuleViewSet as _EEWorkflowApprovalRuleViewSet,
        WorkflowApprovalRuleNodeViewSet as _EEWorkflowApprovalRuleNodeViewSet,
        WorkflowApprovalInstanceViewSet as _EEWorkflowApprovalInstanceViewSet,
    )
    from taurus_ee.services.workflow_approval_engine import (
        WorkflowApprovalRuleMatcher as _EEWorkflowApprovalRuleMatcher,
        WorkflowApprovalFlowEngine as _EEWorkflowApprovalFlowEngine,
    )
    _WFAPP_EE_OK = True
except ImportError:
    # taurus_ee 物理剥离后的 fallback stub：让 class 定义不崩溃。
    # 运行时调用路径已被 @require_feature(_F_WFAPP) 拦截，不会真正走进这些类。
    from taurus.ee_fallback import _EEFallbackViewSet as _FBVS
    class _EEWorkflowApproveViewSet(_FBVS): pass
    class _EEWorkflowApprovalRuleViewSet(_FBVS): pass
    class _EEWorkflowApprovalRuleNodeViewSet(_FBVS): pass
    class _EEWorkflowApprovalInstanceViewSet(_FBVS): pass
    class _EEWorkflowApprovalRuleMatcher: pass  # Engine 非 ViewSet，空 stub 足够
    class _EEWorkflowApprovalFlowEngine: pass
    _WFAPP_EE_OK = False


@_wfapp_method_decorator(_wfapp_require_feature(_F_WFAPP), name='dispatch')
class WorkflowApproveViewSet(_EEWorkflowApproveViewSet):
    """Thin wrapper — EE implementation in taurus_ee.views.workflow_approval_views"""
    pass


@_wfapp_method_decorator(_wfapp_require_feature(_F_WFAPP), name='dispatch')
class WorkflowApprovalRuleViewSet(_EEWorkflowApprovalRuleViewSet):
    """Thin wrapper — EE implementation in taurus_ee.views.workflow_approval_views"""
    pass


@_wfapp_method_decorator(_wfapp_require_feature(_F_WFAPP), name='dispatch')
class WorkflowApprovalRuleNodeViewSet(_EEWorkflowApprovalRuleNodeViewSet):
    """Thin wrapper — EE implementation in taurus_ee.views.workflow_approval_views"""
    pass


@_wfapp_method_decorator(_wfapp_require_feature(_F_WFAPP), name='dispatch')
class WorkflowApprovalInstanceViewSet(_EEWorkflowApprovalInstanceViewSet):
    """Thin wrapper — EE implementation in taurus_ee.views.workflow_approval_views"""
    pass


# Engine Thin Wrapper（保持 taurus.views.WorkflowApprovalFlowEngine 名字不变）
class WorkflowApprovalRuleMatcher(_EEWorkflowApprovalRuleMatcher):
    """Thin wrapper — EE implementation in taurus_ee.services.workflow_approval_engine"""
    pass


class WorkflowApprovalFlowEngine(_EEWorkflowApprovalFlowEngine):
    """Thin wrapper — EE implementation in taurus_ee.services.workflow_approval_engine"""
    pass


# Placeholder — 原 taurus/views.py L9178-L9833 段（审批 VS + 2 Engine）已迁移到 taurus_ee.*
# Placeholder — 原 taurus/views.py L9062-L9178 段（RuleMatcher） 已迁移到 taurus_ee.services.workflow_approval_engine
# Placeholder — 原 taurus/views.py L1903-L2106 段（WorkflowApproveViewSet 兼容旧中心）已迁移到 taurus_ee.views.workflow_approval_views


class WorkflowExecutionViewSet(CustomModelViewSet):
    """WorkflowExecutionrecord管理"""
    queryset = WorkflowExecution.objects.all()
    serializer_class = WorkflowExecutionSerializer
    filterset_fields = ['workflow', 'status', 'trigger_type', 'creator']
    search_fields = ['workflow__name', 'creator__username']
    ordering_fields = ['start_time', 'create_datetime']
    ordering = ['-create_datetime']

    def get_serializer_class(self):
        if self.action == 'list':
            from taurus.serializers import WorkflowExecutionListSerializer
            return WorkflowExecutionListSerializer
        return super().get_serializer_class()

    def get_queryset(self):
        """
        除默认 filterset_fields 外, 额外supportFrontendConvention/scale outFilterParameters:
          - workflow_name__icontains:Per流程Name模糊Search(对应 workflow.name)
          - creator__name__icontains:PerExecution人Name模糊Search(对应 creator.username)
          - start_time__gte / start_time__lte:ExecutionStart time范围
          - trigger_type__ne:排除指定Trigger type(例如选"Execution status"时自动排除Dry run dryrun, 
            保证表格entries数与"Success/Running"卡片数字一致)
        这些 lookup cannotvia dvadmin / AutoFilterSet 直接Generate, therefore在 get_queryset 中
        read request.query_params 自行Filter;simultaneouslykeep非Administrator/可见性控制(仅自己create/ + PublicWorkflow/Execution).
        """
        from django.db import models as _m
        qs = super().get_queryset().select_related('workflow', 'creator')
        user = getattr(self.request, 'user', None)
        if user and not getattr(user, 'is_superuser', False):
            qs = qs.filter(
                _m.Q(creator=user) | _m.Q(workflow__auth_type='public')
            ).distinct()
        params = getattr(self.request, 'query_params', {})

        v = params.get('workflow_name__icontains')
        if v:
            qs = qs.filter(workflow__name__icontains=v)

        v = params.get('creator__name__icontains')
        if v:
            qs = qs.filter(
                _m.Q(creator__username__icontains=v) | _m.Q(creator__first_name__icontains=v) | _m.Q(creator__last_name__icontains=v)
            )

        v = params.get('start_time__gte')
        if v:
            qs = qs.filter(start_time__gte=v)
        v = params.get('start_time__lte')
        if v:
            qs = qs.filter(start_time__lte=v)

        v = params.get('trigger_type__ne')
        if v:
            qs = qs.exclude(trigger_type=v)

        return qs

    def _light_advance_running_executions(self, executions, user_id=None):
        """list/Detail query后/轻量兜底Advance:对return结果中处于 RUNNING 且仍有 RUNNING 节./Execution, 
        顺手做一次 advance_workflow tick, Avoid遗漏Dispatch导致Status停在 RUNNING.

        只在 HTTP response前"顺手"做一次, 不保证覆盖All RUNNING, actually/实时Advance由
        websocket_async /Callbackpath保证.
        """
        if not executions:
            return
        from taurus.workflow.models import WorkflowNodeExecution
        from taurus.workflow.engine.executor import STATUS_RUNNING
        from taurus.workflow.engine.runner import WorkflowRunner
        runner = WorkflowRunner()
        max_advance = 5
        handled = 0
        for exec_obj in executions:
            if getattr(exec_obj, 'status', 1) != 1:
                continue
            if handled >= max_advance:
                break
            try:
                running_count = WorkflowNodeExecution.objects.filter(
                    execution_id=exec_obj.pk, status=STATUS_RUNNING,
                ).count()
                if running_count <= 0:
                    continue
                try:
                    runner.advance_workflow(execution_id=exec_obj.pk, user_id=user_id)
                    handled += 1
                except Exception:
                    continue
            except Exception:
                continue

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            running_candidates = [e for e in page if getattr(e, 'status', 0) == 1][:5]
            if running_candidates:
                try:
                    self._light_advance_running_executions(
                        running_candidates, user_id=getattr(request.user, 'id', None),
                    )
                    # advance may把 DB 行Status改了, 这里re-RefreshPaginationobject里/objectField(不重查, 
                    # 只对已在 page 里/Execution做 refresh_from_db, 开销小)
                    for e in running_candidates:
                        try:
                            e.refresh_from_db()
                        except Exception:
                            continue
                except Exception:
                    pass
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        data_list = list(queryset)
        running_candidates = [e for e in data_list if getattr(e, 'status', 0) == 1][:5]
        if running_candidates:
            try:
                self._light_advance_running_executions(
                    running_candidates, user_id=getattr(request.user, 'id', None),
                )
                for e in running_candidates:
                    try:
                        e.refresh_from_db()
                    except Exception:
                        continue
            except Exception:
                pass
        serializer = self.get_serializer(data_list, many=True)
        return SuccessResponse(data=serializer.data, msg='Fetched successfully')
    
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """CancelExecution"""
        execution = self.get_object()
        if execution.status != 1:
            return ErrorResponse(msg="Only running workflows can be cancelled")

        execution.status = 4
        execution.end_time = timezone.now()
        execution.save()

        execution.step_executions.filter(status=0).update(status=4)

        try:
            from taurus.workflow.models import WorkflowNodeExecution
            WorkflowNodeExecution.objects.filter(
                execution_id=execution.pk,
                status__in=[0, 1],
            ).update(status=4, end_time=timezone.now())
        except Exception:
            pass

        return SuccessResponse(msg="WorkflowCancelled")
    
    @action(detail=True, methods=['post'])
    def advance(self, request, pk=None):
        """Advance DAG WorkflowExecution一步(poll + dispatch + aggregate)"""
        from taurus.workflow.engine.runner import WorkflowRunner, WorkflowRunnerError

        execution = self.get_object()
        if execution.status not in (1,):
            return ErrorResponse(msg='Workflow is not running, cannot advance')

        runner = WorkflowRunner()
        try:
            tick = runner.advance_workflow(execution.pk, user_id=request.user.pk)
        except WorkflowRunnerError as exc:
            return ErrorResponse(msg=str(exc))

        return SuccessResponse(data={
            'execution_id': tick.execution_id,
            'polled': tick.polled,
            'newly_completed': tick.newly_completed,
            'newly_dispatched': tick.newly_dispatched,
            'newly_skipped': tick.newly_skipped,
            'finished': tick.finished,
        }, msg='Advance completed')

    @action(detail=True, methods=['get'])
    def node_executions(self, request, pk=None):
        """Fetch DAG WorkflowRunning所有节.ExecutionDetail"""
        from taurus.workflow.models import WorkflowNodeExecution
        from taurus.serializers import WorkflowNodeExecutionSerializer

        execution = self.get_object()
        rows = WorkflowNodeExecution.objects.filter(
            execution_id=execution.pk,
        ).order_by('node_key', '-attempt_no')
        serializer = WorkflowNodeExecutionSerializer(rows, many=True)
        return SuccessResponse(data=serializer.data)

    @action(detail=True, methods=['get'])
    def detail_info(self, request, pk=None):
        """FetchExecutionDetail(附带轻量 advance Polling兜底:若仍有 running 节., 自动做一次 tick, 
        解决Dispatchserver没start/漏扫时 wait 节.醒了但永远 running /糟糕体验)"""
        execution = self.get_object()
        # 轻量Polling兜底:仅when execution 本身仍在runStatus时Trigger, AvoidPerformance开销
        if getattr(execution, 'status', 1) == 1:
            try:
                from taurus.workflow.models import WorkflowNodeExecution
                from taurus.workflow.engine.executor import STATUS_RUNNING
                running_count = WorkflowNodeExecution.objects.filter(
                    execution_id=execution.pk, status=STATUS_RUNNING
                ).count()
                if running_count > 0:
                    try:
                        from taurus.workflow.engine.runner import WorkflowRunner
                        runner = WorkflowRunner()
                        runner.advance_workflow(
                            execution_id=execution.pk,
                            user_id=getattr(request.user, 'id', None),
                        )
                        execution.refresh_from_db()
                    except Exception:
                        pass
            except Exception:
                pass
        serializer = WorkflowExecutionSerializer(execution)
        return SuccessResponse(data=serializer.data)

    @action(detail=True, methods=['post'], url_path='retry-node/(?P<node_key>[^/.]+)')
    def retry_node(self, request, pk=None, node_key=None):
        """retry指定节.:将节.StatusReset为 pending, Engine下次 tick 会re- dispatch"""
        from taurus.workflow.models import WorkflowNodeExecution

        execution = self.get_object()
        if execution.status != 1:
            return ErrorResponse(msg='Workflow is not running')

        rows = WorkflowNodeExecution.objects.filter(
            execution_id=execution.pk,
            node_key=node_key,
            status__in=[3, 5],
        )
        if not rows.exists():
            return ErrorResponse(msg=f'Node {node_key} not found or not retryable')

        updated = rows.update(
            status=0,
            error_message='',
            exit_code=None,
            finished_at=None,
            started_at=None,
            output={},
            output_refs=[],
            adapter_state={},
            poll_count=0,
        )
        return SuccessResponse(msg=f'Node {node_key} reset to pending ({updated} records)')

    @action(detail=True, methods=['post'], url_path='skip-node/(?P<node_key>[^/.]+)')
    def skip_node(self, request, pk=None, node_key=None):
        """Skip指定节.:将 pending 节.标记为 skipped"""
        from taurus.workflow.models import WorkflowNodeExecution

        execution = self.get_object()
        if execution.status != 1:
            return ErrorResponse(msg='Workflow is not running')

        rows = WorkflowNodeExecution.objects.filter(
            execution_id=execution.pk,
            node_key=node_key,
            status=0,
        )
        if not rows.exists():
            return ErrorResponse(msg=f'Node {node_key} not found or not pending')

        now = timezone.now()
        updated = rows.update(status=5, finished_at=now)
        return SuccessResponse(msg=f'Node {node_key} skipped ({updated} records)')

    @action(detail=True, methods=['post'], url_path='approve-node/(?P<node_key>[^/.]+)')
    def approve_node(self, request, pk=None, node_key=None):
        """Approval passed指定节.:将 pending /Approval节.标记为Success"""
        from taurus.workflow.models import WorkflowNodeExecution

        execution = self.get_object()
        if execution.status != 1:
            return ErrorResponse(msg='Workflow is not running')

        rows = WorkflowNodeExecution.objects.filter(
            execution_id=execution.pk,
            node_key=node_key,
            status=0,
            node_type='approval',
        )
        if not rows.exists():
            return ErrorResponse(msg=f'Approval node {node_key} not found or not pending')

        now = timezone.now()
        comment = request.data.get('comment', '')
        updated = rows.update(
            status=2,
            finished_at=now,
            output={'approved': True, 'comment': comment},
        )
        return SuccessResponse(msg=f'Approval node {node_key} approved ({updated} records)')

    @action(detail=True, methods=['post'])
    def rerun(self, request, pk=None):
        """重跑WorkflowExecution

        mode:
          - full: 完全重跑(新建一entriesExecution instance, 复用原 workflow, Parameters, trigger_type 等)
          - failed_only: 只重跑Failed/Skip/终止节., 在current execution 内Reset对应节.为 pending, 
                         并把 execution 整体置为 running, 然后立即做一次 tick Advance.

        对于SuccessStatus/Execution, 推荐use full;对于FailedStatus/Execution, User可Per需Select.

        Note:ifWorkflow已ReleaseUpdate/ DAG Version, 原Version/Execution将不allow重跑, 
        需based on最新VersionManual trigger新Execution.
        """
        from taurus.workflow.models import WorkflowNodeExecution
        from taurus.workflow.engine.runner import WorkflowRunner

        execution = self.get_object()
        if execution.status == 1:
            return ErrorResponse(msg='Running workflow does not need rerun')

        workflow = execution.workflow
        if getattr(workflow, 'dag_published_version_id', None) is None:
            return ErrorResponse(msg='Workflow not published, cannot rerun')

        # VersionConsistencyCheck:Execution时/ dag Versionmust等于 workflow current最新ReleaseVersion
        exec_vid = getattr(execution, 'dag_version_id', None)
        latest_vid = workflow.dag_published_version_id
        if exec_vid is not None and latest_vid is not None and exec_vid != latest_vid:
            exec_ver_num = None
            latest_ver_num = None
            try:
                if exec_vid:
                    from taurus.workflow.models import WorkflowDAGVersion
                    ver_row = WorkflowDAGVersion.objects.filter(pk=exec_vid).values('version').first()
                    if ver_row:
                        exec_ver_num = ver_row['version']
            except Exception:
                pass
            try:
                if latest_vid:
                    from taurus.workflow.models import WorkflowDAGVersion as _V
                    lv = _V.objects.filter(pk=latest_vid).values('version').first()
                    if lv:
                        latest_ver_num = lv['version']
            except Exception:
                pass
            if exec_ver_num is not None and latest_ver_num is not None:
                msg = (
                    f'Execution based on DAG v{exec_ver_num}, '
                    f'workflow has published v{latest_ver_num}, rerun of old version not allowed.'
                    f'Please start new execution from workflow detail page.'
                )
            else:
                msg = (
                    'DAG version mismatch, rerun of old version not allowed.'
                    'Please start new execution from workflow detail page.'
                )
            return ErrorResponse(msg=msg, data={
                'code': 'DAG_VERSION_NOT_LATEST',
                'execution_dag_version_id': exec_vid,
                'latest_dag_version_id': latest_vid,
                'execution_dag_version': exec_ver_num,
                'latest_dag_version': latest_ver_num,
            })

        mode = request.data.get('mode', 'full')
        if mode not in ('full', 'failed_only'):
            return ErrorResponse(msg='Invalid mode, only full or failed_only supported')

        runner = WorkflowRunner()

        if mode == 'full':
            # 方式一:create全newExecution instance, 复用上一次/ trigger_params / trigger_type / fail_strategy 等
            trigger_params = (execution.context or {}).get('trigger_params') or {}
            trigger_type = execution.trigger_type if execution.trigger_type else 'manual'
            fail_strategy = getattr(execution, 'fail_strategy', 'fail_fast') or 'fail_fast'
            # Dry run/重跑仍保持Dry run
            if getattr(execution, 'is_dryrun', False):
                trigger_type = 'dryrun'
            result = runner.trigger_workflow(
                workflow,
                trigger_params=trigger_params,
                trigger_type=trigger_type,
                user_id=request.user.pk,
                fail_strategy=fail_strategy,
            )
            return SuccessResponse(data={
                'mode': 'full',
                'new_execution_id': result.execution_id,
            }, msg='Full rerun initiated')

        # 方式二:failed_only —— 在current execution 上ResetFailed/Skip节.
        now = timezone.now()
        # 1) Reset execution 本身为 running
        execution.status = 1
        execution.end_time = None
        execution.error_message = ''
        execution.save(update_fields=['status', 'end_time', 'error_message', 'update_datetime'])
        # 2) Reset所有 status in [Failed(3), Skip(5), Terminated(4)] /节.为 pending, 并清EmptyFailedMessage
        node_updated = WorkflowNodeExecution.objects.filter(
            execution_id=execution.pk,
            status__in=[3, 4, 5],
        ).update(
            status=0,
            error_message='',
            exit_code=None,
            finished_at=None,
            started_at=None,
            output={},
            output_refs=[],
            adapter_state={},
            poll_count=0,
            update_datetime=now,
        )
        # 3) 同步 step_executions
        try:
            execution.step_executions.filter(status__in=[3, 4, 5]).update(
                status=0,
                end_time=None,
                error_message='',
                output='',
            )
        except Exception:
            pass
        # 4) 立即做一次 advance Advance
        try:
            runner.advance_workflow(execution.pk, user_id=request.user.pk)
        except Exception:
            pass
        return SuccessResponse(data={
            'mode': 'failed_only',
            'execution_id': execution.pk,
            'reset_node_count': node_updated,
        }, msg=f'Reset  {node_updated}  failed nodes and resumed execution')


class ScheduleViewSet(CustomModelViewSet):
    """Scheduled task管理"""
    queryset = Schedule.objects.all()
    serializer_class = ScheduleSerializer
    search_fields = ['name', 'description']
    filterset_fields = ['status', 'schedule_type', 'target_type']
    ordering_fields = ['create_datetime', 'last_run_time', 'next_run_time']
    ordering = ['-create_datetime']
    
    def get_serializer_class(self):
        if self.action == 'list':
            return ScheduleListSerializer
        return ScheduleSerializer
    
    def perform_create(self, serializer):
        """createScheduled task时Register到 Celery Beat"""
        # === 配额校验：ScriptTask + Schedule 总数 ===
        from taurus.editions.loader import check_quota as _check_quota
        from taurus.models import ScriptTask as _ScriptTask
        _total = Schedule.objects.count() + _ScriptTask.objects.count()
        _check_quota('max_scheduled_tasks', _total, '定时调度任务')
        instance = serializer.save()
        self._register_celery_task(instance)
    
    def perform_update(self, serializer):
        """UpdateScheduled task时re-Register到 Celery Beat"""
        instance = serializer.save()
        self._register_celery_task(instance)
    
    def perform_destroy(self, instance):
        """DeleteScheduled task时从 Celery Beat Deregister"""
        self._unregister_celery_task(instance)
        instance.delete()
    
    def _register_celery_task(self, schedule):
        """RegisterScheduled task到 Celery Beat(Proxy到Modules级function)"""
        register_schedule_to_celery(schedule)

    def _unregister_celery_task(self, schedule):
        """从 Celery Beat DeregisterScheduled task(Proxy到Modules级function)"""
        unregister_schedule_from_celery(schedule)
    
    @action(detail=True, methods=['post'])
    def enable(self, request, pk=None):
        """EnableScheduled task"""
        schedule = self.get_object()
        schedule.status = 1
        schedule.save()
        self._register_celery_task(schedule)
        return SuccessResponse(msg="Scheduled task enabled")
    
    @action(detail=True, methods=['post'])
    def disable(self, request, pk=None):
        """DisableScheduled task"""
        schedule = self.get_object()
        schedule.status = 0
        schedule.save()
        self._unregister_celery_task(schedule)
        return SuccessResponse(msg="Scheduled taskDisabled")
    
    @action(detail=True, methods=['post'])
    def run_now(self, request, pk=None):
        """立即ExecutionScheduled task"""
        schedule = self.get_object()
        
        try:
            from taurus.tasks import execute_schedule_task
            # 异步Execution
            execute_schedule_task.delay(schedule.id)
            return SuccessResponse(msg="Execution queued")
        except Exception as e:
            return ErrorResponse(msg=f"Execution failed: {str(e)}")
    
    @action(detail=True, methods=['get'])
    def executions(self, request, pk=None):
        """FetchScheduled taskExecutionrecord"""
        schedule = self.get_object()
        executions = schedule.executions.all()[:50]  # 最近50entries
        serializer = ScheduleExecutionSerializer(executions, many=True)
        return SuccessResponse(data=serializer.data)

    # ===================================================================
    # [M2.3 EE] 统一调度中心 (SCRIPT_TASK_UNIFIED) / HA 告警 (SCHEDULE_ALERT_RETRY)
    # Double Guard: action 体首行 ee_service_or_403，CE 直接 403。
    # ===================================================================
    @action(detail=False, methods=['get'], url_path='unified-list')
    def unified_list(self, request):
        """[EE] 双轨（Schedule × ScriptTask）合并列表."""
        try:
            from taurus_ee.utils.gate import ee_service_or_403
        except ImportError:
            from taurus.ee_fallback import ee_service_or_403
        from taurus.editions.features import F_SCRIPT_TASK_UNIFIED
        svc_cls = ee_service_or_403('scheduler_unified_service', F_SCRIPT_TASK_UNIFIED)
        items, total = svc_cls.unified_list(self, request)
        return SuccessResponse(data={
            'results': items,
            'total': total,
        }, msg='Unified schedule list (EE)')

    @action(detail=False, methods=['post'], url_path='trigger-now')
    def trigger_now(self, request):
        """[EE] 双轨通用立刻执行入口：body = {source: 'schedule'|'script_task', pk: int}."""
        try:
            from taurus_ee.utils.gate import ee_service_or_403
        except ImportError:
            from taurus.ee_fallback import ee_service_or_403
        from taurus.editions.features import F_SCRIPT_TASK_UNIFIED
        from rest_framework.exceptions import ValidationError
        svc_cls = ee_service_or_403('scheduler_unified_service', F_SCRIPT_TASK_UNIFIED)
        source = request.data.get('source')
        pk = request.data.get('pk')
        if source not in ('schedule', 'script_task'):
            raise ValidationError("source 必须是 'schedule' 或 'script_task'")
        try:
            pk_i = int(pk)
        except (TypeError, ValueError):
            raise ValidationError('pk 必须是整数')
        ok, msg = svc_cls.trigger_now(self, request, source=source, pk=pk_i)
        return SuccessResponse(msg=msg) if ok else ErrorResponse(msg=msg)

    @action(detail=False, methods=['get'], url_path='unified-stats')
    def unified_stats(self, request):
        """[EE] 双轨 KPI 汇总."""
        try:
            from taurus_ee.utils.gate import ee_service_or_403
        except ImportError:
            from taurus.ee_fallback import ee_service_or_403
        from taurus.editions.features import F_SCRIPT_TASK_UNIFIED
        svc_cls = ee_service_or_403('scheduler_unified_service', F_SCRIPT_TASK_UNIFIED)
        stats = svc_cls.unified_stats(self, request)
        return SuccessResponse(data=stats, msg='Unified schedule stats (EE)')

    @action(detail=False, methods=['post'], url_path='publish-alert')
    def publish_alert(self, request):
        """[EE] 发布调度失败告警（调试/外部集成入口，SCHEDULE_ALERT_RETRY）."""
        try:
            from taurus_ee.utils.gate import ee_service_or_403
        except ImportError:
            from taurus.ee_fallback import ee_service_or_403
        from taurus.editions.features import F_SCHEDULE_ALERT_RETRY
        svc_cls = ee_service_or_403('scheduler_alert_service', F_SCHEDULE_ALERT_RETRY)
        ok, detail = svc_cls.publish_failure_alert(request.data)
        return SuccessResponse(msg=detail) if ok else ErrorResponse(msg=detail)

    @action(detail=False, methods=['get'], url_path='recent-alerts')
    def recent_alerts(self, request):
        """[EE] 最近调度失败告警（SCHEDULE_ALERT_RETRY in-app inbox）."""
        try:
            from taurus_ee.utils.gate import ee_service_or_403
        except ImportError:
            from taurus.ee_fallback import ee_service_or_403
        from taurus.editions.features import F_SCHEDULE_ALERT_RETRY
        svc_cls = ee_service_or_403('scheduler_alert_service', F_SCHEDULE_ALERT_RETRY)
        try:
            limit = max(1, min(int(request.query_params.get('limit', 50)), 200))
        except (TypeError, ValueError):
            limit = 50
        return SuccessResponse(data={
            'results': svc_cls.recent_alerts(limit=limit),
        }, msg=f'Recent scheduler alerts (last {limit})')


class ScheduleExecutionViewSet(CustomModelViewSet):
    """Scheduled taskExecutionrecord管理"""
    queryset = ScheduleExecution.objects.all()
    serializer_class = ScheduleExecutionSerializer
    filterset_fields = ['schedule', 'status']
    ordering_fields = ['start_time', 'create_datetime']
    ordering = ['-create_datetime']


class HostViewSet(CustomModelViewSet):
    """Host管理"""
    queryset = Host.objects.all()
    serializer_class = HostSerializer
    search_fields = ['host_name', 'host_ip']
    filterset_fields = ['host_type', 'status', 'online_status']
    extra_filter_class = [CoreModelFilterBankend]  # Disable数据级PermissionFilter, use users Many-to-many关系控制
    lookup_field = 'host_uuid'  # use host_uuid 作为findField

    def perform_create(self, serializer):
        # === 配额校验：社区版主机上限 ===
        from taurus.editions.loader import check_quota as _check_quota
        _check_quota('max_hosts', Host.objects.count(), '托管主机')
        super().perform_create(serializer)

    def get_queryset(self):
        """
        普通User只能看到authorization给自己/Host
        Administratorcan看到所有Host
        """
        queryset = super().get_queryset()
        # anonymousUser或未认证时, returnEmptyQuery集(issue_certificate 等 AllowAny interface会直接via pk Query)
        if not self.request.user or not self.request.user.is_authenticated:
            return queryset
        if not self.request.user.is_superuser:
            queryset = queryset.filter(users=self.request.user)
        return queryset

    @action(detail=False, methods=['get'])
    def my_host_info(self, request):
        """Fetch我/Host统计Message"""
        # 复用 get_queryset /PermissionFilter:超管看All, 普通User只看authorization给自己/
        my_hosts = self.get_queryset()
        total = my_hosts.count()
        # 正常:Approved + 在line + Certificate有效;其余视为Exception(与 my_alarm_info Alert判定一致)
        normal = my_hosts.filter(
            status=1,
            online_status=1,
            certificate_status='valid'
        ).count()
        exception = total - normal

        return SuccessResponse(data={
            'total': total,
            'normal': normal,
            'exception': exception
        })

    @action(detail=False, methods=['get'])
    def my_alarm_info(self, request):
        """Fetch我/HostAlertMessage"""
        my_hosts = Host.objects.filter(users=request.user)
        alarms = []
        for host in my_hosts:
            if host.status == 1 and host.online_status == 0:
                alarms.append({
                    'id': host.id,
                    'host_ip': host.host_ip,
                    'host_name': host.host_name,
                    'content': 'Host offline',
                    'level': 'Critical',
                })
            elif host.certificate_status == 'revoked':
                alarms.append({
                    'id': host.id,
                    'host_ip': host.host_ip,
                    'host_name': host.host_name,
                    'content': 'Certificate revoked',
                    'level': 'Important',
                })
            elif host.status == 0:
                alarms.append({
                    'id': host.id,
                    'host_ip': host.host_ip,
                    'host_name': host.host_name,
                    'content': 'Host pending approval',
                    'level': 'Important',
                })
        return SuccessResponse(data=alarms)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """批准Host(Administrator)"""
        host = self.get_object()
        if host.status != 0:  # 不YesPending approvalStatus
            return ErrorResponse(msg="Only hosts with pending approval can be approved")

        host.status = 1  # Approved
        host.save()
        return SuccessResponse(msg="HostApproved")

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """拒绝Host(Administrator)"""
        host = self.get_object()
        if host.status != 0:
            return ErrorResponse(msg="Only hosts with pending approval can be approved")

        host.status = 2  # 已拒绝
        host.save()
        return SuccessResponse(msg="Host rejected")

    @action(detail=True, methods=['post'])
    def disable(self, request, pk=None):
        """DisableHost(Administrator)"""
        host = self.get_object()
        if host.status != 1:
            return ErrorResponse(msg="Only approved hosts can be disabled")

        host.status = 3  # Disabled
        host.save()
        return SuccessResponse(msg="HostDisabled")

    @action(detail=True, methods=['post'])
    def revoke_certificate(self, request, pk=None):
        """吊销HostCertificate(Administrator)"""
        host = self.get_object()
        
        if not host.certificate_serial:
            return ErrorResponse(msg="Host has no certificate serial, cannot revoke")
        
        if host.certificate_status == 'revoked':
            return ErrorResponse(msg="Host certificate has been revoked")
        
        reason = request.data.get('reason', 'unspecified')
        
        # Update数据library中/CertificateStatus
        host.certificate_status = 'revoked'
        host.certificate_revoked_at = timezone.now()
        host.certificate_revocation_reason = reason
        host.save()
        
        logger.info("[CertRevoke] Host certificate revoked: host_uuid=%s, hostname=%s, serial=%s, reason=%s",
                    host.host_uuid, host.host_name, host.certificate_serial, reason)
        
        return SuccessResponse(
            data={
                'host_id': str(host.host_uuid),
                'certificate_serial': host.certificate_serial,
                'revoked_at': host.certificate_revoked_at,
                'reason': reason,
            },
            msg="Certificate revoked"
        )

    @action(detail=True, methods=['post'])
    def restore_certificate(self, request, pk=None):
        """Restore已吊销/Certificate(Administrator)"""
        host = self.get_object()
        
        if host.certificate_status != 'revoked':
            return ErrorResponse(msg="Host certificate not revoked, no need to restore")
        
        # RestoreCertificateStatus
        host.certificate_status = 'valid'
        host.certificate_revoked_at = None
        host.certificate_revocation_reason = None
        host.save()
        
        logger.info("[CertRestore] Host certificate restored: host_uuid=%s, hostname=%s, serial=%s",
                    host.host_uuid, host.host_name, host.certificate_serial)
        
        return SuccessResponse(msg="Certificate restored")

    @action(detail=False, methods=['get'])
    def revoked_certificates(self, request):
        """Fetch所有已吊销/Certificatelist(Administrator)"""
        revoked_hosts = Host.objects.filter(certificate_status='revoked')
        
        data = []
        for host in revoked_hosts:
            data.append({
                'host_id': str(host.host_uuid),
                'host_name': host.host_name,
                'host_ip': host.host_ip,
                'certificate_serial': host.certificate_serial,
                'revoked_at': host.certificate_revoked_at,
                'reason': host.certificate_revocation_reason,
            })
        
        return SuccessResponse(data=data)

    @action(detail=True, methods=['post'], permission_classes=[AllowAny])
    def issue_certificate(self, request, host_uuid=None):
        """
        签发ClientCertificate
        
        Request body:
        {
            "csr": "-----BEGIN CERTIFICATE REQUEST-----\n...\n-----END CERTIFICATE REQUEST-----"
        }
        
        response:
        {
            "ca_cert": "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----",
            "client_cert": "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----",
            "serial": "01",
            "expires_in_days": 365
        }
        """
        host = self.get_object()
        
        # ValidationHostStatus
        if host.status != 1:
            return ErrorResponse(msg="Host not approved, cannot issue certificate")
        
        csr = request.data.get('csr')
        if not csr:
            return ErrorResponse(msg="Missing CSR content")
        
        # initialize CA 管理server
        ca_dir = getattr(settings, 'CA_CERT_DIR', os.path.join(settings.BASE_DIR, 'certs'))
        ca_manager = CAManager(ca_dir)
        
        # 确保 CA Certificate存在
        if not ca_manager.ensure_ca_exists():
            return ErrorResponse(msg="CA CertificateinitializeFailed")
        
        # 签署 CSR
        client_cert = ca_manager.sign_csr(csr, days=365)
        if not client_cert:
            return ErrorResponse(msg="Certificate signing failed")
        
        # FetchCertificate序列号
        serial = ca_manager.get_next_serial()
        
        # Fetch CA Certificate
        ca_cert = ca_manager.get_ca_cert()
        
        # UpdateHostCertificateMessage
        host.certificate_serial = serial
        host.certificate_status = 'valid'
        host.certificate_revoked_at = None
        host.certificate_revocation_reason = None
        host.save()
        
        logger.info(
            "[CertIssue] Certificate issued: host_uuid=%s, hostname=%s, serial=%s",
            host.host_uuid, host.host_name, serial
        )
        
        return DetailResponse(
            data={
                'ca_cert': ca_cert,
                'client_cert': client_cert,
                'serial': serial,
                'expires_in_days': 365,
            },
            msg="Certificate issued"
        )

    @action(detail=True, methods=['post'], permission_classes=[AllowAny])
    def issue_server_certificate(self, request, host_uuid=None):
        """
        签发ServiceserverCertificate(Contains SAN)
        
        Request body:
        {
            "csr": "-----BEGIN CERTIFICATE REQUEST-----\n...\n-----END CERTIFICATE REQUEST-----",
            "san_list": ["DNS:localhost", "DNS:*.localhost", "IP:127.0.0.1", "IP:::1"]  // Optional, 默认useHost名和IP
        }
        
        response:
        {
            "ca_cert": "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----",
            "server_cert": "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----",
            "serial": "01",
            "expires_in_days": 365
        }
        """
        host = self.get_object()
        
        # ValidationHostStatus
        if host.status != 1:
            return ErrorResponse(msg="Host not approved, cannot issue certificate")
        
        csr = request.data.get('csr')
        if not csr:
            return ErrorResponse(msg="Missing CSR content")

        # 方案 2:ServiceserverCertificate SAN Unified为固定 DNS Name(裸 IP 直连场景)
        # 1. 优先useEnvironment variables GRPC_SERVER_CERT_SAN specifiedName(如 taurus-grpc-server)
        # 2. Clientvia grpc.ssl_target_name_override 用此NameCheckCertificate, 绕过 IP/Host名match
        # 3. 不再将Host IP / Host名write SAN, Avoid因 IP 变更导致Certificate失效
        fixed_san_name = getattr(settings, 'GRPC_SERVER_CERT_SAN', 'taurus-grpc-server')

        # keep SERVER_CERT_SAN_LIST Environment variables作为覆盖option(如需自Definition多 SAN)
        san_list_config = getattr(settings, 'SERVER_CERT_SAN_LIST', '')
        if san_list_config:
            san_list = [s.strip() for s in san_list_config.split(",") if s.strip()]
            logger.info(
                "[ServerCertIssue] Using SERVER_CERT_SAN_LIST config SAN list: %s",
                san_list
            )
        else:
            # 默认:仅use固定 DNS Name作为 SAN
            san_list = [f"DNS:{fixed_san_name}"]
            logger.info(
                "[ServerCertIssue] Using fixed DNS SAN (plan 2): host_uuid=%s, san=%s",
                host.host_uuid, san_list
            )

        # initialize CA 管理server
        ca_dir = getattr(settings, 'CA_CERT_DIR', os.path.join(settings.BASE_DIR, 'certs'))
        ca_manager = CAManager(ca_dir)
        
        # 确保 CA Certificate存在
        if not ca_manager.ensure_ca_exists():
            return ErrorResponse(msg="CA CertificateinitializeFailed")
        
        # 签署ServiceserverCertificate
        server_cert = ca_manager.sign_server_csr(csr, san_list, days=365)
        if not server_cert:
            return ErrorResponse(msg="Service server certificate signing failed")
        
        # FetchCertificate序列号
        serial = ca_manager.get_next_serial()
        
        # Fetch CA Certificate
        ca_cert = ca_manager.get_ca_cert()
        
        logger.info(
            "[ServerCertIssue] Certificate issued: host_uuid=%s, hostname=%s, serial=%s, san=%s",
            host.host_uuid, host.host_name, serial, san_list
        )
        
        return DetailResponse(
            data={
                'ca_cert': ca_cert,
                'server_cert': server_cert,
                'serial': serial,
                'expires_in_days': 365,
                'target_name': fixed_san_name,
            },
            msg="Service certificate issued"
        )

    @action(detail=True, methods=['post'], url_path='generate-ticket')
    async def generate_ticket(self, request, pk=None):
        """
        GenerateExecutionCommandTicket
        
        Request body:
        {
            "action": "execute_command",  // Optional: execute_command/upload_file/download_file/maintenance
            "command": "ls -la",          // Optional, 限制只能Execution此Command
            "expires_minutes": 5          // Optional, 默认5分钟
        }
        
        response:
        {
            "ticket": "cmd_xxx...",
            "ticket_id": "ticket_xxx",
            "expires_at": "2026-06-25T10:35:00Z",
            "executor_endpoint": "grpc://host:50051"
        }
        """
        if not getattr(settings, 'TICKET_AUTH_ENABLED', False):
            return ErrorResponse(msg="Ticket auth not enabled, cannot generate ticket")
        
        host = self.get_object()
        
        if host.status != 1:
            return ErrorResponse(msg="Host not approved, cannot generate ticket")
        
        action_type = request.data.get('action', 'execute_command')
        command = request.data.get('command')
        expires_minutes = request.data.get('expires_minutes', 5)
        
        try:
            from taurus.utils.auth_client import TaurusAuthClient
            
            auth_client = TaurusAuthClient()
            ticket_result = await auth_client.generate_ticket(
                host_uuid=str(host.host_uuid),
                action=action_type,
                command=command,
                expires_minutes=expires_minutes,
            )
            
            executor_endpoint = self._get_executor_endpoint(host)
            
            return DetailResponse(
                data={
                    'ticket': ticket_result['ticket'],
                    'ticket_id': ticket_result['ticket_id'],
                    'expires_at': ticket_result['expires_at'],
                    'executor_endpoint': executor_endpoint,
                    'action': action_type,
                    'command': command,
                },
                msg="TicketGenerateSuccess"
            )
            
        except Exception as e:
            logger.error(f"Ticket generation failed: host={host.host_uuid}, error={e}")
            return ErrorResponse(msg=f"Ticket generation failed: {str(e)}")
    
    @action(detail=True, methods=['post'], url_path='revoke-ticket')
    async def revoke_ticket(self, request, pk=None):
        """
        UndoTicket
        
        Request body:
        {
            "ticket_id": "ticket_xxx",
            "reason": "UserCanceloperation"  // Optional
        }
        """
        if not getattr(settings, 'TICKET_AUTH_ENABLED', False):
            return ErrorResponse(msg="Ticket auth not enabled, cannot revoke ticket")
        
        host = self.get_object()
        ticket_id = request.data.get('ticket_id')
        
        if not ticket_id:
            return ErrorResponse(msg="Missing ticket_id")
        
        reason = request.data.get('reason', '')
        
        try:
            from taurus.utils.auth_client import TaurusAuthClient
            
            auth_client = TaurusAuthClient()
            result = await auth_client.revoke_ticket(
                ticket_id=ticket_id,
                reason=reason,
            )
            
            return DetailResponse(data=result, msg="TicketUndoSuccess")
            
        except Exception as e:
            logger.error(f"Ticket revocation failed: host={host.host_uuid}, ticket={ticket_id}, error={e}")
            return ErrorResponse(msg=f"Ticket revocation failed: {str(e)}")
    
    @action(detail=False, methods=['get'], url_path='list-tickets')
    async def list_tickets(self, request):
        """
        QueryTicketlist
        
        QueryParameters:
        - host_uuid: HostUUID(Optional)
        - status: TicketStatus(Optional, 0=未use, 1=已use, 2=已过期, 3=已Undo)
        """
        if not getattr(settings, 'TICKET_AUTH_ENABLED', False):
            return ErrorResponse(msg="Ticket auth not enabled, cannot query ticket list")
        
        host_uuid = request.query_params.get('host_uuid')
        status_param = request.query_params.get('status')
        
        try:
            from taurus.utils.auth_client import TaurusAuthClient
            
            auth_client = TaurusAuthClient()
            tickets = await auth_client.list_tickets(
                host_uuid=host_uuid,
                status=int(status_param) if status_param else None,
            )
            
            return SuccessResponse(data=tickets, msg="Queried successfully")
            
        except Exception as e:
            logger.error(f"Failed to query ticket list: error={e}")
            return ErrorResponse(msg=f"Failed to query ticket list: {str(e)}")
    
    @action(detail=False, methods=['get'], url_path='audit-logs')
    async def audit_logs(self, request):
        """
        QueryauditLog
        
        QueryParameters:
        - ticket_id: TicketID(Optional)
        - event: eventclass型(Optional, ticket_created/ticket_verified/ticket_used/ticket_revoked)
        """
        if not getattr(settings, 'TICKET_AUTH_ENABLED', False):
            return ErrorResponse(msg="Ticket auth not enabled, cannot query audit log")
        
        ticket_id = request.query_params.get('ticket_id')
        event = request.query_params.get('event')
        
        try:
            from taurus.utils.auth_client import TaurusAuthClient
            
            auth_client = TaurusAuthClient()
            logs = await auth_client.get_audit_logs(
                ticket_id=ticket_id,
                event=event,
            )
            
            return SuccessResponse(data=logs, msg="Queried successfully")
            
        except Exception as e:
            logger.error(f"Failed to query audit log: error={e}")
            return ErrorResponse(msg=f"Failed to query audit log: {str(e)}")
    
    @action(detail=False, methods=['post'], url_path='validate-hosts')
    def validate_hosts(self, request):
        """
        CheckHost标识listYesNo存在且currentUser有Permission

        Request body:
        {
            "hosts": ["host-001", "192.168.1.100", ...]
        }

        return:
        {
            "valid": [{ "identifier": "host-001", "id": 1, "host_name": "host-001", "host_ip": "192.168.1.100" }, ...],
            "not_found": ["xxx", ...],
            "no_permission": [{ "identifier": "yyy", "host_name": "...", "host_ip": "..." }, ...]
        }
        """
        host_identifiers = request.data.get('hosts', [])
        if not isinstance(host_identifiers, list):
            return ErrorResponse(msg="hosts ParametersmustYesarray")

        identifiers = [str(h).strip() for h in host_identifiers if str(h).strip()]
        if not identifiers:
            return DetailResponse(data={
                "valid": [],
                "not_found": [],
                "no_permission": [],
            })

        from django.db.models import Q
        matched_hosts = Host.objects.filter(
            Q(host_name__in=identifiers) | Q(host_ip__in=identifiers)
        )

        matched_map = {}
        for h in matched_hosts:
            if h.host_name:
                matched_map[h.host_name] = h
            if h.host_ip:
                matched_map[h.host_ip] = h

        is_superuser = getattr(request.user, 'is_superuser', False)
        user_id = getattr(request.user, 'pk', None)

        valid_list = []
        not_found_list = []
        no_permission_list = []

        for ident in identifiers:
            host = matched_map.get(ident)
            if host is None:
                not_found_list.append(ident)
                continue

            has_perm = False
            if is_superuser:
                has_perm = True
            elif user_id is not None:
                has_perm = Host.objects.filter(
                    pk=host.pk, users=user_id
                ).exists()

            host_info = {
                "identifier": ident,
                "id": host.id,
                "host_uuid": str(host.host_uuid),
                "host_name": host.host_name or '',
                "host_ip": host.host_ip or '',
                "status": host.status,
                "online_status": host.online_status,
            }
            # 三项Check(simultaneously满足才 valid):
            #   1. Host存在(Approved matched_map 命中走到这里)
            #   2. User有Permission(has_perm = 超管 或 在User归属list中)
            #   3. Approval passed status == 1(Approved)
            if has_perm and host.status == 1:
                valid_list.append(host_info)
            else:
                no_permission_list.append(host_info)

        return DetailResponse(data={
            "valid": valid_list,
            "not_found": not_found_list,
            "no_permission": no_permission_list,
        })

    def _get_executor_endpoint(self, host):
        """FetchHost/ executor 端."""
        host_ip = host.host_ip or 'localhost'
        return f"grpc://{host_ip}:50051"


class SupervisorViewSet(CustomModelViewSet):
    """Supervisor 管理interface(无需认证)"""
    queryset = Host.objects.none()
    serializer_class = HostSerializer
    permission_classes = [AllowAny]

    @action(detail=False, methods=['get'], permission_classes=[AllowAny])
    def install_script(self, request):
        """
        Download Supervisor installScript
        自动注入currentService端地址, User可via curl/wget Download后直接Execution

        用法:
          curl -fsSL https://taurus.example.com/api/taurus/supervisor/install_script/ | bash -s -- --token tao_xxx
          curl -fsSL https://taurus.example.com/api/taurus/supervisor/install_script/ | bash -s -- --token tao_xxx --auto-install
        """
        scheme = request.scheme
        host = request.get_host()
        server_url = f"{scheme}://{host}"

        script_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(script_dir, 'scripts', 'install.sh')

        if not os.path.exists(script_path):
            return ErrorResponse(msg="Install script file not found, please contact admin")

        with open(script_path, 'r') as f:
            script_content = f.read()

        script_content = script_content.replace(
            '__TAURUS_SERVER_URL__',
            server_url,
        )
        script_content = script_content.replace(
            'SERVER_URL_INJECTED=false',
            'SERVER_URL_INJECTED=true',
        )

        response = HttpResponse(script_content, content_type='text/x-shellscript')
        response['Content-Disposition'] = 'inline; filename="install.sh"'
        response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response

    @action(detail=False, methods=['post'], permission_classes=[AllowAny])
    def register(self, request):
        """Supervisor Register"""
        serializer = ExecutorRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token_str = serializer.validated_data['token']
        host_info = serializer.validated_data['host_info']
        supervisor_version = serializer.validated_data.get('supervisor_version', '1.0.0')
        client_ip = request.META.get('REMOTE_ADDR', 'unknown')

        logger.info(
            "[SupervisorRegister] Registration request: token=%s..., client_ip=%s, "
            "host_info=%s, version=%s",
            token_str[:8], host_info.get('ip'),
            {k: v for k, v in host_info.items() if k != 'extra_info'},
            supervisor_version,
        )

        # ValidationToken(useHashfind, 数据library中不存储明文)
        try:
            token_hash = RegistrationToken.hash_token(token_str)
            token = RegistrationToken.objects.get(
                token=token_hash,
                is_active=True
            )
            logger.info("[SupervisorRegister] Token verified: name=%s, max_uses=%d, used=%d",
                        token.name, token.max_uses, token.used_count)
        except RegistrationToken.DoesNotExist:
            logger.warning("[SupervisorRegister] Invalid token: token=%s...", token_str[:8])
            return ErrorResponse(msg="Invalid register token")

        # checkTokenYesNo过期
        if token.expires_at < timezone.now():
            logger.warning("[SupervisorRegister] Token expired: name=%s, expires_at=%s",
                           token.name, token.expires_at)
            return ErrorResponse(msg="Register token expired")

        # checkTokenuse次数
        if token.used_count >= token.max_uses:
            logger.warning("[SupervisorRegister] Token usage limit reached: name=%s, used=%d/%d",
                           token.name, token.used_count, token.max_uses)
            return ErrorResponse(msg="Register token usage limit reached")

        # check IP Whitelist
        if token.allowed_ips:
            if client_ip not in token.allowed_ips:
                logger.warning("[SupervisorRegister] IP not in whitelist: client_ip=%s, allowed=%s",
                               client_ip, token.allowed_ips)
                return ErrorResponse(msg=f"IP {client_ip}  not in whitelist")
            logger.info("[SupervisorRegister] IP whitelist passed: client_ip=%s", client_ip)

        # Check whether already exists相同 IP /Host
        existing_host = Host.objects.filter(host_ip=host_info['ip']).first()
        if existing_host:
            if existing_host.online_status == 1:
                logger.warning("[SupervisorRegister] IP exists and online: ip=%s, existing_host=%s",
                               host_info['ip'], existing_host.host_name)
                return ErrorResponse(msg=f"IP {host_info['ip']}  host already exists and online, cannot re-register")
            logger.info("[SupervisorRegister] IP exists offline, allowing re-registration: ip=%s, host_uuid=%s",
                        host_info['ip'], existing_host.host_uuid)

        import secrets
        from django.conf import settings

        signing_enabled = getattr(settings, 'REQUEST_SIGNING_ENABLED', False)
        signing_secret = secrets.token_hex(32) if signing_enabled else None

        # === 配额校验：仅全新主机注册时检查，已存在主机重注册不计入 ===
        if not existing_host:
            from taurus.editions.loader import check_quota as _check_quota
            _check_quota('max_hosts', Host.objects.count(), '托管主机')

        if existing_host:
            existing_host.host_name = host_info.get('hostname', host_info['ip'])
            existing_host.host_username = host_info.get('username', '')
            existing_host.host_type = host_info.get('os', 'unknown')
            existing_host.extra_info = host_info.get('extra_info', {})
            existing_host.supervisor_version = supervisor_version
            existing_host.status = 1 if token.auto_approve else 0
            existing_host.online_status = 0
            existing_host.request_signing_secret = signing_secret
            existing_host.last_heartbeat_at = None
            existing_host.save()
            host = existing_host
            logger.info("[SupervisorRegister] Host re-registered: uuid=%s, name=%s, ip=%s, status=%d",
                         host.host_uuid, host.host_name, host.host_ip, host.status)
        else:
            host = Host.objects.create(
                host_name=host_info.get('hostname', host_info['ip']),
                host_ip=host_info['ip'],
                host_username=host_info.get('username', ''),
                host_type=host_info.get('os', 'unknown'),
                extra_info=host_info.get('extra_info', {}),
                supervisor_version=supervisor_version,
                status=1 if token.auto_approve else 0,
                request_signing_secret=signing_secret,
                creator=request.user if request.user.is_authenticated else None,
            )
            logger.info("[SupervisorRegister] Host record created: uuid=%s, name=%s, ip=%s, status=%d",
                         host.host_uuid, host.host_name, host.host_ip, host.status)

        # UpdateTokenuse次数
        token.used_count += 1
        token.save()

        # according to Client IP 分配HeartbeatServiceserver(网段match + Load balancing)
        hb_server = HeartbeatServer.assign_for_host(host_info['ip'])
        host.heartbeat_server = hb_server
        host.save(update_fields=['heartbeat_server'])

        # 构建HeartbeatConfig(Client 自动Save, 无需手动Config)
        if hb_server:
            heartbeat_config = {
                'server_url': hb_server.address.rstrip('/'),
                'interval': 30,
                'timeout': 10,
                'server_name': hb_server.name,
            }
            hb_server.current_connections = models.F('current_connections') + 1
            hb_server.save(update_fields=['current_connections'])
            hb_server.refresh_from_db(fields=['current_connections'])
            logger.info(
                "[SupervisorRegister] Heartbeat server assigned: ip=%s -> %s (%s), "
                "subnet=%s, weight=%d, connections=%d/%d",
                host_info['ip'], hb_server.name, hb_server.address,
                hb_server.subnet or '(fallback)', hb_server.weight,
                hb_server.current_connections, hb_server.max_connections,
            )

            # 构建备用节.list(排除current主节., PerPriorityOrder)
            fallback_servers = []
            all_active = HeartbeatServer.objects.filter(is_active=True).exclude(id=hb_server.id)
            for i, fb in enumerate(all_active.order_by('-weight')):
                fallback_servers.append({
                    'server_url': fb.address.rstrip('/'),
                    'server_name': fb.name,
                    'priority': i + 1,
                })
        else:
            default_url = request.build_absolute_uri('/').rstrip('/')
            heartbeat_config = {
                'server_url': default_url,
                'interval': 30,
                'timeout': 10,
                'server_name': 'default',
            }
            fallback_servers = []
            logger.warning(
                "[SupervisorRegister] No heartbeat server available, using default: ip=%s -> %s",
                host_info['ip'], default_url,
            )

        heartbeat_config['fallback_servers'] = fallback_servers

        response_data = {
            'host_id': str(host.host_uuid),
            'status': host.status,
            'status_display': host.get_status_display(),
            'message': 'Registered, awaiting admin approval' if host.status == 0 else 'Registered and auto-approved',
            'heartbeat': heartbeat_config,
            'signing_secret': host.request_signing_secret,
        }

        return DetailResponse(data=response_data, msg="RegisterSuccess")

    @action(detail=False, methods=['post'], permission_classes=[AllowAny])
    def deregister(self, request):
        """
        Supervisor Deregister
        Host主动Deregister, 将Status设为离line
        requireSignatureValidation(与Heartbeatinterface一致), 防止未authorizationDeregister
        """
        host_id = request.data.get('host_id')
        if not host_id:
            return ErrorResponse(msg="Missing host_id")

        try:
            host = Host.objects.get(host_uuid=host_id)
        except Host.DoesNotExist:
            return ErrorResponse(msg="Host not found")

        if getattr(settings, 'REQUEST_SIGNING_ENABLED', False) and host.request_signing_secret:
            from taurus.utils.signing import verify_signature

            client_signature = request.data.get('signature')
            client_nonce = request.data.get('nonce')
            client_timestamp = request.data.get('timestamp_int')

            if not client_signature or not client_nonce or not client_timestamp:
                return ErrorResponse(msg="Request missing signature parameters")

            import json
            body_data = dict(request.data)
            body_data['signature'] = ''
            body_str = json.dumps(body_data, sort_keys=True)

            is_valid, error_msg = verify_signature(
                host_id=host_id,
                secret=host.request_signing_secret,
                timestamp=int(client_timestamp),
                nonce=client_nonce,
                signature=client_signature,
                body=body_str,
            )

            if not is_valid:
                logger.warning("[SupervisorDeregister] Signature verification failed: %s host_uuid=%s", error_msg, host_id)
                return ErrorResponse(msg=f"Signature verification failed: {error_msg}")

        host.online_status = 0
        host.save(update_fields=['online_status'])

        logger.info("[SupervisorDeregister] Host deregistered: host_id=%s, hostname=%s", host_id, host.host_name)
        return DetailResponse(msg="DeregisterSuccess")

    @action(detail=False, methods=['get'], permission_classes=[AllowAny])
    def download(self, request):
        """
        Download Taurus Program installpackage(Config驱动)
        supportParameters:
            - package_type: 程序class型(如 executor, supervisor, monitor 等)
            - platform: linux, macos, windows(默认 linux)
            - arch: x86_64, arm64(默认 x86_64)
        
        Config方式:modify package_dirs.json File, 无需modify代码
        """
        package_type = request.GET.get('package_type')
        if not package_type:
            return ErrorResponse(msg="Please specify package_type", status=400)
        
        platform = request.GET.get('platform', 'linux')
        arch = request.GET.get('arch', 'x86_64')
        
        # 从Config中Fetch程序packagedirectoryConfig
        package_dirs = getattr(settings, 'TAURUS_PACKAGE_DIRS', {})
        
        if package_type not in package_dirs:
            logger.error("[DownloadPkg] Unconfigured program type: %s, configured: %s", package_type, list(package_dirs.keys()))
            return ErrorResponse(msg=f"Unconfigured program type: {package_type}, available: {', '.join(package_dirs.keys())}", status=400)
        
        pkg_config = package_dirs[package_type]
        package_dir = pkg_config.get('dir')
        file_prefix = pkg_config.get('prefix', f'taurus-{package_type}')
        file_ext = pkg_config.get('ext', '')
        
        if not package_dir or not os.path.exists(package_dir):
            logger.error("[Download%s] Package dir missing: %s", package_type, package_dir)
            return ErrorResponse(msg=f"{package_type}  package directory not configured", status=500)
        
        # findmatch/Installation package
        pattern = f"{file_prefix}-*-{platform}-{arch}{file_ext}"
        packages = glob.glob(os.path.join(package_dir, pattern))
        
        if not packages:
            # Attempt只match platform
            pattern = f"{file_prefix}-*-{platform}*{file_ext}"
            packages = glob.glob(os.path.join(package_dir, pattern))
        
        if not packages:
            logger.warning("[Download%s] No matching package: platform=%s, arch=%s", package_type, platform, arch)
            return ErrorResponse(msg=f"No suitable install package found for  {platform}/{arch} / {package_type}  package", status=404)
        
        # Select最新Version
        def extract_version(filepath):
            filename = os.path.basename(filepath)
            match = re.search(rf'{re.escape(file_prefix)}-(\d+\.\d+\.\d+)', filename)
            return match.group(1) if match else '0.0.0'
        
        packages.sort(key=extract_version, reverse=True)
        latest_package = packages[0]
        
        logger.info("[Download%s] Downloading file: %s", package_type, os.path.basename(latest_package))
        
        # compute checksum
        import hashlib
        sha256_hash = hashlib.sha256()
        with open(latest_package, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        checksum = sha256_hash.hexdigest()
        
        logger.info("[Download%s] SHA256: %s", package_type, checksum)
        
        response = FileResponse(
            open(latest_package, 'rb'),
            content_type='application/octet-stream'
        )
        response['Content-Disposition'] = f'attachment; filename="{os.path.basename(latest_package)}"'
        response['X-Checksum-SHA256'] = checksum
        return response

    @action(detail=False, methods=['post'], permission_classes=[AllowAny])
    def heartbeat(self, request):
        """Supervisor Heartbeat上报(receive管理指令)"""
        serializer = SupervisorHeartbeatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        host_id = serializer.validated_data['host_id']
        timestamp = serializer.validated_data['timestamp']
        supervisor_version = serializer.validated_data.get('supervisor_version', '1.0.0')
        host_name = serializer.validated_data.get('host_name', '')
        host_username = serializer.validated_data.get('host_username', '')
        current_server_url = serializer.validated_data.get('current_server_url', '')
        metrics = serializer.validated_data.get('metrics', {})
        programs = serializer.validated_data.get('programs', [])

        logger.debug(
            "[SupervisorHeartbeat] host_id=%s, host_name=%s, host_username=%s, supervisor_version=%s, current_server=%s, programs=%d",
            host_id, host_name, host_username, supervisor_version, current_server_url, len(programs),
        )

        # findHost
        try:
            host = Host.objects.get(host_uuid=host_id, status=1)  # 只接受Approved/Host
        except Host.DoesNotExist:
            logger.warning("[SupervisorHeartbeat] Host not found or not approved: host_uuid=%s", host_id)
            return ErrorResponse(msg="Host does not exist or not approved")

        # Verify request signature (if enabled on Server)
        from django.conf import settings
        if getattr(settings, 'REQUEST_SIGNING_ENABLED', False) and host.request_signing_secret:
            from taurus.utils.signing import verify_signature
            
            client_signature = request.data.get('signature')
            client_nonce = request.data.get('nonce')
            client_timestamp = request.data.get('timestamp_int')
            
            if not client_signature or not client_nonce or not client_timestamp:
                logger.warning("[SupervisorHeartbeat] Signature verification failed: missing signed params host_uuid=%s", host_id)
                return ErrorResponse(msg="Request missing signature parameters")
            
            # Supervisor computeSignature时use signature=''(Empty字符串Placeholder)
            # so Server Validation时也require将 signature replace为Empty字符串
            import json
            body_data = dict(request.data)
            body_data['signature'] = ''
            body_str = json.dumps(body_data, sort_keys=True)
            
            is_valid, error_msg = verify_signature(
                host_id=host_id,
                secret=host.request_signing_secret,
                timestamp=int(client_timestamp),
                nonce=client_nonce,
                signature=client_signature,
                body=body_str,
            )
            
            if not is_valid:
                logger.warning("[SupervisorHeartbeat] Signature verification failed: %s host_uuid=%s", error_msg, host_id)
                return ErrorResponse(msg=f"Signature verification failed: {error_msg}")

        # UpdateHostMessage
        update_fields = ['last_heartbeat_at', 'online_status', 'supervisor_version']
        
        was_offline = host.online_status != 1
        host.last_heartbeat_at = timezone.now()
        host.online_status = 1
        host.supervisor_version = supervisor_version
        
        # detect Supervisor YesNo从离lineRestore(超过 2 次Heartbeat interval没收到Heartbeat)
        # ifRestore, Reset所有卡住/指令Status
        if was_offline:
            logger.info(
                "[SupervisorHeartbeat] Host restored after offline: host_id=%s, host_uuid=%s",
                host_id, host.host_uuid,
            )
            
            # Reset所有卡住/installConfig
            reset_install_count = ProgramInstallConfig.objects.filter(
                host=host,
                installed=False,
                installing=True,
            ).update(installing=False, dispatched_at=None)
            if reset_install_count > 0:
                logger.info(
                    "[SupervisorHeartbeat] Resetting %d stuck install configs (offline recovery)",
                    reset_install_count,
                )
            
            # Reset所有卡住/程序指令
            reset_cmd_count = ProgramCommand.objects.filter(
                host=host,
                status=0,
                dispatched=True,
            ).update(dispatched=False, dispatched_at=None)
            if reset_cmd_count > 0:
                logger.info(
                    "[SupervisorHeartbeat] Resetting %d stuck program commands (offline recovery)",
                    reset_cmd_count,
                )
        
        # UpdateHostUser名(ifHeartbeat中provide了)
        if host_username:
            update_fields.append('host_username')
            host.host_username = host_username
        
        # UpdateHostName(ifHeartbeat中provide了且与数据library不一致)
        if host_name and host_name != host.host_name:
            old_host_name = host.host_name
            update_fields.append('host_name')
            host.host_name = host_name
            logger.info("[SupervisorHeartbeat] Updating hostname: host_id=%s, from=%s, to=%s", host_id, old_host_name, host_name)
        
        # UpdateHeartbeatServiceserver(ifHeartbeat中provide了currentjoin/Serviceserver地址)
        if current_server_url:
            try:
                from taurus.models import HeartbeatServer
                hb_server = HeartbeatServer.objects.filter(address=current_server_url).first()
                if hb_server and hb_server.id != host.heartbeat_server_id:
                    # 先record旧值, 再赋值
                    old_server_addr = host.heartbeat_server.address if host.heartbeat_server else 'None'
                    update_fields.append('heartbeat_server')
                    host.heartbeat_server = hb_server
                    logger.info(
                        "[SupervisorHeartbeat] Host heartbeat server switched: host_id=%s, from=%s, to=%s",
                        host_id,
                        old_server_addr,
                        current_server_url,
                    )
            except Exception as e:
                logger.warning("[SupervisorHeartbeat] Failed to update heartbeat server: %s", e)
        
        host.save(update_fields=update_fields)

        if was_offline:
            logger.info("[SupervisorHeartbeat] Host online: host_id=%s, name=%s, ip=%s",
                        host_id, host.host_name, host.host_ip)

        # SaveHeartbeatrecord(metrics 指标数据)- 同一Host只keep最新一entries
        from django.utils import timezone as django_timezone
        now = django_timezone.now()
        HostHeartbeat.objects.update_or_create(
            host=host,
            defaults={
                'timestamp': now,
                'supervisor_version': supervisor_version,
                'heartbeat_server': current_server_url,
                'cpu_usage': metrics.get('cpu_usage'),
                'memory_usage': metrics.get('memory_usage'),
                'disk_usage': metrics.get('disk_usage'),
                'load_average': metrics.get('load_average'),
                'network_rx_bytes': metrics.get('network_rx_bytes'),
                'network_tx_bytes': metrics.get('network_tx_bytes'),
                'process_count': metrics.get('process_count'),
                'uptime_seconds': metrics.get('uptime_seconds'),
                'supervisor_status': 'running',
                'update_datetime': now,
            }
        )

        # Update受管程序Status
        reported_programs = set()
        for prog in programs:
            prog_name = prog.get('name')
            if not prog_name:
                continue
            
            reported_programs.add(prog_name)
            
            defaults = {
                'version': prog.get('version', 'unknown'),
                'status': prog.get('status', 'stopped'),
                'pid': prog.get('pid'),
                'port': prog.get('port'),
                'last_heartbeat_at': django_timezone.now(),
            }
            
            ManagedProgram.objects.update_or_create(
                host=host,
                name=prog_name,
                defaults=defaults,
            )
            
            logger.debug(
                "[SupervisorHeartbeat] Updating program status: %s %s v%s %s",
                host.host_name, prog_name, defaults['version'], defaults['status'],
            )
        
        # 将未上报/程序标记为Stopped(Supervisor 不再管理这些程序)
        # Note:即使 reported_programs 为Empty也要Execution(Supervisor 首次start时所有程序都应标记为stop)
        # 优化:只UpdateStatus不Yes stopped /程序, Avoid不必要/数据library写operation
        stopped_count = ManagedProgram.objects.filter(
            host=host,
        ).exclude(name__in=reported_programs).exclude(
            status='stopped',  # 已经Yes stopped /不requireUpdate
        ).update(
            status='stopped',
            pid=None,
            port=None,
            last_heartbeat_at=django_timezone.now(),
        )
        if stopped_count > 0:
            logger.info(
                "[SupervisorHeartbeat] Marking %d programs as stopped (not reported): host_id=%s",
                stopped_count, host_id,
            )

        # 同步 ProgramInstallConfig.installed:Supervisor 未上报/程序标记为Not installed
        # 确保后端与 Supervisor localStatus一致
        # Note:仅when Supervisor 上报了至少一 程序时才Execution同步, Avoid首次Heartbeat(程序list为Empty)误改Status
        if reported_programs:
            uninstalled_count = ProgramInstallConfig.objects.filter(
                host=host,
                installed=True,
            ).exclude(program_name__in=reported_programs).update(
                installed=False,
            )
            if uninstalled_count > 0:
                logger.info(
                    "[SupervisorHeartbeat] Syncing %d install configs as not installed (not reported): host_id=%s",
                    uninstalled_count, host_id,
                )

        # Supervisor 离lineRestore后Reset指令Status(Avoid Supervisor Restart后指令卡住)
        # 原理:Supervisor 能sendHeartbeatDescription它已Restart完成, before/指令if还没Execution就视为丢失
        # Note:不settingtimeoutReset, 因为install指令mayrequire很长时间(Download大File等)
        # 只有 Supervisor 离lineRestore时才Reset, 由 Supervisor 主动上报Execution result
        if was_offline:
            reset_count = ProgramInstallConfig.objects.filter(
                host=host,
                installed=False,
                installing=True,
            ).update(installing=False, dispatched_at=None)
            if reset_count > 0:
                logger.info(
                    "[SupervisorHeartbeat] Resetting %d install config states (offline recovery)",
                    reset_count,
                )
            
            cmd_reset_count = ProgramCommand.objects.filter(
                host=host,
                status=0,
                dispatched=True,
            ).update(dispatched=False, dispatched_at=None)
            if cmd_reset_count > 0:
                logger.info(
                    "[SupervisorHeartbeat] Resetting %d program command states (offline recovery)",
                    cmd_reset_count,
                )

        # processHeartbeat中上报/正在Running/指令Status
        executing_commands = request.data.get('executing_commands', [])
        if executing_commands:
            for exec_cmd in executing_commands:
                cmd_id = exec_cmd.get('command_id')
                cmd_type = exec_cmd.get('command_type')
                state = exec_cmd.get('state')
                
                if not cmd_id or not cmd_type:
                    continue
                
                logger.info(
                    "[SupervisorHeartbeat] Received cmd status report: command_id=%s, type=%s, state=%s",
                    cmd_id, cmd_type, state,
                )
                
                # according to指令class型UpdateStatus
                if cmd_type == 'install_config':
                    try:
                        config = ProgramInstallConfig.objects.get(id=cmd_id, host=host)
                        # if Supervisor 报告正在Installing, 保持 installing=True
                        if state in ('downloading', 'verifying', 'installing', 'starting'):
                            config.installing = True
                            config.dispatched_at = timezone.now()
                            config.save(update_fields=['installing', 'dispatched_at'])
                            logger.info(
                                "[SupervisorHeartbeat] Keeping installing state: config_id=%s, state=%s",
                                cmd_id, state,
                            )
                    except ProgramInstallConfig.DoesNotExist:
                        logger.warning(
                            "[SupervisorHeartbeat] Install config not found: command_id=%s", cmd_id,
                        )
                else:
                    try:
                        cmd = ProgramCommand.objects.get(id=cmd_id, host=host)
                        # if Supervisor 报告正在Running, 保持 dispatched=True
                        if state in ('downloading', 'verifying', 'installing', 'starting'):
                            cmd.dispatched = True
                            cmd.dispatched_at = timezone.now()
                            cmd.save(update_fields=['dispatched', 'dispatched_at'])
                            logger.info(
                                "[SupervisorHeartbeat] Keeping executing state: command_id=%s, state=%s",
                                cmd_id, state,
                            )
                    except ProgramCommand.DoesNotExist:
                        logger.warning(
                            "[SupervisorHeartbeat] Program command not found: command_id=%s", cmd_id,
                        )

        # 构建管理指令
        commands = []
        
        # 下发Program install指令(先查ID再Update, Avoid时间精度问题)
        pending_ids = list(ProgramInstallConfig.objects.filter(
            host=host,
            installed=False,
            installing=False,
            enabled=True,
            dispatched_at__isnull=True,
        ).values_list('id', flat=True))
        
        if pending_ids:
            ProgramInstallConfig.objects.filter(id__in=pending_ids).update(
                installing=True, dispatched_at=timezone.now()
            )
            
            pending_configs = ProgramInstallConfig.objects.filter(
                id__in=pending_ids,
            ).select_related('host')
            
            for config in pending_configs:
                commands.append({
                    'type': 'install_program',
                    'program_name': config.program_name,
                    'version': config.version,
                    'config': config.config,
                    'auto_start': config.auto_start,
                    'user': config.user,
                    'group': config.group,
                    'command_id': config.id,
                    'command_type': 'install_config',
                    'max_retries': config.max_retries,
                })
                logger.info(
                    "[SupervisorHeartbeat] Dispatching install cmd: host_id=%s, program=%s v%s",
                    host_id, config.program_name, config.version,
                )
        
        # 下发其他程序管理指令(upgrade, start, stop, Restart, remove)
        pending_cmd_ids = list(ProgramCommand.objects.filter(
            host=host,
            status=0,
            dispatched=False,
        ).values_list('id', flat=True))
        
        if pending_cmd_ids:
            ProgramCommand.objects.filter(id__in=pending_cmd_ids).update(
                dispatched=True, dispatched_at=timezone.now()
            )
            
            pending_commands = ProgramCommand.objects.filter(
                id__in=pending_cmd_ids,
            ).select_related('host').order_by('create_datetime')
            
            for cmd in pending_commands:
                # check程序YesNoInstalled(start/stop/restart require程序先install)
                if cmd.action in ('start', 'stop', 'restart'):
                    program_exists = ManagedProgram.objects.filter(
                        host=host,
                        name=cmd.program_name,
                    ).exists()
                    if not program_exists:
                        logger.warning(
                            "[SupervisorHeartbeat] Skipping cmd: program not installed host_id=%s, program=%s, action=%s",
                            host_id, cmd.program_name, cmd.action,
                        )
                        cmd.dispatched = False
                        cmd.dispatched_at = None
                        cmd.save(update_fields=['dispatched', 'dispatched_at'])
                        continue
                
                cmd_data = {
                    'type': f'{cmd.action}_program',
                    'program_name': cmd.program_name,
                    'command_id': cmd.id,
                    'command_type': 'program_command',
                    'max_retries': cmd.max_retries,
                }
                
                # upgrade指令requireTarget version
                if cmd.action == 'upgrade':
                    cmd_data['target_version'] = cmd.target_version
                    cmd_data['config'] = cmd.config
                
                # install指令requireVersion和Config
                if cmd.action == 'install':
                    cmd_data['version'] = cmd.target_version
                    cmd_data['config'] = cmd.config
                
                commands.append(cmd_data)
                
                logger.info(
                    "[SupervisorHeartbeat] Dispatching program mgmt cmd: host_id=%s, program=%s, action=%s",
                    host_id, cmd.program_name, cmd.action,
                )

        # 下发Log收集控制指令
        pending_log_cmd_ids = list(LogCommand.objects.filter(
            host=host,
            status=0,
            dispatched=False,
        ).values_list('id', flat=True))

        if pending_log_cmd_ids:
            LogCommand.objects.filter(id__in=pending_log_cmd_ids).update(
                dispatched=True, dispatched_at=timezone.now()
            )

            pending_log_commands = LogCommand.objects.filter(
                id__in=pending_log_cmd_ids,
            ).select_related('host').order_by('create_datetime')

            for log_cmd in pending_log_commands:
                cmd_data = {
                    'type': 'log_collect',
                    'log_action': log_cmd.action,
                    'min_level': log_cmd.min_level,
                    'programs': log_cmd.programs or [],
                    'duration': log_cmd.duration,
                    'command_id': log_cmd.id,
                    'command_type': 'log_command',
                }
                commands.append(cmd_data)

                logger.info(
                    "[SupervisorHeartbeat] Dispatching log collect cmd: host_id=%s, action=%s, level=%s",
                    host_id, log_cmd.action, log_cmd.min_level,
                )

        return SuccessResponse(data={
            'commands': commands,
            'server_time': timezone.now().isoformat()
        }, msg="SupervisorHeartbeatreceiveSuccess")

    @action(detail=False, methods=['post'], permission_classes=[AllowAny])
    def report_command_result(self, request):
        """receive Supervisor 上报/指令Execution result"""
        from rest_framework import serializers
        
        class ReportSerializer(serializers.Serializer):
            host_id = serializers.CharField(help_text="Host UUID")
            command_id = serializers.IntegerField(help_text="Command ID")
            command_type = serializers.CharField(required=False, allow_blank=True, help_text="Command type: program_command=program command, install_config=install config")
            status = serializers.IntegerField(help_text="Execution status:2=Success, 3=Failed")
            result_message = serializers.CharField(required=False, allow_blank=True, help_text="Execution resultmessage")
        
        serializer = ReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        host_id = serializer.validated_data['host_id']
        command_id = serializer.validated_data['command_id']
        command_type = serializer.validated_data.get('command_type', 'program_command')
        status = serializer.validated_data['status']
        result_message = serializer.validated_data.get('result_message', '')
        
        # findHost
        try:
            host = Host.objects.get(host_uuid=host_id, status=1)
        except Host.DoesNotExist:
            return ErrorResponse(msg="Host does not exist or not approved")
        
        # Verify request signature (if enabled on Server)
        if getattr(settings, 'REQUEST_SIGNING_ENABLED', False) and host.request_signing_secret:
            from taurus.utils.signing import verify_signature
            
            client_signature = request.data.get('signature')
            client_nonce = request.data.get('nonce')
            client_timestamp = request.data.get('timestamp_int')
            
            if not client_signature or not client_nonce or not client_timestamp:
                logger.warning("[CmdResultReport] Signature verification failed: missing signed params host_uuid=%s", host_id)
                return ErrorResponse(msg="Request missing signature parameters")
            
            # Supervisor computeSignature时use signature=''(Empty字符串Placeholder)
            # so Server Validation时也require将 signature replace为Empty字符串
            import json
            body_data = dict(request.data)
            body_data['signature'] = ''
            body_str = json.dumps(body_data, sort_keys=True)
            
            is_valid, error_msg = verify_signature(
                host_id=host_id,
                secret=host.request_signing_secret,
                timestamp=int(client_timestamp),
                nonce=client_nonce,
                signature=client_signature,
                body=body_str,
            )
            
            if not is_valid:
                logger.warning("[CmdResultReport] Signature verification failed: %s host_uuid=%s", error_msg, host_id)
                return ErrorResponse(msg=f"Signature verification failed: {error_msg}")
        
        # according to指令class型UpdateDifferentModel
        if command_type == 'install_config':
            # Status同步上报(command_id=0, Supervisor Restart后detectlocalInstalled程序)
            if command_id == 0:
                # 从 result_message 中提取 program_name
                # Format: "程序Installed(localdetect): taurus-executor v1.0.0"
                import re
                match = re.search(r': (\S+) v', result_message or '')
                if match:
                    program_name = match.group(1)
                    try:
                        install_config = ProgramInstallConfig.objects.get(
                            host=host,
                            program_name=program_name,
                            installed=False,
                        )
                        install_config.installed = True
                        install_config.installing = False
                        install_config.dispatched_at = None
                        install_config.save(update_fields=['installed', 'installing', 'dispatched_at'])
                        logger.info(
                            "[StatusSyncReport] Install config marked installed: host_id=%s, program=%s",
                            host_id, program_name,
                        )
                    except ProgramInstallConfig.DoesNotExist:
                        logger.debug(
                            "[StatusSyncReport] No pending install config (may installed): host_id=%s, program=%s",
                            host_id, program_name,
                        )
                else:
                    logger.warning("[StatusSyncReport] Cannot parse program_name: %s", result_message)
                return SuccessResponse(msg="Status sync complete")
            else:
                # UpdateinstallConfigStatus
                try:
                    install_config = ProgramInstallConfig.objects.get(id=command_id, host=host)
                    install_config.result_message = result_message
                    if status == 2:
                        install_config.installed = True
                        install_config.installing = False
                        # 不Reset dispatched_at, 防止重复下发
                        install_config.save(update_fields=['installed', 'installing', 'result_message'])
                        logger.info(
                            "[CmdResultReport] Install config success: host_id=%s, config_id=%s, program=%s",
                            host_id, command_id, install_config.program_name,
                        )
                    else:
                        # Failed时Reset installed=False 和 installing=False, allow Supervisor according to max_retries 自行retry
                        # 不Reset dispatched_at, 防止后端重复下发
                        install_config.installed = False
                        install_config.installing = False
                        install_config.save(update_fields=['installed', 'installing', 'result_message'])
                        logger.warning(
                            "[CmdResultReport] Install config failed: host_id=%s, config_id=%s, program=%s, max_retries=%d, msg=%s",
                            host_id, command_id, install_config.program_name,
                            install_config.max_retries, result_message,
                        )
                    return SuccessResponse(msg="Install config status updated")
                except ProgramInstallConfig.DoesNotExist:
                    logger.warning("[CmdResultReport] Install config not found: command_id=%s", command_id)
                    return ErrorResponse(msg="Install config not found")
        else:
            # Update程序指令Status
            try:
                cmd = ProgramCommand.objects.get(id=command_id, host=host)
                cmd.status = status
                cmd.result_message = result_message
                cmd.executed_at = timezone.now()
                
                # 不Reset dispatched 和 dispatched_at, 防止后端重复下发
                # Supervisor 端/retry由 max_retries 控制, 不require后端re-下发
                cmd.save(update_fields=['status', 'result_message', 'executed_at'])
                
                if status != 2:
                    logger.warning(
                        "[CmdResultReport] Command execution failed: host_id=%s, command_id=%s, action=%s, max_retries=%d, msg=%s",
                        host_id, command_id, cmd.action, cmd.max_retries, result_message,
                    )
                
                logger.info(
                    "[CmdResultReport] Command result updated: host_id=%s, command_id=%s, status=%s",
                    host_id, command_id, status,
                )
                
                return SuccessResponse(msg="Command execution result reported")
            except ProgramCommand.DoesNotExist:
                return ErrorResponse(msg="Command not found")


class HostHeartbeatViewSet(CustomModelViewSet):
    """HostHeartbeatrecord管理(只读)"""
    queryset = HostHeartbeat.objects.all()
    serializer_class = HostHeartbeatSerializer
    filterset_fields = ['host', 'supervisor_status']
    ordering_fields = ['timestamp', 'create_datetime']
    ordering = ['-timestamp']

    def get_queryset(self):
        queryset = super().get_queryset()
        # 普通User只能看到authorizationHost/record
        if not self.request.user.is_superuser:
            queryset = queryset.filter(host__users=self.request.user)
        return queryset


class ManagedProgramViewSet(CustomModelViewSet):
    """受管程序管理"""
    queryset = ManagedProgram.objects.all()
    serializer_class = ManagedProgramSerializer
    search_fields = ['name', 'host__host_name', 'host__host_ip']
    filterset_fields = ['host', 'status']
    ordering_fields = ['name', 'create_datetime']
    ordering = ['name']

    def get_queryset(self):
        queryset = super().get_queryset()
        # 普通User只能看到authorizationHost/程序
        if not self.request.user.is_superuser:
            queryset = queryset.filter(host__users=self.request.user)
        return queryset

    @action(methods=['POST'], detail=True)
    def start(self, request, pk=None):
        """start程序"""
        program = self.get_object()
        ProgramCommand.objects.create(
            host=program.host,
            program_name=program.name,
            action='start',
        )
        return SuccessResponse(msg="Start command issued, waiting for supervisor")

    @action(methods=['POST'], detail=True)
    def stop(self, request, pk=None):
        """stop程序"""
        program = self.get_object()
        ProgramCommand.objects.create(
            host=program.host,
            program_name=program.name,
            action='stop',
        )
        return SuccessResponse(msg="Stop command issued, waiting for supervisor")

    @action(methods=['POST'], detail=True)
    def restart(self, request, pk=None):
        """Restart程序"""
        program = self.get_object()
        ProgramCommand.objects.create(
            host=program.host,
            program_name=program.name,
            action='restart',
        )
        return SuccessResponse(msg="Restart command issued, waiting for supervisor")

    @action(methods=['POST'], detail=True)
    def remove(self, request, pk=None):
        """remove程序"""
        program = self.get_object()
        ProgramCommand.objects.create(
            host=program.host,
            program_name=program.name,
            action='remove',
        )
        return SuccessResponse(msg="Remove command issued, waiting for supervisor")



# =====================================================================
# [M2.5 Double Gate] 日志中心 (HostLog/LogCommand) + 联系销售(ContactLead: create=CE，其余=EE)
# 导入工具统一放在此块，避免装饰器找不到。
# =====================================================================
from django.utils.decorators import method_decorator as _hl_method_decorator  # noqa: E402
from taurus.editions.loader import require_feature as _hl_require_feature  # noqa: E402
from taurus.editions.features import F_HOST_LOG_FORWARDING as _F_HL_LOG  # noqa: E402

from django.utils.decorators import method_decorator as _lc_method_decorator  # noqa: E402
from taurus.editions.loader import require_feature as _lc_require_feature  # noqa: E402
from taurus.editions.features import F_LOG_COMMAND_CONTROL as _F_LC_CMD  # noqa: E402

from django.utils.decorators import method_decorator as _cl_method_decorator  # noqa: E402
from taurus.editions.features import F_CONTACT_LEAD_PORTAL as _F_CL_GATE  # noqa: E402
from taurus.editions.loader import require_feature as _cl_require_feature  # noqa: E402
from functools import wraps as _cl_wraps

def _cl_mixed_gate(view_fn):
    """ContactLead: create CE 匿名允许，其余 action EE专属 Gate.

    正确分流思路（避免 self.action 在 initialize_request 之前为 <NONE> 的问题）：
    不要在 wrapper 里猜测 action，也不要手动 initialize 触发 throttle/permission 副作用；
    而是直接基于 *传入的* request.method + as_view 映射来推断 action：
      - POST + 无 pk = create  → 放行
      - 其他所有情况（list/retrieve/update/partial_update/destroy/自定义 action）→ EE Gate
    """
    gate_decorator = _cl_require_feature(_F_CL_GATE)

    @_cl_wraps(view_fn)
    def _dispatch_wrapper(*args, **kwargs):
        if len(args) < 2:
            return view_fn(*args, **kwargs)
        self, request = args[0], args[1]
        rest_args = args[2:]

        method = getattr(request, 'method', '').upper()
        # create: POST / 无 pk
        pk = kwargs.get('pk') if isinstance(kwargs, dict) else None
        is_create = (method == 'POST' and pk is None and (not rest_args))
        if is_create:
            return view_fn(self, request, *rest_args, **kwargs)

        def _bypass_view(req, *a, **kw):
            return view_fn(self, req, *a, **kw)
        return gate_decorator(_bypass_view)(request, *rest_args, **kwargs)

    return _dispatch_wrapper


class HostLogViewSet(CustomModelViewSet):
    """HostLog管理(Supervisor remoteLog转发) — EE (F_HOST_LOG_FORWARDING) 专属"""
    def dispatch(self, request, *args, **kwargs):
        @_hl_require_feature(_F_HL_LOG)
        def _inner(_req, *_a, **_kw):
            return super().dispatch(_req, *_a, **_kw)
        return _inner(request, *args, **kwargs)

    queryset = HostLog.objects.all()
    serializer_class = HostLogSerializer
    search_fields = ['message', 'program_name', 'host__host_name', 'host__host_ip']
    filterset_fields = ['host', 'log_level', 'program_name']
    ordering_fields = ['log_time', 'create_datetime']
    ordering = ['-log_time']

    def get_queryset(self):
        queryset = super().get_queryset()
        # 普通User只能看到authorizationHost/Log
        if not self.request.user.is_superuser:
            queryset = queryset.filter(host__users=self.request.user)
        return queryset

    @action(detail=False, methods=['post'], permission_classes=[AllowAny])
    def receive(self, request):
        """receive Supervisor 上报/Log(无需认证, requireSignatureValidation)"""
        serializer = HostLogReceiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        host_id = serializer.validated_data['host_id']
        logs = serializer.validated_data['logs']

        try:
            host = Host.objects.get(host_uuid=host_id, status=1)
        except Host.DoesNotExist:
            return ErrorResponse(msg="Host does not exist or not approved")

        # Verify request signature (if enabled on Server)
        if getattr(settings, 'REQUEST_SIGNING_ENABLED', False) and host.request_signing_secret:
            from taurus.utils.signing import verify_signature

            client_signature = request.data.get('signature')
            client_nonce = request.data.get('nonce')
            client_timestamp = request.data.get('timestamp_int')

            if not client_signature or not client_nonce or not client_timestamp:
                logger.warning("[LogReport] Signature verification failed: missing signed params host_uuid=%s", host_id)
                return ErrorResponse(msg="Request missing signature parameters")

            import json
            body_data = dict(request.data)
            body_data['signature'] = ''
            body_str = json.dumps(body_data, sort_keys=True)

            is_valid, error_msg = verify_signature(
                host_id=host_id,
                secret=host.request_signing_secret,
                timestamp=int(client_timestamp),
                nonce=client_nonce,
                signature=client_signature,
                body=body_str,
            )

            if not is_valid:
                logger.warning("[LogReport] Signature verification failed: %s host_uuid=%s", error_msg, host_id)
                return ErrorResponse(msg=f"Signature verification failed: {error_msg}")

        if not logs:
            return SuccessResponse(msg="Log list is empty")

        created_count = 0
        from django.utils import timezone as django_timezone

        for log_entry in logs:
            log_level = log_entry.get('log_level', 'INFO').upper()
            log_time = log_entry.get('log_time')
            message = log_entry.get('message', '')

            if not message:
                continue

            try:
                # Parse log_time from string to datetime if provided
                parsed_log_time = django_timezone.now()
                if log_time:
                    try:
                        # Try parsing ISO format first
                        if 'T' in str(log_time):
                            parsed_log_time = datetime.fromisoformat(str(log_time).replace('Z', '+00:00'))
                            # Remove timezone info if USE_TZ is False
                            if not settings.USE_TZ and parsed_log_time.tzinfo is not None:
                                parsed_log_time = parsed_log_time.replace(tzinfo=None)
                        else:
                            # Try common formats
                            for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f'):
                                try:
                                    parsed_log_time = datetime.strptime(str(log_time), fmt)
                                    break
                                except ValueError:
                                    continue
                            else:
                                # Fallback to current time
                                parsed_log_time = django_timezone.now()
                    except Exception:
                        parsed_log_time = django_timezone.now()
                
                HostLog.objects.create(
                    host=host,
                    program_name=log_entry.get('program_name'),
                    log_level=log_level,
                    log_time=parsed_log_time,
                    message=message,
                    source_file=log_entry.get('source_file'),
                    source_line=log_entry.get('source_line'),
                    process_id=log_entry.get('process_id'),
                    thread_id=log_entry.get('thread_id'),
                    extra_info=log_entry.get('extra_info', {}),
                )
                created_count += 1
            except Exception as e:
                logger.warning(f"[LogReport] Failed to create log record: {e}")

        logger.info(
            "[LogReport] Received host logs: host_id=%s, count=%d",
            host_id, created_count,
        )

        return SuccessResponse(data={'received_count': created_count}, msg=f"Received {created_count} logs")


class LogCommandViewSet(CustomModelViewSet):
    """Log收集控制指令管理 — EE (F_LOG_COMMAND_CONTROL) 专属"""
    def dispatch(self, request, *args, **kwargs):
        @_lc_require_feature(_F_LC_CMD)
        def _inner(_req, *_a, **_kw):
            return super().dispatch(_req, *_a, **_kw)
        return _inner(request, *args, **kwargs)

    queryset = LogCommand.objects.all()
    serializer_class = LogCommandSerializer
    create_serializer_class = LogCommandCreateSerializer
    search_fields = ['host__host_name', 'host__host_ip']
    filterset_fields = ['host', 'action', 'status']
    ordering_fields = ['create_datetime', 'dispatched_at']
    ordering = ['-create_datetime']

    def get_queryset(self):
        queryset = super().get_queryset()
        if not self.request.user.is_superuser:
            queryset = queryset.filter(host__users=self.request.user)
        return queryset


class RegistrationTokenViewSet(CustomModelViewSet):
    """RegisterToken管理"""
    queryset = RegistrationToken.objects.all()
    serializer_class = RegistrationTokenSerializer
    search_fields = ['name', 'description']
    filterset_fields = ['is_active', 'auto_approve']
    ordering_fields = ['create_datetime', 'expires_at']
    ordering = ['-create_datetime']

    @action(detail=True, methods=['post'])
    def revoke(self, request, pk=None):
        """UndoToken"""
        token = self.get_object()
        token.is_active = False
        token.save()
        return SuccessResponse(msg="Token revoked")


class HeartbeatServerViewSet(CustomModelViewSet):
    """HeartbeatServiceserver管理"""
    queryset = HeartbeatServer.objects.all()
    serializer_class = HeartbeatServerSerializer
    search_fields = ['name', 'address']
    filterset_fields = ['is_active', 'subnet']
    ordering_fields = ['weight', 'current_connections', 'create_datetime']
    ordering = ['-weight']


# ============================================================================
# [M2.4 Thin Wrapper] Supervisor 程序管理 5 个 ViewSet — 4 EE 专属（全 CRUD Gate）
#   加 1 个（ProgramCommandViewSet）保留基础 CRUD，仅 batch_create 在 host_ids>1 时走 EE Gate.
# Thin Wrapper 范式：class XXXXViewSet(CustomModelViewSet): 保留原 queryset/serializer_class
#   + 外层 @method_decorator(require_feature(F_*), name='dispatch') Double Gate
#   + 原 action body (apply_to_hosts / install / uninstall / batch_create / redispatch /
#     apply / preview_hosts / upgrade_version) 改成：首行 ee_service_or_403(program_policy_engine)
#     → 调用 ProgramPolicyEngine 的对应静态方法。
# ============================================================================
from django.utils.decorators import method_decorator as _svp_method_decorator
from taurus.editions.loader import require_feature as _svp_require_feature
from taurus.editions.features import (
    F_PROGRAM_INSTALL_TEMPLATE as _F_PIT,
    F_PROGRAM_HOST_BINDING as _F_PHB,
    F_PROGRAM_INSTALL_CONFIG as _F_PIC,
    F_PROGRAM_COMMAND_BATCH as _F_PCB,
    F_PROGRAM_INSTALL_POLICY as _F_PIP,
)


@_svp_method_decorator(_svp_require_feature(_F_PIT), name='dispatch')
class ProgramInstallTemplateViewSet(CustomModelViewSet):
    """Program installtemplate管理 (EE: F_PROGRAM_INSTALL_TEMPLATE)
    list:Query create:Create update:modify retrieve:单例 destroy:Delete"""
    queryset = ProgramInstallTemplate.objects.all()
    serializer_class = ProgramInstallTemplateSerializer
    create_serializer_class = ProgramInstallTemplateCreateSerializer
    update_serializer_class = ProgramInstallTemplateUpdateSerializer
    search_fields = ['name', 'program_name', 'version', 'description']
    filterset_fields = ['program_name', 'auto_start', 'user', 'group']
    ordering_fields = ['create_datetime', 'update_datetime']
    ordering = ['-create_datetime']

    @action(methods=['POST'], detail=True)
    def apply_to_hosts(self, request, pk=None):
        """EE: 将模板应用到多主机（调用 program_policy_engine）."""
        from taurus.ee_registry import ee_registry
        svc = ee_registry.get_service('program_policy_engine')
        ok, data, msg = svc.apply_template_to_hosts(self, request, pk=pk)
        return SuccessResponse(data=data, msg=msg) if ok else ErrorResponse(msg=msg)


@_svp_method_decorator(_svp_require_feature(_F_PHB), name='dispatch')
class ProgramHostBindingViewSet(CustomModelViewSet):
    """Host程序绑定管理 (EE: F_PROGRAM_HOST_BINDING)"""
    queryset = ProgramHostBinding.objects.all()
    serializer_class = ProgramHostBindingSerializer
    create_serializer_class = ProgramHostBindingCreateSerializer
    update_serializer_class = ProgramHostBindingUpdateSerializer
    search_fields = ['host__host_name', 'host__host_ip', 'template__name', 'template__program_name']
    filterset_fields = ['host', 'template', 'installed', 'installing']
    ordering_fields = ['create_datetime', 'update_datetime', 'dispatched_at']
    ordering = ['-create_datetime']

    @action(methods=['POST'], detail=True)
    def install(self, request, pk=None):
        """EE: 触发安装（create install config + 设置 binding.installing）."""
        from taurus.ee_registry import ee_registry
        svc = ee_registry.get_service('program_policy_engine')
        ok, data, msg = svc.host_binding_install(self, request, pk=pk)
        return SuccessResponse(data=data, msg=msg) if ok else ErrorResponse(msg=msg)

    @action(methods=['POST'], detail=True)
    def uninstall(self, request, pk=None):
        """EE: 触发卸载（create remove ProgramCommand）."""
        from taurus.ee_registry import ee_registry
        svc = ee_registry.get_service('program_policy_engine')
        ok, data, msg = svc.host_binding_uninstall(self, request, pk=pk)
        return SuccessResponse(data=data, msg=msg) if ok else ErrorResponse(msg=msg)


@_svp_method_decorator(_svp_require_feature(_F_PIC), name='dispatch')
class ProgramInstallConfigViewSet(CustomModelViewSet):
    """Program installConfig管理 (EE: F_PROGRAM_INSTALL_CONFIG)"""
    queryset = ProgramInstallConfig.objects.all()
    serializer_class = ProgramInstallConfigSerializer
    create_serializer_class = ProgramInstallConfigCreateSerializer
    update_serializer_class = ProgramInstallConfigUpdateSerializer
    search_fields = ['program_name', 'version', 'host__host_name', 'host__host_ip']
    filterset_fields = ['host', 'program_name', 'installed', 'auto_start']
    ordering_fields = ['create_datetime', 'update_datetime']
    ordering = ['-create_datetime']

    @action(methods=['POST'], detail=False)
    def batch_create(self, request):
        """EE: 批量 create install config（多 host 相同 config）."""
        from taurus.ee_registry import ee_registry
        svc = ee_registry.get_service('program_policy_engine')
        ok, data, msg = svc.config_batch_create(self, request)
        return SuccessResponse(data=data, msg=msg) if ok else ErrorResponse(msg=msg)

    @action(methods=['POST'], detail=True)
    def redispatch(self, request, pk=None):
        """EE: 重新下发 install 指令（reset dispatch/reset installing）."""
        from taurus.ee_registry import ee_registry
        svc = ee_registry.get_service('program_policy_engine')
        ok, data, msg = svc.config_redispatch(self, request, pk=pk)
        return SuccessResponse(data=data, msg=msg) if ok else ErrorResponse(msg=msg)


class ProgramCommandViewSet(CustomModelViewSet):
    """程序管理指令管理 — 基础 CRUD 留 CE（COMMAND_SINGLE）；仅 batch_create 在 host_ids len>1 时走 EE PROGRAM_COMMAND_BATCH Gate."""
    queryset = ProgramCommand.objects.all()
    serializer_class = ProgramCommandSerializer
    create_serializer_class = ProgramCommandCreateSerializer
    update_serializer_class = ProgramCommandUpdateSerializer
    search_fields = ['program_name', 'host__host_name', 'host__host_ip']
    filterset_fields = ['host', 'program_name', 'action', 'status']
    ordering_fields = ['create_datetime', 'update_datetime', 'executed_at']
    ordering = ['-create_datetime']

    @action(methods=['POST'], detail=False)
    def batch_create(self, request):
        """批量 create 指令：len(host_ids) == 1 允许 CE (COMMAND_SINGLE)，>1 必须走 EE (COMMAND_BATCH Gate)."""
        host_ids = request.data.get('host_ids') or []
        if isinstance(host_ids, (list, tuple)) and len(host_ids) > 1:
            try:
                from taurus_ee.utils.gate import ee_service_or_403
            except ImportError:
                from taurus.ee_fallback import ee_service_or_403
            svc = ee_service_or_403('program_policy_engine', _F_PCB)
            ok, data, msg = svc.batch_create_command(self, request)
            return SuccessResponse(data=data, msg=msg) if ok else ErrorResponse(msg=msg)
        # CE: len==0/1 场景走最小安全实现，复用 ORM 批量 0/1 条（跟 COMMAND_SINGLE 一致）
        from rest_framework import serializers as _ser_mod
        class _MiniBatchSer(_ser_mod.Serializer):
            host_ids = _ser_mod.ListField(child=_ser_mod.IntegerField(), allow_empty=True, max_length=1)
            program_name = _ser_mod.CharField(max_length=100)
            action = _ser_mod.ChoiceField(choices=['install','upgrade','start','stop','restart','remove'])
            target_version = _ser_mod.CharField(max_length=50, required=False, allow_blank=True)
            config = _ser_mod.DictField(required=False, default=dict)
        s = _MiniBatchSer(data=request.data); s.is_valid(raise_exception=True)
        v = s.validated_data
        created_count = 0
        for hid in v['host_ids']:
            try:
                h = Host.objects.get(id=hid)
            except Host.DoesNotExist:
                continue
            ProgramCommand.objects.create(
                host=h, program_name=v['program_name'], action=v['action'],
                target_version=v.get('target_version') or None,
                config=v.get('config') or {}, creator=request.user,
            )
            created_count += 1
        return SuccessResponse(data={'created_count': created_count}, msg=f"Created {created_count} commands (CE single host)")


@_svp_method_decorator(_svp_require_feature(_F_PIP), name='dispatch')
class ProgramInstallPolicyViewSet(CustomModelViewSet):
    """Program install策略管理 (EE: F_PROGRAM_INSTALL_POLICY)"""
    queryset = ProgramInstallPolicy.objects.all()
    serializer_class = ProgramInstallPolicySerializer
    create_serializer_class = ProgramInstallPolicyCreateSerializer
    update_serializer_class = ProgramInstallPolicyUpdateSerializer
    search_fields = ['name', 'program_name', 'version']
    filterset_fields = ['status', 'auto_apply', 'program_name']
    ordering_fields = ['priority', 'create_datetime', 'update_datetime']
    ordering = ['priority', '-create_datetime']

    @action(methods=['POST'], detail=True)
    def apply(self, request, pk=None):
        """EE: 手动触发策略应用."""
        from taurus.ee_registry import ee_registry
        svc = ee_registry.get_service('program_policy_engine')
        ok, data, msg = svc.policy_apply(self, request, pk=pk)
        return SuccessResponse(data=data, msg=msg) if ok else ErrorResponse(msg=msg)

    @action(methods=['GET'], detail=True)
    def preview_hosts(self, request, pk=None):
        """EE: 预览策略匹配 host 列表."""
        from taurus.ee_registry import ee_registry
        svc = ee_registry.get_service('program_policy_engine')
        ok, data, msg = svc.policy_preview_hosts(self, request, pk=pk)
        return SuccessResponse(data=data, msg=msg) if ok else ErrorResponse(msg=msg)

    @action(methods=['POST'], detail=True)
    def upgrade_version(self, request, pk=None):
        """EE: 批量升级策略版本（为已应用主机创建新版本 config）."""
        from taurus.ee_registry import ee_registry
        svc = ee_registry.get_service('program_policy_engine')
        ok, data, msg = svc.policy_upgrade_version(self, request, pk=pk)
        return SuccessResponse(data=data, msg=msg) if ok else ErrorResponse(msg=msg)



# ==================== 运维中心View集 ====================

import json
import tempfile
import base64
import asyncio
import os
import uuid
from typing import Dict, Optional, Any


def _get_executor_address(host):
    """according to Host object构造 executor gRPC 地址"""
    host_ip = host.host_ip or 'localhost'
    return f"{host_ip}:50051"


def _get_host_by_id(host_id):
    """according to host_id QueryHost, support整数primary key, UUID Format 或 IP 地址(手动InputIP时)"""
    if host_id is None:
        return None
    try:
        host_id_str = str(host_id).strip()
    except Exception:
        return None
    if not host_id_str:
        return None

    host = None
    # 1. 整数primary keyQuery
    try:
        host = Host.objects.get(id=int(host_id_str), status=1)
    except (ValueError, TypeError, Host.DoesNotExist):
        host = None

    # 2. UUID Query
    if host is None:
        import uuid as _uuid
        try:
            _uuid.UUID(host_id_str)
            host = Host.objects.get(host_uuid=host_id_str, status=1)
        except (ValueError, Host.DoesNotExist):
            host = None

    # 3. IP 地址 / Host名精确Query(手动InputIP场景)
    if host is None:
        try:
            from django.db.models import Q
            host = Host.objects.filter(
                Q(host_ip__iexact=host_id_str) | Q(host_name__iexact=host_id_str),
                status=1
            ).first()
        except Exception:
            host = None

    return host


def _run_async(coro):
    """在同步Context中run异步Coroutine"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # 已有Event loop在run(如 Django ASGI), create新line程run
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    else:
        return asyncio.run(coro)
def _resolve_approval_context(request):
    """
    从 request.data ParseApprovalrelatedContext:
    return (need_audit, auto_notify, approval_mode, approver_ids, countersign_ids,
          submit_desc, candidate_approvers_list)
    """
    from django.contrib.auth import get_user_model

    need_audit = bool(request.data.get('need_audit', False))
    auto_notify = bool(request.data.get('auto_notify', False))

    approver_ids = [int(x) for x in (request.data.get('approver_ids') or []) if str(x).isdigit()]
    countersign_ids = [int(x) for x in (request.data.get('countersign_ids') or []) if str(x).isdigit()]
    mode = (request.data.get('approval_mode') or '').strip()
    if countersign_ids and not mode:
        mode = 'all'
    if mode not in ('any', 'all'):
        mode = 'any' if (approver_ids or countersign_ids) else None

    submit_desc = request.data.get('submit_desc', '') or None

    candidate_approvers = []
    if approver_ids or countersign_ids:
        User = get_user_model()
        candidate_ids_set = set(approver_ids) | set(countersign_ids)
        for u in User.objects.filter(id__in=list(candidate_ids_set)):
            candidate_approvers.append({
                'user_id': u.id,
                'username': u.username,
                'name': getattr(u, 'name', u.username),
            })

    return need_audit, auto_notify, mode, approver_ids, countersign_ids, submit_desc, candidate_approvers



class OpsViewSet(viewsets.ViewSet):
    """
    运维中心 - CommandExecution, ScriptExecution, FileUpload

    via gRPC SDK 调用 taurus-executor Executionoperation.
    Command/ScriptExecutionadopt WebSocket 模式(与 web_ui.py 一致):
    1. POST requeststartExecution, return execution_id
    2. Frontendvia WebSocket joinFetch实时Output
    """

    @action(methods=['POST'], detail=False)
    def execute_command(self, request):
        """
        Execution单entriesCommand, return execution_id, Frontendvia WebSocket FetchOutput
        
        POST /api/taurus/ops/execute_command/
        {
            "host_id": "uuid",
            "command": "ls -la",
            "args": [],
            "working_directory": "/tmp",
            "timeout_seconds": 300,
            "environment": {},
            "use_shell": false,
            "merge_streams": false,
            "privileged": false
        }
        
        Response: {"execution_id": "uuid"}
        WebSocket: ws://host/ws/ops/<execution_id>/
        """
        serializer = OpsCommandExecuteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # === 配额校验：社区版最大并发执行数 ===
        from taurus.editions.loader import check_quota as _check_quota
        from taurus.models import OpsExecution as _OpsExec
        _running = _OpsExec.objects.filter(status__in=[0, 1, 5]).count()
        _check_quota('max_concurrent_executions', _running, '并发执行任务')

        host_id = serializer.validated_data['host_id']
        command = serializer.validated_data['command']
        args = serializer.validated_data.get('args', [])
        working_directory = serializer.validated_data.get('working_directory', '')
        timeout_seconds = serializer.validated_data.get('timeout_seconds', 300)
        environment = serializer.validated_data.get('environment', {})
        use_shell_explicit = serializer.validated_data.get('use_shell', None)
        merge_streams = serializer.validated_data.get('merge_streams', False)
        load_profile = serializer.validated_data.get('load_profile', 'false')
        privileged = serializer.validated_data.get('privileged', False)
        su_user = serializer.validated_data.get('su_user', None)
        su_password = serializer.validated_data.get('su_password', None)
        
        # according to host_id QueryHost(support整数primary key或 UUID)
        host = _get_host_by_id(host_id)
        if host is None:
            return ErrorResponse(msg="Host does not exist or not approved")
        
        if host.online_status != 1:
            return ErrorResponse(msg="Host is offline, cannot execute command")
        
        address = _get_executor_address(host)
        
        if use_shell_explicit is not None:
            use_shell = use_shell_explicit
        else:
            use_shell = True
        
        execution_id = str(uuid.uuid4())
        batch_id = serializer.validated_data.get('batch_id', None)
        
        if privileged:
            environment = dict(environment) if environment else {}
            environment['PRIVILEGED_EXECUTION'] = 'true'
            if su_user:
                environment['SU_USER'] = su_user
            if su_password:
                # Encryption后持久化, Avoid明文密码落library
                environment['SU_PASSWORD'] = encrypt_value(su_password)
        
        from taurus.models import OpsExecution


        exec_mode = request.data.get('exec_mode', 'parallel')
        concurrent = int(request.data.get('concurrent', 10))
        fail_strategy = request.data.get('fail_strategy', 'continue')
        pilot_count = int(request.data.get('pilot_count', 2))
        pilot_success_rate = int(request.data.get('pilot_success_rate', 100))

        (need_audit, auto_notify, _approval_mode,
         _approver_ids, _countersign_ids, _submit_desc, candidate_approvers) = _resolve_approval_context(request)

        if need_audit:
            execution_status = 5
        else:
            execution_status = 0

        ops_execution = OpsExecution.objects.create(
            execution_id=execution_id,
            batch_id=batch_id,
            execution_type='command',
            host=host,
            user=request.user,
            command=command,
            args=args,
            working_directory=working_directory,
            timeout_seconds=timeout_seconds,
            environment=environment,
            use_shell=use_shell,
            merge_streams=merge_streams,
            load_profile=load_profile,
            privileged=privileged,
            su_user=su_user,
            exec_mode=exec_mode,
            concurrent=concurrent,
            fail_strategy=fail_strategy,
            pilot_count=pilot_count,
            pilot_success_rate=pilot_success_rate,
            need_audit=need_audit,
            auto_notify=auto_notify,
            approval_mode=_approval_mode,
            approver_ids=_approver_ids,
            countersign_ids=_countersign_ids,
            submit_desc=_submit_desc,
            status=execution_status,
        )

        if need_audit:
            from taurus.models import OpsExecutionApproval
            if not batch_id:
                batch_id = f"APPROVAL{timezone.now().strftime('%Y%m%d%H%M%S')}"
            approval = OpsExecutionApproval.objects.create(
                batch_id=batch_id,
                status='pending',
                source_type='command',
                related_name=command[:200] if command else '',
                related_desc=_submit_desc or '',
                submitter=request.user,
                submitter_name=getattr(request.user, 'username', '') or getattr(request.user, 'name', ''),
                submit_desc=_submit_desc or '',
                approval_mode=_approval_mode if _approval_mode in ('any', 'all') else 'any',
                candidate_approvers=candidate_approvers,
                approval_records=[],
                host=host,
                execution_type='command',
                command=command,
                use_shell=use_shell,
                script_type=None,
                script_content=None,
                args=args,
                working_directory=working_directory or None,
                timeout_seconds=timeout_seconds,
                environment=environment,
                merge_streams=merge_streams,
                load_profile=load_profile,
                privileged=privileged,
                su_user=su_user,
                exec_mode=exec_mode,
                concurrency=concurrent,
                fail_strategy=fail_strategy,
                pilot_count=pilot_count,
                pilot_success_rate=pilot_success_rate,
                auto_notify=auto_notify,
                target_hosts_count=int(request.data.get('target_hosts_count', 1)),
                ops_execution=ops_execution,
            )
            logger.info(f"[execute_command] Entered approval flow: execution_id={execution_id}, approval_id={approval.id}")
            return DetailResponse(data={
                'execution_id': execution_id,
                'approval_id': approval.id,
                'batch_id': approval.batch_id,
                'pending_approval': True,
                'approval_mode': approval.approval_mode,
                'candidate_approvers': approval.candidate_approvers,
            }, msg="Submitted for approval, awaiting review")

        try:
            from taurus.websocket_async import get_event_loop, submit_execution, _execute_ops_async
            loop = get_event_loop()
            if loop is not None and not loop.is_closed():
                submitted = submit_execution(execution_id, _execute_ops_async(execution_id))
                if submitted:
                    logger.info(f"[execute_command] Immediate trigger execution: {execution_id}")
                else:
                    logger.info(f"[execute_command] Immediate submit failed, will poll as fallback: {execution_id}")
            else:
                logger.debug(f"[execute_command] WebSocket event loop unavailable (standalone Django), polling fallback: {execution_id}")
        except Exception as trigger_e:
            logger.warning(f"[execute_command] Immediate trigger exception, polling fallback: {trigger_e}")

        return DetailResponse(data={'execution_id': execution_id})


    @action(methods=['POST'], detail=False)
    def execute_script(self, request):
        """
        ExecutionScript, return execution_id, Frontendvia WebSocket FetchOutput
        
        POST /api/taurus/ops/execute_script/
        {
            "host_id": "uuid",
            "script_type": "sh",
            "script_content": "#!/bin/bash\necho hello",
            "args": [],
            "environment": {},
            "timeout_seconds": 300
        }
        
        Response: {"execution_id": "uuid"}
        WebSocket: ws://host/ws/ops/<execution_id>/
        """
        serializer = OpsScriptExecuteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # === 配额校验：社区版最大并发执行数 ===
        from taurus.editions.loader import check_quota as _check_quota
        from taurus.models import OpsExecution as _OpsExec
        _running = _OpsExec.objects.filter(status__in=[0, 1, 5]).count()
        _check_quota('max_concurrent_executions', _running, '并发执行任务')

        host_id = serializer.validated_data['host_id']
        script_type = serializer.validated_data['script_type']
        script_content = serializer.validated_data['script_content']
        args = serializer.validated_data.get('args', [])
        working_directory = serializer.validated_data.get('working_directory', '')
        environment = serializer.validated_data.get('environment', {})
        timeout_seconds = serializer.validated_data.get('timeout_seconds', 300)
        merge_streams = serializer.validated_data.get('merge_streams', False)
        load_profile = serializer.validated_data.get('load_profile', 'false')
        privileged = serializer.validated_data.get('privileged', False)
        su_user = serializer.validated_data.get('su_user', None)
        su_password = serializer.validated_data.get('su_password', None)
        
        try:
            host = _get_host_by_id(host_id)
            if host is None:
                return ErrorResponse(msg="Host does not exist or not approved")
        except Exception:
            return ErrorResponse(msg="Host does not exist or not approved")
        
        if host.online_status != 1:
            return ErrorResponse(msg="Host is offline, cannot execute script")
        
        # according toScript type确定interpretserver
        interpreter_map = {
            'sh': '/bin/bash',
            'python': '/usr/bin/python3',
        }
        interpreter = interpreter_map.get(script_type)
        if not interpreter:
            return ErrorResponse(msg=f"Unsupported script type: {script_type}")
        
        execution_id = str(uuid.uuid4())
        batch_id = serializer.validated_data.get('batch_id', None)

        if privileged:
            environment = dict(environment) if environment else {}
            environment['PRIVILEGED_EXECUTION'] = 'true'
            if su_user:
                environment['SU_USER'] = su_user
            if su_password:
                # Encryption后持久化, Avoid明文密码落library
                environment['SU_PASSWORD'] = encrypt_value(su_password)

        from taurus.models import OpsExecution


        exec_mode = request.data.get('exec_mode', 'parallel')
        concurrent = int(request.data.get('concurrent', 10))
        fail_strategy = request.data.get('fail_strategy', 'continue')
        pilot_count = int(request.data.get('pilot_count', 2))
        pilot_success_rate = int(request.data.get('pilot_success_rate', 100))

        (need_audit, auto_notify, _approval_mode,
         _approver_ids, _countersign_ids, _submit_desc, candidate_approvers) = _resolve_approval_context(request)

        if need_audit:
            execution_status = 5
        else:
            execution_status = 0

        ops_execution = OpsExecution.objects.create(
            execution_id=execution_id,
            batch_id=batch_id,
            execution_type='script',
            host=host,
            user=request.user,
            script_type=script_type,
            script_content=script_content,
            args=args,
            working_directory=working_directory,
            timeout_seconds=timeout_seconds,
            environment=environment,
            use_shell=True,
            merge_streams=merge_streams,
            load_profile=load_profile,
            privileged=privileged,
            su_user=su_user,
            exec_mode=exec_mode,
            concurrent=concurrent,
            fail_strategy=fail_strategy,
            pilot_count=pilot_count,
            pilot_success_rate=pilot_success_rate,
            need_audit=need_audit,
            auto_notify=auto_notify,
            approval_mode=_approval_mode,
            approver_ids=_approver_ids,
            countersign_ids=_countersign_ids,
            submit_desc=_submit_desc,
            status=execution_status,
        )

        if need_audit:
            from taurus.models import OpsExecutionApproval
            if not batch_id:
                batch_id = f"APPROVAL{timezone.now().strftime('%Y%m%d%H%M%S')}"
            approval = OpsExecutionApproval.objects.create(
                batch_id=batch_id,
                status='pending',
                source_type='script',
                related_name=request.data.get('script_name', '') or '',
                related_desc=_submit_desc or request.data.get('script_desc', '') or '',
                submitter=request.user,
                submitter_name=getattr(request.user, 'username', '') or getattr(request.user, 'name', ''),
                submit_desc=_submit_desc or '',
                approval_mode=_approval_mode if _approval_mode in ('any', 'all') else 'any',
                candidate_approvers=candidate_approvers,
                approval_records=[],
                host=host,
                execution_type='script',
                command=None,
                use_shell=True,
                script_type=script_type,
                script_content=script_content,
                args=args,
                working_directory=working_directory or None,
                timeout_seconds=timeout_seconds,
                environment=environment,
                merge_streams=merge_streams,
                load_profile=load_profile,
                privileged=privileged,
                su_user=su_user,
                exec_mode=exec_mode,
                concurrency=concurrent,
                fail_strategy=fail_strategy,
                pilot_count=pilot_count,
                pilot_success_rate=pilot_success_rate,
                auto_notify=auto_notify,
                target_hosts_count=int(request.data.get('target_hosts_count', 1)),
                ops_execution=ops_execution,
            )
            logger.info(f"[execute_script] Entered approval flow: execution_id={execution_id}, approval_id={approval.id}")
            return DetailResponse(data={
                'execution_id': execution_id,
                'approval_id': approval.id,
                'batch_id': approval.batch_id,
                'pending_approval': True,
                'approval_mode': approval.approval_mode,
                'candidate_approvers': approval.candidate_approvers,
            }, msg="Submitted for approval, awaiting review")

        try:
            from taurus.websocket_async import get_event_loop, submit_execution, _execute_ops_async
            loop = get_event_loop()
            if loop is not None and not loop.is_closed():
                submitted = submit_execution(execution_id, _execute_ops_async(execution_id))
                if submitted:
                    logger.info(f"[execute_script] Immediate trigger execution: {execution_id}")
                else:
                    logger.info(f"[execute_script] Immediate submit failed, will poll as fallback: {execution_id}")
            else:
                logger.debug(f"[execute_script] WebSocket event loop unavailable (standalone Django), polling fallback: {execution_id}")
        except Exception as trigger_e:
            logger.warning(f"[execute_script] Immediate trigger exception, polling fallback: {trigger_e}")

        return DetailResponse(data={'execution_id': execution_id})


    @action(methods=['POST'], detail=False)
    def submit_script_approval(self, request):
        """
        CommitScriptExecutionApproval(Script开启Approval流程时调用).
        supportCommit时dynamic指定审核人或Countersign人:
        - approver_ids: [1,2]     Or-sign审核人(任一人via即可)
        - countersign_ids: [1,2]  Countersign审核人(Allvia才可Execution)
        - approval_mode: 'any' | 'all'  若未指定, 则传入 approver_ids 默认为 any, 传入 countersign_ids 则自动切换为 all
        - 两  ID listcan混用(并集作为Candidates;countersign_ids 存在时默认 approval_mode=all)

        POST /api/taurus/ops/submit_script_approval/
        {
            "host_id": "uuid",
            "script_type": "sh",
            "script_content": "#!/bin/bash\necho hello",
            "args": [],
            "environment": {},
            "working_directory": "",
            "timeout_seconds": 300,
            "merge_streams": false,
            "load_profile": "false",
            "privileged": false,
            "su_user": null,
            "submit_desc": "Run system inspection script",
            "batch_id": "BATCHxxx",
            "approver_ids": [10, 11],
            "countersign_ids": [20, 21]
        }

        Response: {"approval_id": 1, "batch_id": "BATCHxxx"}
        """
        serializer = OpsScriptExecuteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        host_id = serializer.validated_data['host_id']
        try:
            host = _get_host_by_id(host_id)
            if host is None:
                return ErrorResponse(msg="Host does not exist or not approved")
        except Exception:
            return ErrorResponse(msg="Host does not exist or not approved")

        batch_id = serializer.validated_data.get('batch_id') or f"APPROVAL{timezone.now().strftime('%Y%m%d%H%M%S')}"
        submit_desc = request.data.get('submit_desc', '')

        # ===== Parsedynamicspecified审核人 =====
        from django.contrib.auth import get_user_model
        User = get_user_model()

        approver_ids = [int(x) for x in (request.data.get('approver_ids') or []) if str(x).isdigit()]
        countersign_ids = [int(x) for x in (request.data.get('countersign_ids') or []) if str(x).isdigit()]
        mode = (request.data.get('approval_mode') or '').strip()

        if countersign_ids and not mode:
            mode = 'all'
        if mode not in ('any', 'all'):
            mode = 'any'

        candidate_ids_set = set(approver_ids) | set(countersign_ids)
        candidate_approvers = []
        if candidate_ids_set:
            for u in User.objects.filter(id__in=list(candidate_ids_set)):
                candidate_approvers.append({
                    'user_id': u.id,
                    'username': u.username,
                    'name': getattr(u, 'name', u.username),
                })

        approval = OpsExecutionApproval.objects.create(
            batch_id=batch_id,
            status='pending',
            source_type='script',
            related_name=request.data.get('script_name', '') or '',
            related_desc=submit_desc or request.data.get('script_desc', '') or '',
            submitter=request.user,
            submitter_name=getattr(request.user, 'username', '') or getattr(request.user, 'name', ''),
            submit_desc=submit_desc,
            approval_mode=mode,
            candidate_approvers=candidate_approvers,
            approval_records=[],
            host=host,
            script_type=serializer.validated_data['script_type'],
            script_content=serializer.validated_data['script_content'],
            args=serializer.validated_data.get('args', []),
            working_directory=serializer.validated_data.get('working_directory') or None,
            timeout_seconds=serializer.validated_data.get('timeout_seconds', 300),
            environment=serializer.validated_data.get('environment', {}),
            merge_streams=serializer.validated_data.get('merge_streams', False),
            load_profile=serializer.validated_data.get('load_profile', 'false'),
            privileged=serializer.validated_data.get('privileged', False),
            su_user=serializer.validated_data.get('su_user'),
            exec_mode=request.data.get('exec_mode', 'parallel'),
            concurrency=request.data.get('concurrency', 10),
            target_hosts_count=request.data.get('target_hosts_count', 1),
        )

        return DetailResponse(data={
            'approval_id': approval.id,
            'batch_id': approval.batch_id,
            'approval_mode': approval.approval_mode,
            'candidate_approvers': approval.candidate_approvers,
        }, msg="Submitted for approval, awaiting review")

    @action(methods=['POST'], detail=False)
    def terminate_command(self, request):
        """
        终止正在Execution/Command

        POST /api/taurus/ops/terminate_command/
        {
            "host_id": "uuid",
            "execution_id": "uuid"
        }
        """
        host_id = request.data.get('host_id')
        execution_id = request.data.get('execution_id')
        if not host_id or not execution_id:
            return ErrorResponse(msg="host_id and execution_id are required")

        host = _get_host_by_id(host_id)
        if host is None:
            return ErrorResponse(msg="Host does not exist or not approved")

        address = _get_executor_address(host)

        async def do_terminate():
            from taurus.sdk import TaurusClient
            from django.conf import settings
            sdk_cert_dir = getattr(settings, 'SDK_CERT_DIR', None)
            client_kwargs = {
                'connect_timeout': int(getattr(settings, 'EXECUTOR_CONNECT_TIMEOUT', 5)),
            }
            if sdk_cert_dir:
                client_kwargs['cert_file'] = os.path.join(sdk_cert_dir, 'client.crt')
                client_kwargs['key_file'] = os.path.join(sdk_cert_dir, 'client.key')
                client_kwargs['ca_file'] = os.path.join(sdk_cert_dir, 'ca.crt')
            async with TaurusClient(address, **client_kwargs) as client:
                executions = await client.list_executions()
                pid = None
                for exec_info in executions:
                    if exec_info['execution_id'] == execution_id:
                        pid = exec_info['pid']
                        break
                if not pid or pid <= 0:
                    return {'success': False, 'message': 'Execution/process not found'}
                return await client.send_signal(pid, 9)

        try:
            result = _run_async(do_terminate())
            if result.get('success'):
                from taurus.models import OpsExecution
                qs = OpsExecution.objects.filter(execution_id=execution_id)
                rec = qs.first()
                batch_id = getattr(rec, 'batch_id', None) if rec else None
                qs.update(
                    status=3, exit_code=1, error_message='User terminated', finished_at=timezone.now()
                )
                # User终止后, 若这YesScheduled task/一part, AttemptaggregateUpdate整体Status
                if batch_id:
                    try:
                        from taurus.websocket_async import _try_finalize_script_task_execution
                        _try_finalize_script_task_execution(batch_id)
                    except Exception as agg_e:
                        logger.error(f"Failed to aggregate scheduled task status after user termination: {agg_e}")
                return SuccessResponse(msg="CommandTerminated")
            return ErrorResponse(msg=result.get('message', 'Terminate failed'))
        except Exception as e:
            logger.error(f"Exception terminating command: {e}")
            return ErrorResponse(msg=f"Terminate command failed: {str(e)}")

    @action(methods=['POST'], detail=False)
    def pause_command(self, request):
        """
        pause正在Execution/Command (SIGSTOP)

        POST /api/taurus/ops/pause_command/
        {
            "host_id": "uuid",
            "execution_id": "uuid"
        }
        """
        host_id = request.data.get('host_id')
        execution_id = request.data.get('execution_id')
        if not host_id or not execution_id:
            return ErrorResponse(msg="host_id and execution_id are required")

        host = _get_host_by_id(host_id)
        if host is None:
            return ErrorResponse(msg="Host does not exist or not approved")

        address = _get_executor_address(host)

        async def do_pause():
            from taurus.sdk import TaurusClient
            from django.conf import settings
            sdk_cert_dir = getattr(settings, 'SDK_CERT_DIR', None)
            client_kwargs = {
                'connect_timeout': int(getattr(settings, 'EXECUTOR_CONNECT_TIMEOUT', 5)),
            }
            if sdk_cert_dir:
                client_kwargs['cert_file'] = os.path.join(sdk_cert_dir, 'client.crt')
                client_kwargs['key_file'] = os.path.join(sdk_cert_dir, 'client.key')
                client_kwargs['ca_file'] = os.path.join(sdk_cert_dir, 'ca.crt')
            async with TaurusClient(address, **client_kwargs) as client:
                executions = await client.list_executions()
                pid = None
                for exec_info in executions:
                    if exec_info['execution_id'] == execution_id:
                        pid = exec_info['pid']
                        break
                if not pid or pid <= 0:
                    return {'success': False, 'message': 'Execution/process not found'}
                return await client.send_signal(pid, 19)

        try:
            result = _run_async(do_pause())
            if result.get('success'):
                return SuccessResponse(msg="CommandPaused")
            return ErrorResponse(msg=result.get('message', 'Pause failed'))
        except Exception as e:
            logger.error(f"Exception pausing command: {e}")
            return ErrorResponse(msg=f"Pause command failed: {str(e)}")

    @action(methods=['POST'], detail=False)
    def resume_command(self, request):
        """
        Restorepause/Command (SIGCONT)

        POST /api/taurus/ops/resume_command/
        {
            "host_id": "uuid",
            "execution_id": "uuid"
        }
        """
        host_id = request.data.get('host_id')
        execution_id = request.data.get('execution_id')
        if not host_id or not execution_id:
            return ErrorResponse(msg="host_id and execution_id are required")

        host = _get_host_by_id(host_id)
        if host is None:
            return ErrorResponse(msg="Host does not exist or not approved")

        address = _get_executor_address(host)

        async def do_resume():
            from taurus.sdk import TaurusClient
            from django.conf import settings
            sdk_cert_dir = getattr(settings, 'SDK_CERT_DIR', None)
            client_kwargs = {
                'connect_timeout': int(getattr(settings, 'EXECUTOR_CONNECT_TIMEOUT', 5)),
            }
            if sdk_cert_dir:
                client_kwargs['cert_file'] = os.path.join(sdk_cert_dir, 'client.crt')
                client_kwargs['key_file'] = os.path.join(sdk_cert_dir, 'client.key')
                client_kwargs['ca_file'] = os.path.join(sdk_cert_dir, 'ca.crt')
            async with TaurusClient(address, **client_kwargs) as client:
                executions = await client.list_executions()
                pid = None
                for exec_info in executions:
                    if exec_info['execution_id'] == execution_id:
                        pid = exec_info['pid']
                        break
                if not pid or pid <= 0:
                    return {'success': False, 'message': 'Execution/process not found'}
                return await client.send_signal(pid, 18)

        try:
            result = _run_async(do_resume())
            if result.get('success'):
                return SuccessResponse(msg="Command resumed")
            return ErrorResponse(msg=result.get('message', 'Resume failed'))
        except Exception as e:
            logger.error(f"Exception resuming command: {e}")
            return ErrorResponse(msg=f"Resume command failed: {str(e)}")

    @action(methods=['GET'], detail=False)
    def list_executions(self, request):
        """
        列出指定Host上正在Execution/Command

        GET /api/taurus/ops/list_executions/?host_id=uuid
        """
        host_id = request.query_params.get('host_id')
        if not host_id:
            return ErrorResponse(msg="host_id Required")

        host = _get_host_by_id(host_id)
        if host is None:
            return ErrorResponse(msg="Host does not exist or not approved")

        address = _get_executor_address(host)

        async def do_list():
            from taurus.sdk import TaurusClient
            from django.conf import settings
            sdk_cert_dir = getattr(settings, 'SDK_CERT_DIR', None)
            client_kwargs = {
                'connect_timeout': int(getattr(settings, 'EXECUTOR_CONNECT_TIMEOUT', 5)),
            }
            if sdk_cert_dir:
                client_kwargs['cert_file'] = os.path.join(sdk_cert_dir, 'client.crt')
                client_kwargs['key_file'] = os.path.join(sdk_cert_dir, 'client.key')
                client_kwargs['ca_file'] = os.path.join(sdk_cert_dir, 'ca.crt')
            async with TaurusClient(address, **client_kwargs) as client:
                return await client.list_executions()

        try:
            executions = _run_async(do_list())
            return SuccessResponse(data=executions)
        except Exception as e:
            logger.error(f"Exception listing executions: {e}")
            return ErrorResponse(msg=f"Failed to fetch execution list: {str(e)}")

    @action(methods=['GET'], detail=False)
    def execution_history(self, request):
        """
        QueryExecution历史record

        GET /api/taurus/ops/execution_history/?host_id=uuid&page=1&limit=20
        """
        from taurus.models import OpsExecution

        host_id = request.query_params.get('host_id')
        page = int(request.query_params.get('page', 1))
        limit = int(request.query_params.get('limit', 20))

        queryset = OpsExecution.objects.all().order_by('-create_datetime')
        if host_id:
            queryset = queryset.filter(host_id=host_id)
        # 排除WorkflowTrigger/Executionrecord
        queryset = queryset.exclude(batch_id__startswith='wfex-')

        total = queryset.count()
        items = queryset[(page - 1) * limit: page * limit]

        data = []
        for item in items:
            output_text = ''
            if item.output_buffer:
                for chunk in item.output_buffer:
                    if 'stdout' in chunk:
                        output_text += chunk.get('stdout', '')
                    if 'stderr' in chunk:
                        output_text += chunk.get('stderr', '')
            
            data.append({
                'id': item.id,
                'execution_id': item.execution_id,
                'batch_id': item.batch_id,
                'execution_type': item.execution_type,
                'host_id': str(item.host_id),
                'host_uuid': item.host.host_uuid if item.host else '',
                'host_name': item.host.host_name if item.host else '',
                'host_ip': item.host.host_ip if item.host else '',
                'command': item.command,
                'script_type': item.script_type,
                'status': item.status,
                'status_display': item.get_status_display(),
                'exit_code': item.exit_code,
                'error_message': item.error_message,
                'output': output_text,
                'working_directory': item.working_directory,
                'timeout_seconds': item.timeout_seconds,
                'use_shell': item.use_shell,
                'merge_streams': item.merge_streams,
                'privileged': item.privileged,
                'su_user': item.su_user,
                'started_at': item.started_at.isoformat() if item.started_at else None,
                'finished_at': item.finished_at.isoformat() if item.finished_at else None,
                'create_datetime': item.create_datetime.isoformat() if item.create_datetime else None,
            })

        return SuccessResponse(data={'total': total, 'items': data, 'page': page, 'limit': limit})

    @action(methods=['POST'], detail=False)
    def upload_file(self, request):
        """
        UploadFile到Target host
        
        POST /api/taurus/ops/upload_file/
        Content-Type: multipart/form-data
        {
            "host_id": "uuid",
            "file_path": "/tmp/uploaded_file.txt",
            "file": <file>
        }
        """
        serializer = OpsFileUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        host_id = serializer.validated_data['host_id']
        file_path = serializer.validated_data['file_path']
        uploaded_file = serializer.validated_data['file']

        host = _get_host_by_id(str(host_id))
        if host is None:
            return ErrorResponse(msg="Host does not exist or not approved")

        if host.online_status != 1:
            return ErrorResponse(msg="Host is offline, cannot upload file")

        address = _get_executor_address(host)
        
        # 将Upload/FileSave到temporaryFile, 然后via SDK Upload
        tmp_path = None
        from taurus.models import OpsExecution
        import uuid
        execution_id = str(uuid.uuid4())
        total_size = 0
        try:
            suffix = os.path.basename(file_path)
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                for chunk in uploaded_file.chunks():
                    tmp.write(chunk)
                tmp_path = tmp.name
            
            total_size = os.path.getsize(tmp_path)
            
            async def do_upload():
                from taurus.sdk import TaurusClient
                from django.conf import settings
                sdk_cert_dir = getattr(settings, 'SDK_CERT_DIR', None)
                client_kwargs = {
                    'connect_timeout': int(getattr(settings, 'EXECUTOR_CONNECT_TIMEOUT', 5)),
                }
                if sdk_cert_dir:
                    client_kwargs['cert_file'] = os.path.join(sdk_cert_dir, 'client.crt')
                    client_kwargs['key_file'] = os.path.join(sdk_cert_dir, 'client.key')
                    client_kwargs['ca_file'] = os.path.join(sdk_cert_dir, 'ca.crt')
                async with TaurusClient(address, **client_kwargs) as client:
                    return await client.upload_file(tmp_path, file_path)
            
            result = _run_async(do_upload())
            
            if result.get('success'):
                # recordUploadoperation
                OpsExecution.objects.create(
                    execution_id=execution_id,
                    execution_type='upload',
                    host=host,
                    user=request.user,
                    file_path=file_path,
                    file_size=total_size,
                    status=2,  # Completed
                    exit_code=0,
                )
                return SuccessResponse(
                    data={'file_path': file_path, 'size': total_size},
                    msg=f"File uploaded: {file_path}"
                )
            else:
                error_msg = result.get('message', 'Unknown error')
                OpsExecution.objects.create(
                    execution_id=execution_id,
                    execution_type='upload',
                    host=host,
                    user=request.user,
                    file_path=file_path,
                    file_size=total_size,
                    status=3,  # Failed
                    error_message=error_msg,
                )
                return ErrorResponse(msg=f"File upload failed: {error_msg}")
        except Exception as e:
            logger.error(f"Exception uploading file: {e}")
            OpsExecution.objects.create(
                execution_id=execution_id,
                execution_type='upload',
                host=host,
                user=request.user,
                file_path=file_path,
                file_size=total_size,
                status=3,  # Failed
                error_message=str(e),
            )
            return ErrorResponse(msg=f"File upload failed: {str(e)}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @action(methods=['POST'], detail=False)
    def upload_to_backend_temp(self, request):
        """
        Upload单 File到后端temporarydirectory(用于WorkflowFile分发节./源File)

        POST /api/taurus/ops/upload_to_backend_temp/
        Content-Type: multipart/form-data
        {
            "file": <file>,
            "original_filename": "optional_name.txt"
        }

        Returns: {"file_path": "/tmp/taurus_workflow_uploads/<uuid>/<filename>", "filename": "original_name.txt", "size": 1234, "original_filename": "uploaded_name.txt"}
        """
        import uuid as _uuid
        import tempfile

        serializer = OpsBackendTempUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uploaded_file = serializer.validated_data['file']
        original_filename = serializer.validated_data.get('original_filename', '') or uploaded_file.name or 'unnamed'

        # 安全processFile名:removepath分隔符, 限制长度
        safe_name = os.path.basename(original_filename)
        if not safe_name or safe_name in ('', '.', '..'):
            safe_name = f'file_{_uuid.uuid4().hex[:8]}'
        if len(safe_name) > 200:
            name_part, ext_part = os.path.splitext(safe_name)
            safe_name = name_part[:200 - len(ext_part)] + ext_part

        upload_root = os.path.join(tempfile.gettempdir(), 'taurus_workflow_uploads')
        session_id = _uuid.uuid4().hex
        upload_dir = os.path.join(upload_root, session_id)
        os.makedirs(upload_dir, exist_ok=True)

        dest_path = os.path.join(upload_dir, safe_name)
        total_size = 0
        try:
            with open(dest_path, 'wb') as f:
                for chunk in uploaded_file.chunks():
                    f.write(chunk)
                    total_size += len(chunk)

            # Generate默认Targetpath
            default_target = ''
            default_prefix = request.data.get('default_target_prefix', '').strip()
            if default_prefix:
                default_target = default_prefix.rstrip('/') + '/' + safe_name

            return SuccessResponse(
                data={
                    'file_path': dest_path,
                    'filename': safe_name,
                    'size': total_size,
                    'original_filename': original_filename,
                    'default_target_path': default_target,
                },
                msg='File uploaded'
            )
        except Exception as e:
            logger.error(f"Failed to upload file to backend temp dir: {e}")
            if os.path.exists(dest_path):
                os.unlink(dest_path)
            return ErrorResponse(msg=f'File upload failed: {str(e)}')

    @action(methods=['POST'], detail=False)
    def upload_batch_to_backend_temp(self, request):
        """
        批量Upload多 File到后端temporarydirectory(用于WorkflowFile分发节./源File)

        POST /api/taurus/ops/upload_batch_to_backend_temp/
        Content-Type: multipart/form-data
        {
            "files": [<file1>, <file2>, ...],
            "default_target_prefix": "/opt/app/releases" (Optional)
        }

        Returns: {
            "files": [
                {"file_path": "...", "filename": "...", "size": 123, "default_target_path": "..."},
                ...
            ],
            "session_id": "...",
            "upload_dir": "..."
        }
        """
        import uuid as _uuid
        import tempfile

        serializer = OpsBatchBackendTempUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        files_list = serializer.validated_data['files']
        default_prefix = serializer.validated_data.get('default_target_prefix', '').strip()

        upload_root = os.path.join(tempfile.gettempdir(), 'taurus_workflow_uploads')
        session_id = _uuid.uuid4().hex
        upload_dir = os.path.join(upload_root, session_id)
        os.makedirs(upload_dir, exist_ok=True)

        results = []
        for uploaded_file in files_list:
            try:
                original_filename = uploaded_file.name or 'unnamed'
                safe_name = os.path.basename(original_filename)
                if not safe_name or safe_name in ('', '.', '..'):
                    safe_name = f'file_{_uuid.uuid4().hex[:8]}'
                if len(safe_name) > 200:
                    name_part, ext_part = os.path.splitext(safe_name)
                    safe_name = name_part[:200 - len(ext_part)] + ext_part

                dest_path = os.path.join(upload_dir, safe_name)
                total_size = 0
                with open(dest_path, 'wb') as f:
                    for chunk in uploaded_file.chunks():
                        f.write(chunk)
                        total_size += len(chunk)

                default_target = ''
                if default_prefix:
                    default_target = default_prefix.rstrip('/') + '/' + safe_name

                results.append({
                    'file_path': dest_path,
                    'filename': safe_name,
                    'size': total_size,
                    'original_filename': original_filename,
                    'default_target_path': default_target,
                })
            except Exception as e:
                logger.error(f"Single file failed during batch upload {uploaded_file.name}: {e}")
                results.append({
                    'file_path': '',
                    'filename': uploaded_file.name or 'unnamed',
                    'size': 0,
                    'original_filename': uploaded_file.name or 'unnamed',
                    'default_target_path': '',
                    'error': str(e),
                })

        has_errors = any(r.get('error') for r in results)
        return SuccessResponse(
            data={
                'files': results,
                'session_id': session_id,
                'upload_dir': upload_dir,
            },
            msg=f'Batch upload done: {len([r for r in results if not r.get("error")])}/{len(results)} succeeded' + (' (partial failure)' if has_errors else '')
        )

    @action(methods=['GET'], detail=False)
    def list_files(self, request):
        """
        列出Target host指定directory/File和childdirectory

        GET /api/taurus/ops/list_files/?host_id=<host_id>&path=<path>
        """
        serializer = OpsFileListSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        host_id = serializer.validated_data['host_id']
        path = serializer.validated_data.get('path', '/')

        host = _get_host_by_id(host_id)
        if host is None:
            return ErrorResponse(msg="Host does not exist or not approved")

        if host.online_status != 1:
            return ErrorResponse(msg="Host is offline, cannot list files")

        address = _get_executor_address(host)

        try:
            async def do_list():
                from taurus.sdk import TaurusClient
                from django.conf import settings
                sdk_cert_dir = getattr(settings, 'SDK_CERT_DIR', None)
                client_kwargs = {
                    'connect_timeout': int(getattr(settings, 'EXECUTOR_CONNECT_TIMEOUT', 5)),
                }
                if sdk_cert_dir:
                    client_kwargs['cert_file'] = os.path.join(sdk_cert_dir, 'client.crt')
                    client_kwargs['key_file'] = os.path.join(sdk_cert_dir, 'client.key')
                    client_kwargs['ca_file'] = os.path.join(sdk_cert_dir, 'ca.crt')
                async with TaurusClient(address, **client_kwargs) as client:
                    return await client.list_directory(path)

            entries = _run_async(do_list())
            return SuccessResponse(
                data={'path': path, 'entries': entries},
                msg="FilelistRetrieved successfully"
            )
        except Exception as e:
            logger.error(f"Exception getting file list: {e}")
            return ErrorResponse(msg=f"File list fetch failed: {str(e)}")

    @action(methods=['GET'], detail=False)
    def download_file(self, request):
        """
        从Target hostDownloadFile

        GET /api/taurus/ops/download_file/?host_id=<host_id>&path=<file_path>
        """
        serializer = OpsFileDownloadSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        host_id = serializer.validated_data['host_id']
        remote_path = serializer.validated_data['path']

        host = _get_host_by_id(host_id)
        if host is None:
            return ErrorResponse(msg="Host does not exist or not approved")

        if host.online_status != 1:
            return ErrorResponse(msg="Host is offline, cannot download file")

        address = _get_executor_address(host)
        tmp_path = None
        from taurus.models import OpsExecution
        import uuid
        execution_id = str(uuid.uuid4())
        file_size = 0

        try:
            suffix = os.path.basename(remote_path)
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp_path = tmp.name

            async def do_download():
                from taurus.sdk import TaurusClient
                from django.conf import settings
                sdk_cert_dir = getattr(settings, 'SDK_CERT_DIR', None)
                client_kwargs = {
                    'connect_timeout': int(getattr(settings, 'EXECUTOR_CONNECT_TIMEOUT', 5)),
                }
                if sdk_cert_dir:
                    client_kwargs['cert_file'] = os.path.join(sdk_cert_dir, 'client.crt')
                    client_kwargs['key_file'] = os.path.join(sdk_cert_dir, 'client.key')
                    client_kwargs['ca_file'] = os.path.join(sdk_cert_dir, 'ca.crt')
                async with TaurusClient(address, **client_kwargs) as client:
                    return await client.download_file(remote_path, tmp_path)

            result = _run_async(do_download())

            if not os.path.exists(tmp_path):
                OpsExecution.objects.create(
                    execution_id=execution_id,
                    execution_type='download',
                    host=host,
                    user=request.user,
                    file_path=remote_path,
                    status=3,
                    error_message="Download failed: file not generated",
                )
                return ErrorResponse(msg="Download failed: file not generated")

            file_size = os.path.getsize(tmp_path)
            file_name = os.path.basename(remote_path)

            # recordDownloadoperation
            OpsExecution.objects.create(
                execution_id=execution_id,
                execution_type='download',
                host=host,
                user=request.user,
                file_path=remote_path,
                file_size=file_size,
                status=2,  # Completed
                exit_code=0,
            )

            with open(tmp_path, 'rb') as f:
                from django.http import HttpResponse
                response = HttpResponse(f.read(), content_type='application/octet-stream')
                response['Content-Disposition'] = f'attachment; filename="{file_name}"'
                response['Content-Length'] = file_size
                return response
        except Exception as e:
            logger.error(f"Exception downloading file: {e}")
            OpsExecution.objects.create(
                execution_id=execution_id,
                execution_type='download',
                host=host,
                user=request.user,
                file_path=remote_path,
                file_size=file_size,
                status=3,  # Failed
                error_message=str(e),
            )
            return ErrorResponse(msg=f"File download failed: {str(e)}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)


class OpsExecutionViewSet(CustomModelViewSet):
    """运维Executionrecord管理"""
    queryset = OpsExecution.objects.all()
    serializer_class = OpsExecutionSerializer
    search_fields = ['execution_id', 'command', 'host__host_name', 'host__host_ip']
    filterset_fields = ['status', 'execution_type', 'host', 'batch_id']
    ordering_fields = ['create_datetime', 'started_at', 'finished_at']
    ordering = ['-create_datetime']

    def get_serializer_class(self):
        if self.action == 'list':
            return OpsExecutionListSerializer
        return OpsExecutionSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if not self.request.user or not self.request.user.is_authenticated:
            return queryset.none()
        if not self.request.user.is_superuser:
            queryset = queryset.filter(user=self.request.user)
        # 默认排除WorkflowTrigger/Executionrecord(batch_id 以 wfex- 开头), 
        # WorkflowExecutionrecord由 WorkflowRecordDetail 面independent展示
        queryset = queryset.exclude(batch_id__startswith='wfex-')
        # cleanupFrontend Tab "All" send/无效 status=all Parameters
        if hasattr(self.request, 'query_params'):
            status_val = self.request.query_params.get('status')
            if status_val and not status_val.isdigit():
                try:
                    self.request.query_params._mutable = True
                    del self.request.query_params['status']
                except (AttributeError, KeyError):
                    pass
            # supportPer host_ip Filter(associateField host__host_ip)
            host_ip_val = self.request.query_params.get('host_ip')
            if host_ip_val:
                queryset = queryset.filter(host__host_ip__icontains=host_ip_val)
                try:
                    self.request.query_params._mutable = True
                    del self.request.query_params['host_ip']
                except (AttributeError, KeyError):
                    pass
        return queryset.select_related('host', 'user')

    @action(detail=False, methods=['get'])
    def my_task_info(self, request):
        """Fetch我/任务Execution统计Message"""
        queryset = OpsExecution.objects.all()
        if not request.user.is_superuser:
            queryset = queryset.filter(user=request.user)
        # 排除WorkflowTrigger/Executionrecord
        queryset = queryset.exclude(batch_id__startswith='wfex-')

        total = queryset.count()
        pending = queryset.filter(status=0).count()
        running = queryset.filter(status=1).count()
        success = queryset.filter(status=2).count()
        failed = queryset.filter(status=3).count()
        interrupted = queryset.filter(status=4).count()

        return SuccessResponse(data={
            'total': total,
            'pending': pending,
            'running': running,
            'success': success,
            'failed': failed,
            'interrupted': interrupted,
        })


class OpsExecutionApprovalViewSet(CustomModelViewSet):
    """Execution任务Approval管理

    whenScriptExecution开启Approval时, 先createApprovalInstance, Approval passed后自动TriggerExecution.
    supportdynamic指定审核人(Or-sign any / Countersign all), support委派, Add-sign, 撤回.
    """
    # [M2.6 Gate] ops 审批全流程（list/create/approve/reject/delegate/add-sign）属于 EE 专属.
    # 采用 inline def dispatch override 规避 @method_decorator(name='dispatch') 在继承 dispatch 上
    # 静默失效的 QueryArgumentsMixin bug（同 M2.5 HostLog / LogCommand 模式）。
    def dispatch(self, request, *args, **kwargs):
        from taurus.editions.loader import require_feature as _ops_require_feature
        from taurus.editions.features import F_OPS_EXECUTION_APPROVAL as _F_OPS_APPROVAL

        @_ops_require_feature(_F_OPS_APPROVAL)
        def _inner(_req, *_a, **_kw):
            return super().dispatch(_req, *_a, **_kw)
        return _inner(request, *args, **kwargs)

    queryset = OpsExecutionApproval.objects.all()
    serializer_class = OpsExecutionApprovalSerializer
    create_serializer_class = OpsExecutionApprovalCreateSerializer
    search_fields = ['host__host_name', 'host__host_ip', 'submitter_name', 'approver_name']
    filterset_fields = {
        'status': ['exact', 'in'],
        'host': ['exact'],
        'submitter': ['exact'],
        'approver': ['exact'],
        'batch_id': ['exact'],
        'approval_mode': ['exact'],
    }
    ordering_fields = ['create_datetime', 'approve_time', 'finish_time']
    ordering = ['-create_datetime']
    permission_classes = [IsAuthenticated]
    # Approvalrecord可见性由 candidate_approvers / submitter / approver 三要素在 get_queryset 中
    # 精确控制(跨Dept也allow查看), 不再叠加 dvadmin default DataLevelPermissionsFilter(Per
    # Dept归属一刀切), No则非本DeptCommit/Approval单会被误Filter.
    extra_filter_class = []

    def get_serializer_class(self):
        if self.action == 'list':
            return OpsExecutionApprovalListSerializer
        if self.action == 'create':
            return OpsExecutionApprovalCreateSerializer
        return OpsExecutionApprovalSerializer

    def _user_visible_candidate_ids(self, queryset, user):
        """扫描 queryset, 提取出 candidate_approvers JSON 里Containscurrent user.id /Approval单 id list.

        candidate_approvers 结构示例: [{user_id: 2, username: 'admin', name: 'Administrator'}, ...]
        simultaneously兼容 approver_id 为Empty时用Role key match(例如 candidates 里存/YesRole名时兜底)
        """
        visible_ids = []
        uid = user.id
        user_roles = set()
        if hasattr(user, 'role'):
            user_roles = set(user.role.values_list('key', flat=True))
        user_role_ids = set()
        if hasattr(user, 'role'):
            user_role_ids = set(user.role.values_list('id', flat=True))
        for app in queryset.only('id', 'candidate_approvers', 'approver_id', 'status'):
            candidates = app.candidate_approvers or []
            cids = [c['user_id'] for c in candidates if isinstance(c, dict) and isinstance(c.get('user_id'), int)]
            if uid in cids:
                visible_ids.append(app.id)
                continue
            # 兜底:Candidates存/Yes {role_key: 'admin'} 或 {role_id: 1, role_name: 'Administrator'}, currentUser有该Role也能看见
            role_hit = False
            for c in candidates:
                if not isinstance(c, dict):
                    continue
                rk = c.get('role_key') or c.get('key')
                rid = c.get('role_id')
                if (rk and rk in user_roles) or (rid and rid in user_role_ids):
                    role_hit = True
                    break
            if role_hit:
                visible_ids.append(app.id)
                continue
            # 兼容单审传统模式:candidate_approvers 为Empty时, 看 approver_id
            if not cids and not role_hit:
                # 只要 approver_id Yes自己, 或Yes超级Administrator
                if (app.approver_id and app.approver_id == uid):
                    visible_ids.append(app.id)
        return visible_ids

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        view_type = self.request.query_params.get('view_type', '')
        if not user or not user.is_authenticated:
            return queryset.none()

        if view_type == 'pending_me' or view_type == 'pending':
            pending = queryset.filter(status='pending')
            mine_ids = self._user_visible_candidate_ids(pending, user)
            return queryset.filter(id__in=mine_ids).select_related('host', 'submitter', 'approver', 'ops_execution')
        if view_type == 'mine':
            return queryset.filter(submitter=user).select_related('host', 'submitter', 'approver', 'ops_execution')

        from django.db.models import Q
        # 1. 自己Commit/ / 自己Yes approver /
        base = queryset.filter(Q(submitter=user) | Q(approver=user))
        base_ids = set(base.values_list('id', flat=True))

        # 2. scale out:candidate_approvers 里Contains自己/(含Rolematch), 不限制 pending Status
        cand_ids = set(self._user_visible_candidate_ids(queryset, user))

        visible_ids = base_ids | cand_ids
        if visible_ids:
            queryset = queryset.filter(id__in=visible_ids)
        else:
            queryset = queryset.none()
        return queryset.select_related('host', 'submitter', 'approver', 'ops_execution')

    def list(self, request, *args, **kwargs):
        _auto_sync_stale_approvals()
        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        user = self.request.user
        serializer.save(
            submitter=user,
            submitter_name=getattr(user, 'username', '') or getattr(user, 'name', ''),
            status='pending',
        )

    # ---------------- 候选审核人判定 / Statuscompute ----------------
    @staticmethod
    def _is_user_candidate_approver(approval, user):
        """ApprovalPermission判定:
        1. candidate_approvers[*].user_id == user.id
        2. candidate_approvers[*].role_key/.role_id 命中 user 拥有/Role
        3. Candidates为Empty时:传统单审模式 approver_id == user.id
        """
        candidates = approval.candidate_approvers or []
        cids = [c['user_id'] for c in candidates if isinstance(c, dict) and isinstance(c.get('user_id'), int)]
        if user.id in cids:
            return True
        # Role level候选
        user_roles = set()
        user_role_ids = set()
        if hasattr(user, 'role'):
            try:
                user_roles = set(user.role.values_list('key', flat=True))
                user_role_ids = set(user.role.values_list('id', flat=True))
            except Exception:
                pass
        for c in candidates:
            if not isinstance(c, dict):
                continue
            rk = c.get('role_key') or c.get('key')
            rid = c.get('role_id')
            if (rk and rk in user_roles) or (rid and rid in user_role_ids):
                return True
        # 单审兜底(CandidatesEmpty时看 approver_id)
        if not cids:
            if approval.approver_id and approval.approver_id == user.id:
                return True
        return False

    @staticmethod
    def _is_approved_by_mode(approval):
        records = approval.approval_records or []
        approved_set = set(r['user_id'] for r in records if r.get('action') == 'approve' and r.get('user_id'))
        candidates = approval.candidate_approvers or []
        candidate_ids = [c['user_id'] for c in candidates if isinstance(c, dict)]

        if not candidate_ids:
            # 未指定Candidates:任一人via即可(传统单审模式)
            return len(approved_set) >= 1

        if approval.approval_mode == 'all':
            return set(candidate_ids) <= approved_set and len(candidate_ids) > 0
        # any / first
        return len(approved_set & set(candidate_ids)) >= 1

    # ---------------- 审核动作 ----------------
    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        """Approval passed(多Candidates:Or-sign任一人via, CountersignmustAllvia)"""
        from django.utils import timezone
        serializer = OpsExecutionApprovalActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        approval = self.get_object()
        if approval.status != 'pending':
            return ErrorResponse(msg=f"Current status ({approval.get_status_display()})) does not allow approval")

        if not self._is_user_candidate_approver(approval, request.user):
            return ErrorResponse(msg="You are not in the approver candidates list, cannot operate")

        records = list(approval.approval_records or [])
        for r in records:
            if r.get('user_id') == request.user.id and r.get('action') in ('approve', 'reject'):
                return ErrorResponse(msg="You have already processed this approval")

        records.append({
            'user_id': request.user.id,
            'username': request.user.username,
            'name': getattr(request.user, 'name', request.user.username),
            'action': 'approve',
            'reason': serializer.validated_data.get('reason', ''),
            'operate_time': timezone.now().isoformat(),
        })
        approval.approval_records = records
        update_fields = ['approval_records', 'update_datetime']

        if self._is_approved_by_mode(approval):
            approval.status = 'approved'
            approval.approver = request.user
            approval.approver_name = getattr(request.user, 'username', '') or getattr(request.user, 'name', '')
            approval.approve_reason = serializer.validated_data.get('reason', '')
            approval.approve_time = timezone.now()
            approval.finish_time = timezone.now()
            update_fields += ['status', 'approver', 'approver_name', 'approve_reason', 'approve_time', 'finish_time']

            try:
                _trigger_approval_execution(approval, request.user)
            except Exception as e:
                logger.error(f"[ExecApproval] Post-approval execution trigger failed approval_id={approval.id}: {e}", exc_info=True)
                approval.status = 'failed'
                update_fields.append('status')
                approval.save(update_fields=update_fields)
                return ErrorResponse(msg=f"Approval passed but trigger execution failed: {str(e)}")

        approval.save(update_fields=update_fields)
        return DetailResponse(
            data=OpsExecutionApprovalSerializer(approval).data,
            msg='Approval approved' if approval.status == 'approved' else 'Approval recorded (countersign requires all approvers)'
        )

    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, pk=None):
        """驳回Approval(任一Candidates驳回即整体驳回)"""
        from django.utils import timezone
        serializer = OpsExecutionApprovalActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        approval = self.get_object()
        if approval.status != 'pending':
            return ErrorResponse(msg=f"Current status ({approval.get_status_display()})) does not allow rejection")

        if not self._is_user_candidate_approver(approval, request.user):
            return ErrorResponse(msg="You are not in the approver candidates list, cannot operate")

        reason = serializer.validated_data.get('reason', '')
        if not reason:
            return ErrorResponse(msg="Rejection reason cannot be empty")

        records = list(approval.approval_records or [])
        records.append({
            'user_id': request.user.id,
            'username': request.user.username,
            'name': getattr(request.user, 'name', request.user.username),
            'action': 'reject',
            'reason': reason,
            'operate_time': timezone.now().isoformat(),
        })
        approval.approval_records = records

        approval.status = 'rejected'
        approval.approver = request.user
        approval.approver_name = getattr(request.user, 'username', '') or getattr(request.user, 'name', '')
        approval.approve_reason = reason
        approval.approve_time = timezone.now()
        approval.finish_time = timezone.now()
        approval.save(update_fields=[
            'approval_records',
            'status', 'approver', 'approver_name', 'approve_reason', 'approve_time',
            'finish_time', 'update_datetime'
        ])

        if approval.ops_execution_id:
            OpsExecution.objects.filter(pk=approval.ops_execution_id).update(
                status=6,
                update_datetime=timezone.now(),
            )
        return DetailResponse(data=OpsExecutionApprovalSerializer(approval).data, msg="Rejected")

    @action(detail=True, methods=['post'], url_path='delegate')
    def delegate(self, request, pk=None):
        """审核人委派他人process(加入Candidates)"""
        from django.utils import timezone
        from django.contrib.auth import get_user_model
        User = get_user_model()

        approval = self.get_object()
        if approval.status != 'pending':
            return ErrorResponse(msg=f"Current status ({approval.get_status_display()})) does not allow delegation")
        if not self._is_user_candidate_approver(approval, request.user):
            return ErrorResponse(msg="You are not in the approver candidates list, cannot delegate")

        to_user_id = request.data.get('to_user_id')
        reason = request.data.get('reason', '')
        if not to_user_id:
            return ErrorResponse(msg="Please specify delegate user")
        try:
            to_user = User.objects.get(id=to_user_id)
        except User.DoesNotExist:
            return ErrorResponse(msg="Target user does not exist")

        candidates = list(approval.candidate_approvers or [])
        cids = [c['user_id'] for c in candidates if isinstance(c, dict)]
        if to_user.id not in cids:
            candidates.append({
                'user_id': to_user.id,
                'username': to_user.username,
                'name': getattr(to_user, 'name', to_user.username),
            })
        records = list(approval.approval_records or [])
        records.append({
            'user_id': request.user.id,
            'username': request.user.username,
            'action': 'delegate',
            'to_user_id': to_user.id,
            'to_username': to_user.username,
            'reason': reason,
            'operate_time': timezone.now().isoformat(),
        })
        approval.candidate_approvers = candidates
        approval.approval_records = records
        approval.save(update_fields=['candidate_approvers', 'approval_records', 'update_datetime'])
        return DetailResponse(data=OpsExecutionApprovalSerializer(approval).data, msg="Delegate succeeded")

    @action(detail=True, methods=['post'], url_path='add-sign')
    def add_sign(self, request, pk=None):
        """Add-sign:current审核人append其他审核人(强制扩充Candidates)"""
        from django.utils import timezone
        from django.contrib.auth import get_user_model
        User = get_user_model()

        approval = self.get_object()
        if approval.status != 'pending':
            return ErrorResponse(msg=f"Current status ({approval.get_status_display()})) does not allow adding signers")
        if not self._is_user_candidate_approver(approval, request.user):
            return ErrorResponse(msg="You are not in the approver candidates list, cannot add signer")

        user_ids = request.data.get('user_ids') or []
        reason = request.data.get('reason', '')
        if not user_ids:
            return ErrorResponse(msg="Please specify user to add as signer")

        users = list(User.objects.filter(id__in=list(user_ids)))
        candidates = list(approval.candidate_approvers or [])
        cids = set(c['user_id'] for c in candidates if isinstance(c, dict))
        for u in users:
            if u.id not in cids:
                candidates.append({
                    'user_id': u.id,
                    'username': u.username,
                    'name': getattr(u, 'name', u.username),
                })
        records = list(approval.approval_records or [])
        records.append({
            'user_id': request.user.id,
            'username': request.user.username,
            'action': 'add_sign',
            'user_ids': [u.id for u in users],
            'reason': reason,
            'operate_time': timezone.now().isoformat(),
        })
        approval.candidate_approvers = candidates
        approval.approval_records = records
        approval.save(update_fields=['candidate_approvers', 'approval_records', 'update_datetime'])
        return DetailResponse(data=OpsExecutionApprovalSerializer(approval).data, msg="Added reviewer successfully")

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        """Submitter撤回Approval"""
        from django.utils import timezone
        approval = self.get_object()
        if approval.status != 'pending':
            return ErrorResponse(msg=f"Current status ({approval.get_status_display()})) does not allow withdrawal")
        if approval.submitter_id != request.user.id and not request.user.is_superuser:
            return ErrorResponse(msg="Only submitter can withdraw")

        approval.status = 'cancelled'
        approval.finish_time = timezone.now()
        approval.save(update_fields=['status', 'finish_time', 'update_datetime'])
        return DetailResponse(data=OpsExecutionApprovalSerializer(approval).data, msg="Withdrawn")

    @action(detail=False, methods=['get'], url_path='stats/count')
    def stats_count(self, request):
        """统计: based on self.get_queryset() /可见范围分桶计数, 与list Tab return总数严格一致."""
        user = request.user
        # 先用 get_queryset 拿到currentUser可见/AllApproval单
        visible_qs = self.get_queryset()
        pending_qs = visible_qs.filter(status='pending')
        # pending_me = 待我Approval(我YesCandidates/approver / pending Status)
        pending_me = 0
        pending_by_source = {}
        for app in pending_qs:
            if self._is_user_candidate_approver(app, user):
                pending_me += 1
                src = app.source_type or 'script'
                pending_by_source[src] = pending_by_source.get(src, 0) + 1
        my_pending = visible_qs.filter(submitter=user, status='pending').count()
        total_count = visible_qs.count()
        approved_count = visible_qs.filter(status='approved').count()
        rejected_count = visible_qs.filter(status='rejected').count()
        return DetailResponse(data={
            'pending_me': pending_me,
            'pending_by_source': pending_by_source,
            'my_pending': my_pending,
            'total_count': total_count,
            'approved_count': approved_count,
            'rejected_count': rejected_count,
        }, msg="Retrieved successfully")



def _trigger_approval_execution(approval, user):
    """Approval passed后, according toSnapshotParameterscreate OpsExecution 并Trigger异步Execution(support command/script)"""
    import uuid as _uuid

    # === 配额校验：社区版最大并发执行数 ===
    from taurus.editions.loader import check_quota as _check_quota
    from taurus.models import OpsExecution as _OpsExec
    _running = _OpsExec.objects.filter(status__in=[0, 1, 5]).count()
    _check_quota('max_concurrent_executions', _running, '并发执行任务')

    host = approval.host
    if host is None:
        raise ValueError("Approval not associated with target host")

    if host.online_status != 1:
        raise ValueError(f"Host is offline: {host.host_ip}")

    environment = dict(approval.environment or {})
    if approval.privileged:
        environment['PRIVILEGED_EXECUTION'] = 'true'
        if approval.su_user:
            environment['SU_USER'] = approval.su_user

    # 还原Or-sign/Countersign ID list(结合 candidate_approvers 与 approval_mode)
    candidate_user_ids = [
        int(u['user_id']) for u in (approval.candidate_approvers or [])
        if isinstance(u, dict) and str(u.get('user_id', '')).isdigit()
    ]
    approver_ids_snapshot = candidate_user_ids if approval.approval_mode == 'any' else []
    countersign_ids_snapshot = candidate_user_ids if approval.approval_mode == 'all' else []

    execution_type = (approval.execution_type or 'script').strip()
    if execution_type not in ('command', 'script'):
        execution_type = 'script'

    execution_id = str(_uuid.uuid4())
    create_kwargs = dict(
        execution_id=execution_id,
        batch_id=approval.batch_id,
        execution_type=execution_type,
        host=host,
        user=user,
        working_directory=approval.working_directory,
        timeout_seconds=approval.timeout_seconds,
        environment=environment,
        use_shell=bool(getattr(approval, 'use_shell', True)),
        merge_streams=approval.merge_streams,
        load_profile=approval.load_profile,
        privileged=bool(approval.privileged),
        su_user=approval.su_user,
        exec_mode=approval.exec_mode or 'parallel',
        concurrent=approval.concurrency or 10,
        fail_strategy=approval.fail_strategy or 'continue',
        pilot_count=getattr(approval, 'pilot_count', 2) or 2,
        pilot_success_rate=getattr(approval, 'pilot_success_rate', 100) or 100,
        need_audit=True,
        auto_notify=bool(getattr(approval, 'auto_notify', False)),
        approval_mode=approval.approval_mode or ('all' if countersign_ids_snapshot else ('any' if approver_ids_snapshot else None)),
        approver_ids=approver_ids_snapshot,
        countersign_ids=countersign_ids_snapshot,
        submit_desc=approval.submit_desc or None,
        status=0,
    )

    if execution_type == 'command':
        create_kwargs['command'] = approval.command
        create_kwargs['script_type'] = None
        create_kwargs['script_content'] = None
        create_kwargs['args'] = approval.args or []
    else:
        create_kwargs['command'] = None
        create_kwargs['script_type'] = approval.script_type
        create_kwargs['script_content'] = approval.script_content
        create_kwargs['args'] = approval.args or []

    execution = OpsExecution.objects.create(**create_kwargs)

    approval.ops_execution = execution
    approval.status = 'executing'
    approval.save(update_fields=['ops_execution', 'status', 'update_datetime'])

    try:
        from taurus.websocket_async import get_event_loop, submit_execution, _execute_ops_async
        loop = get_event_loop()
        if loop is not None and not loop.is_closed():
            submitted = submit_execution(execution_id, _execute_ops_async(execution_id))
            if submitted:
                logger.info(f"[ExecApproval] Immediate trigger execution: {execution_id} (type={execution_type}, approval_id={approval.id})")
            else:
                logger.info(f"[ExecApproval] Immediate submit failed, will poll as fallback: {execution_id}")
        else:
            logger.debug(f"[ExecApproval] WebSocket event loop unavailable, polling fallback: {execution_id}")
    except Exception as e:
        logger.warning(f"[ExecApproval] Immediate trigger exception, polling fallback: {e}")


def _auto_sync_stale_approvals():
    """兜底:自动将Status不一致/Approvalrecord从 executing 同步为 done/failed"""
    try:
        from taurus.models import OpsExecutionApproval
        stale = OpsExecutionApproval.objects.filter(
            status='executing',
            ops_execution__isnull=False,
        ).select_related('ops_execution')
        for approval in stale:
            exec_obj = approval.ops_execution
            if exec_obj and exec_obj.status in (2, 3):
                new_status = 'done' if exec_obj.status == 2 else 'failed'
                OpsExecutionApproval.objects.filter(pk=approval.pk).update(
                    status=new_status,
                    finish_time=timezone.now(),
                    update_datetime=timezone.now(),
                )
                logger.info(f"[ApprovalFallbackSync] approval_id={approval.id} -> {new_status}")
    except Exception as e:
        logger.error(f"[ApprovalFallbackSyncException] {e}", exc_info=True)


# ========================== Scriptlibrary管理View ==========================

class ScriptCategoryViewSet(CustomModelViewSet):
    """ScriptCategory管理"""
    queryset = ScriptCategory.objects.all()
    serializer_class = ScriptCategorySerializer
    create_serializer_class = ScriptCategoryCreateSerializer
    update_serializer_class = ScriptCategoryUpdateSerializer
    search_fields = ['name']
    filterset_fields = ['parent', 'category_type', 'is_system']
    ordering_fields = ['sort', 'id', 'create_datetime']
    ordering = ['sort', 'id']

    def get_queryset(self):
        """
        超级Administratorcan看到所有Category
        普通User:
          - 系统Category所有人可见
          - 「PublicScript」下/Category所有人可见
          - 「My scripts」下/CategoryPercreate者Isolation, 每 User只能看到自己create/
        """
        queryset = super().get_queryset()
        user = self.request.user
        if not user or not user.is_authenticated:
            return queryset.filter(category_type='system')

        # 超级Administratorcan看到所有Category
        if user.is_superuser:
            return queryset

        # Fetch「PublicScript」/系统CategoryID
        public_cat = ScriptCategory.objects.filter(
            name='Public scripts', category_type='system'
        ).first()
        public_id = public_cat.id if public_cat else None

        # Fetch「PublicScript」下所有childCategoryID
        def _get_all_child_ids(parent_id):
            if not parent_id:
                return []
            ids = []
            children = ScriptCategory.objects.filter(parent=parent_id)
            for child in children:
                ids.append(child.id)
                ids.extend(_get_all_child_ids(child.id))
            return ids

        public_child_ids = _get_all_child_ids(public_id)

        # 系统Category + PublicScript下/Category + 我create/Category
        from django.db import models as django_models
        queryset = queryset.filter(
            django_models.Q(category_type='system') |
            django_models.Q(id__in=public_child_ids) |
            django_models.Q(creator=user)
        )

        return queryset

    import_field_dict = {
        'name': 'Category name',
        'parent': 'Parent category',
        'category_type': {'title': 'Category type', 'choices': {'data': {'Custom': 'custom', 'System built-in': 'system'}}},
        'sort': 'Sort order',
        'is_virtual': 'Is virtual category',
        'remark': 'Remark',
    }
    import_serializer_class = ScriptCategoryCreateSerializer
    update_template_serializer_class = ScriptCategorySerializer
    export_field_label = {
        'name': 'Category name',
        'category_type_display': 'Category type',
        'sort': 'Sort order',
        'is_system': 'Is system built-in',
        'is_virtual': 'Is virtual category',
        'script_count': 'Script count',
        'remark': 'Remark',
        'create_datetime': 'Created time',
    }
    export_serializer_class = ScriptCategorySerializer

    def get_serializer_class(self):
        if self.action == 'create':
            return ScriptCategoryCreateSerializer
        if self.action == 'update' or self.action == 'partial_update':
            return ScriptCategoryUpdateSerializer
        return ScriptCategorySerializer

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            tree = self.build_tree(serializer.data)
            return self.get_paginated_response(tree)
        serializer = self.get_serializer(queryset, many=True)
        tree = self.build_tree(serializer.data)
        return SuccessResponse(data=tree, msg="Retrieved successfully")

    def build_tree(self, data):
        data_list = list(data)
        item_map = {item['id']: item for item in data_list}
        result = []
        for item in data_list:
            parent = item.get('parent')
            if parent is None:
                result.append(item)
                continue
            parent_id = parent
            if hasattr(parent, 'id'):
                parent_id = parent.id
            try:
                parent_id = int(parent_id)
            except (TypeError, ValueError):
                result.append(item)
                continue
            parent_item = item_map.get(parent_id)
            if parent_item:
                if 'children' not in parent_item:
                    parent_item['children'] = []
                parent_item['children'].append(item)
            else:
                result.append(item)
        return result

    @action(detail=False, methods=['get'], url_path='tree')
    def get_tree(self, request, *args, **kwargs):
        """Fetch完整tree形结构(不Pagination)"""
        from taurus.models import Script
        from django.db import models as django_models
        queryset = self.filter_queryset(self.get_queryset())
        queryset = queryset.order_by('sort', 'id')
        serializer = ScriptCategorySerializer(queryset, many=True, context={'request': request})
        category_list = serializer.data
        item_map = {}
        for item in category_list:
            item['parent'] = item.get('parent')
            item['category_type_display'] = dict(ScriptCategory.CATEGORY_TYPE_CHOICES).get(item.get('category_type'), item.get('category_type'))
            item['children'] = []
            item_map[item['id']] = item

        def _get_all_child_ids(cat_id):
            ids = [cat_id]
            for item in category_list:
                if item['parent'] == cat_id:
                    ids.extend(_get_all_child_ids(item['id']))
            return ids

        def _is_under_mine(cat_id):
            """Check if category is under 'My scripts' tree"""
            current_id = cat_id
            visited = set()
            while current_id and current_id not in visited:
                visited.add(current_id)
                cat = item_map.get(current_id)
                if not cat:
                    break
                if cat.get('category_type') == 'system' and cat.get('name') == 'My scripts':
                    return True
                current_id = cat.get('parent')
            return False

        def _is_under_public(cat_id):
            """Check if category is under 'Public scripts' tree"""
            current_id = cat_id
            visited = set()
            while current_id and current_id not in visited:
                visited.add(current_id)
                cat = item_map.get(current_id)
                if not cat:
                    break
                if cat.get('category_type') == 'system' and cat.get('name') == 'Public scripts':
                    return True
                current_id = cat.get('parent')
            return False

        user = request.user
        # PermissionFilter后/Scriptqueryset, 与ScriptViewSet.get_queryset保持一致(不含"被分享给我"/Script)
        from taurus.utils.share_permission import ShareVisibleQS
        visible_scripts = ShareVisibleQS.filter_visible_scripts(
            Script.objects.all(), user, request, include_shared=False
        )

        # Found"My scripts"和"PublicScript"/系统CategoryID及其所有childCategoryID
        mine_cat = next((c for c in category_list if c.get('category_type') == 'system' and c.get('name') == 'My scripts'), None)
        public_cat = next((c for c in category_list if c.get('category_type') == 'system' and c.get('name') == 'Public scripts'), None)
        mine_cat_ids = _get_all_child_ids(mine_cat['id']) if mine_cat else []
        public_cat_ids = _get_all_child_ids(public_cat['id']) if public_cat else []

        for item in category_list:
            cat_type = item.get('category_type')
            cat_name = item.get('name')
            if cat_type == 'system':
                if cat_name == 'All scripts':
                    item['script_count'] = visible_scripts.count()
                elif cat_name == 'My scripts':
                    # 普通User:限制在"My scripts"Categorytree内, 且只统计currentUsercreate/Script
                    # 超级Administrator:只限制在"My scripts"Categorytree内, 看到该Categorytree内/AllScript
                    qs = visible_scripts.filter(category_id__in=mine_cat_ids)
                    if not user.is_superuser:
                        qs = qs.filter(creator=user)
                    item['script_count'] = qs.count()
                elif cat_name == 'Public scripts':
                    # 限制在"PublicScript"Categorytree内, 且只统计PublicScript(所有User一致)
                    item['script_count'] = visible_scripts.filter(
                        category_id__in=public_cat_ids, auth_type='public'
                    ).count()
                elif cat_name == 'Pending approval':
                    item['script_count'] = visible_scripts.filter(status=2).count()
                elif cat_name == 'Archived scripts':
                    item['script_count'] = visible_scripts.filter(status=3).count()
                else:
                    item['script_count'] = 0
            else:
                child_ids = _get_all_child_ids(item['id'])
                qs = visible_scripts.filter(category_id__in=child_ids)
                # "My scripts"下/childCategory:普通User只统计自己create/, 超级Administrator看到该Categorytree内All
                if _is_under_mine(item['id']):
                    if not user.is_superuser:
                        qs = qs.filter(creator=user)
                # "PublicScript"下/childCategory:只统计PublicScript(所有User一致)
                elif _is_under_public(item['id']):
                    qs = qs.filter(auth_type='public')
                item['script_count'] = qs.count()

        for item in category_list:
            parent_id = item.get('parent')
            if parent_id is None:
                continue
            if parent_id in item_map:
                item_map[parent_id]['children'].append(item)
        result = [item for item in category_list if item.get('parent') is None]
        return SuccessResponse(data=result, msg="Retrieved successfully")

    def perform_create(self, serializer):
        validated_data = serializer.validated_data
        if validated_data.get('category_type') != 'system' and not validated_data.get('parent'):
            mine_category = ScriptCategory.objects.filter(
                name='My scripts', category_type='system'
            ).first()
            if mine_category:
                serializer.validated_data['parent'] = mine_category.id
        instance = serializer.save(creator=self.request.user, modifier=self.request.user.username)
        self.save_category_audit('create', instance)

    def perform_update(self, serializer):
        instance = self.get_object()
        if instance.is_system:
            raise PermissionDenied("System category cannot be modified")
        self.save_category_audit('edit', instance)
        serializer.save(modifier=self.request.user.username)

    def perform_destroy(self, instance):
        if instance.is_system:
            raise PermissionDenied("System category cannot be deleted")
        child_count = ScriptCategory.objects.filter(parent=instance.id).count()
        if child_count > 0:
            raise PermissionDenied("Category has child categories, cannot delete")
        script_count = Script.objects.filter(category=instance.id).count()
        if script_count > 0:
            raise PermissionDenied("Category has scripts, cannot delete")
        self.save_category_audit('delete', instance)
        instance.delete()

    def save_category_audit(self, oper_type, instance):
        """Categoryoperationaudit(暂存, 待Categoryaudit表完善后Enable)"""
        pass

    def get_client_ip(self):
        x_forwarded_for = self.request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return self.request.META.get('REMOTE_ADDR', '')


class ScriptViewSet(CustomModelViewSet):
    """Scriptlibrary管理"""
    queryset = Script.objects.all()
    serializer_class = ScriptSerializer
    create_serializer_class = ScriptCreateSerializer
    update_serializer_class = ScriptUpdateSerializer
    search_fields = ['name', 'tags', 'desc']
    filterset_fields = ['script_type', 'auth_type', 'status']
    ordering_fields = ['create_datetime', 'update_datetime', 'exec_count']
    ordering = ['-create_datetime']

    import_field_dict = {
        'name': 'Script name',
        'script_type': {'title': 'Script type', 'choices': {'data': {'Shell': 'sh', 'Python': 'python', 'PowerShell': 'powershell', 'Bat': 'bat', 'Bash': 'bash'}}},
        'category': 'Category ID',
        'auth_type': {'title': 'Authorization type', 'choices': {'data': {'Private': 'private', 'Public': 'public'}}},
        'tags': 'Tags (comma separated)',
        'desc': 'Description',
        'content': 'Script content',
        'timeout': 'Timeout (seconds)',
        'concurrent': 'Concurrency limit',
        'fail_strategy': {'title': 'Fail strategy', 'choices': {'data': {'Continue': 'continue', 'Abort': 'abort'}}},
        'open_risk_check': 'Enable risk check',
        'need_audit': 'Require approval',
        'log_retention': 'Log retention days',
    }
    import_serializer_class = ScriptCreateSerializer
    update_template_serializer_class = ScriptSerializer
    export_field_label = {
        'name': 'Script name',
        'type_display': 'Script type',
        'category_name': 'Category',
        'auth_type_display': 'Auth type',
        'tags': 'Tags',
        'desc': 'Description',
        'current_version': 'Current version',
        'timeout': 'Timeout (seconds)',
        'concurrent': 'Concurrency limit',
        'fail_strategy_display': 'Fail strategy',
        'open_risk_check': 'Enable risk check',
        'need_audit': 'Require approval',
        'status_display': 'Status',
        'exec_count': 'Exec count',
        'last_exec_time': 'Last exec time',
        'creator_name': 'Creator',
        'create_datetime': 'Created time',
    }
    export_serializer_class = ScriptListSerializer

    def get_queryset(self):
        """
        可见性规则:
          - list 动作(Tab 切换场景):
              * All/我//Public Tab:我create/ + Public/(被分享给My scripts只在"分享给我"Tab出现)
              * shared_to_me Tab:直接分享给我 + link分享激活/Script(不含我create/, Public/)
              * shared_by_me Tab:由我分享给他人/(我create/且存在分享record或分享link)
          - retrieve / update / destroy / effective-perms 等 detail 动作:
              * 完整可见性(我create/ + Public/ + 被分享给我/), 保证 get_object 能拿到object
        mine Parameters:仅显示我/;category 递归Filter等keep
        """
        queryset = super().get_queryset()
        user = self.request.user
        view = self.request.query_params.get('view')
        action = getattr(self, 'action', None)

        if action == 'list':
            # ---------- listView:Per Tab 差异化Filter ----------
            if view == 'shared_to_me':
                q_shared = ShareVisibleQS._shared_to_user_scripts_q(user, self.request)
                queryset = queryset.filter(q_shared).exclude(creator=user).exclude(auth_type='public')
            else:
                if not getattr(user, 'is_superuser', False):
                    queryset = ShareVisibleQS.filter_visible_scripts(queryset, user, self.request, include_shared=False)
            if view == 'shared_by_me':
                from taurus.models import ScriptSharePermission, ShareLink, Script
                from django.db.models import Q
                now = timezone.now()
                now_cond = Q(expire_time__isnull=True) | Q(expire_time__gte=now)
                perm_script_ids = set(
                    ScriptSharePermission.objects.filter(script__creator=user)
                    .exclude(subject_type='user', subject_id=user.id)
                    .filter(now_cond)
                    .values_list('script_id', flat=True)
                )
                link_resource_ids = ShareLink.objects.filter(
                    resource_type='script',
                    is_active=True,
                ).filter(now_cond).values_list('resource_id', flat=True)
                link_rids_int = set()
                for rid in link_resource_ids:
                    try:
                        link_rids_int.add(int(rid))
                    except (ValueError, TypeError):
                        pass
                if link_rids_int:
                    link_script_ids = set(
                        Script.objects.filter(
                            creator=user,
                            id__in=link_rids_int,
                        ).values_list('id', flat=True)
                    )
                else:
                    link_script_ids = set()
                all_ids = perm_script_ids | link_script_ids
                if all_ids:
                    queryset = queryset.filter(id__in=all_ids, creator=user)
                else:
                    queryset = queryset.none()
        else:
            # ---------- DetailView:完整可见性(含分享给我/) ----------
            if not getattr(user, 'is_superuser', False):
                queryset = ShareVisibleQS.filter_visible_scripts(queryset, user, self.request, include_shared=True)

        # mine Parameters:只Query我create/Script
        if self.request.query_params.get('mine') == 'true':
            queryset = queryset.filter(creator=self.request.user)
        # category Parameters:递归Query该Category及其所有childCategory下/Script
        category_id = self.request.query_params.get('category')
        if category_id:
            try:
                from taurus.models import ScriptCategory
                cat = ScriptCategory.objects.get(id=category_id)
                # "AllScript"Yes虚拟root节., 不分tree结构, return所有可见Script
                if cat.name == 'All scripts' and cat.is_virtual:
                    pass
                else:
                    child_ids = self._get_all_child_category_ids(cat.id)
                    queryset = queryset.filter(category_id__in=child_ids)
                    if not self.request.user.is_superuser and self._is_category_under_mine(cat):
                        queryset = queryset.filter(creator=self.request.user)
                    if self._is_category_under_public(cat):
                        queryset = queryset.filter(auth_type='public')
            except (ScriptCategory.DoesNotExist, ValueError):
                pass
        return queryset

    def _get_all_child_category_ids(self, parent_id):
        """递归Fetch所有childCategoryID"""
        from taurus.models import ScriptCategory
        ids = [parent_id]
        children = ScriptCategory.objects.filter(parent=parent_id)
        for child in children:
            ids.extend(self._get_all_child_category_ids(child.id))
        return ids

    def _is_category_under_mine(self, category):
        """Check if category is under 'My scripts' tree"""
        current = category
        visited = set()
        while current and current.id not in visited:
            visited.add(current.id)
            if current.category_type == 'system' and current.name == 'My scripts':
                return True
            current = current.parent
        return False

    def _is_category_under_public(self, category):
        """Check if category is under 'Public scripts' tree"""
        current = category
        visited = set()
        while current and current.id not in visited:
            visited.add(current.id)
            if current.category_type == 'system' and current.name == 'Public scripts':
                return True
            current = current.parent
        return False

    def get_serializer_class(self):
        if self.action == 'list':
            return ScriptListSerializer
        if self.action == 'create':
            return ScriptCreateSerializer
        if self.action == 'update' or self.action == 'partial_update':
            return ScriptUpdateSerializer
        return ScriptSerializer

    @action(detail=False, methods=['get'])
    def my_script_info(self, request):
        """FetchMy scripts统计Message"""
        queryset = Script.objects.all()
        if not request.user.is_superuser:
            queryset = queryset.filter(
                models.Q(creator=request.user) | models.Q(auth_type='public')
            )

        total = queryset.count()
        shell = queryset.filter(script_type='Shell').count()
        python = queryset.filter(script_type='Python3').count()
        powershell = queryset.filter(script_type='PowerShell').count()
        bat = queryset.filter(script_type='Bat').count()
        sql = queryset.filter(script_type='SQL').count()

        return SuccessResponse(data={
            'total': total,
            'shell': shell,
            'python': python,
            'powershell': powershell,
            'bat': bat,
            'sql': sql,
        })

    def _get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR', 'unknown')
        return ip

    def _record_audit(self, script, oper_type, detail='', request=None):
        ScriptAudit.objects.create(
            script=script,
            script_version=script.current_version,
            operator=request.user if request and hasattr(request, 'user') else None,
            operator_name=request.user.username if request and hasattr(request, 'user') and request.user.is_authenticated else '',
            oper_type=oper_type,
            detail=detail,
            client_ip=self._get_client_ip(request) if request else '',
        )

    def _is_public_category(self, category):
        """判断CategoryYesNo属于PublicScriptdirectory"""
        if not category:
            return False
        current = category
        visited = set()
        while current and current.id not in visited:
            visited.add(current.id)
            if current.name == 'Public scripts':
                return True
            current = current.parent
        return False

    def _create_approval(self, script, request):
        """createApprovalrecord(use规则驱动审核Engine)"""
        from taurus.views import ApprovalFlowEngine
        user = request.user if request and hasattr(request, 'user') else None
        if user and user.is_authenticated:
            instance = ApprovalFlowEngine.start_approval(
                script=script,
                submitter=user,
                submit_desc='Submit script for approval',
            )
            return instance
        return None

    def _detect_and_set_risk_level(self, script):
        """自动detectScript content/风险等级并Save"""
        import logging
        try:
            from taurus.script_checker import ScriptCheckService
            result = ScriptCheckService.check(script.content or '', script.script_type or '')
            result.calculate_risk_level()
            script.risk_level = result.risk_level
            script.save(update_fields=['risk_level'])
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning(f'Script[{script.id}] risk detection failed: {e}', exc_info=True)

    def perform_create(self, serializer):
        script = serializer.save(creator=self.request.user)
        # Frontend未传 risk_level 时自动detect风险等级
        if not serializer.validated_data.get('risk_level'):
            self._detect_and_set_risk_level(script)
        # create初始Version
        ScriptVersion.objects.create(
            script=script,
            version=script.current_version,
            content=script.content,
            desc='Initial version',
            is_current=True,
        )
        # 规则驱动审核:Attemptmatch规则, match到则start审核
        self._create_approval(script, self.request)
        self._record_audit(script, 'create', 'Create script', self.request)

    def perform_update(self, serializer):
        old_script = self.get_object()
        # Pending approvalStatusforbidmodify内容/Category等Field(viaSave基础Field如Description/Tags等/需求cankeep)
        if old_script.status == 2:
            mutable_fields = {'tags', 'desc', 'timeout', 'concurrent', 'fail_strategy',
                              'log_retention', 'script_params', 'script_envs', 'supported_systems'}
            changed_fields = set(serializer.validated_data.keys())
            content_changed = 'content' in changed_fields or (
                'content' not in serializer.validated_data and False
            )
            category_changed = 'category' in changed_fields
            name_changed = 'name' in changed_fields
            script_type_changed = 'script_type' in changed_fields
            need_audit_changed = 'need_audit' in changed_fields
            if content_changed or category_changed or name_changed or script_type_changed or need_audit_changed:
                from rest_framework.exceptions import ValidationError
                raise ValidationError("Script is pending approval, cannot modify content/name/type/category/approval config")
        if old_script.is_official and not self.request.user.is_staff:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Official scripts cannot be modified. Use "Save As" to create a private copy')
        old_content = old_script.content
        old_category = old_script.category
        was_public = self._is_public_category(old_category)

        script = serializer.save()

        # Frontend未传 risk_level 且内容有变更时, 自动re-detect风险等级
        if old_content != script.content and not serializer.validated_data.get('risk_level'):
            self._detect_and_set_risk_level(script)
        # 内容有变更时自动Generate新Version
        if old_content != script.content:
            # === 配额校验：社区版单脚本历史版本上限（FIFO 自动淘汰最旧非当前版本） ===
            from taurus.editions.loader import evict_to_quota as _evict
            _evict(script.versions.all(), 'max_script_versions_per_script',
                   filter_exclude={'is_current': True})
            # Cancel旧Version/ is_current
            script.versions.filter(is_current=True).update(is_current=False)
            # Generate新Version号
            versions = list(script.versions.values_list('version', flat=True))
            # 简单递增: V1.0 -> V2.0 -> V3.0 ...
            max_version = 1
            for v in versions:
                try:
                    num = int(v.replace('V', '').split('.')[0])
                    if num > max_version:
                        max_version = num
                except:
                    pass
            new_version = f'V{max_version + 1}.0'
            script.current_version = new_version
            script.save()
            ScriptVersion.objects.create(
                script=script,
                version=new_version,
                content=script.content,
                desc='Auto-generate new version',
                is_current=True,
            )
            self._record_audit(script, 'edit', 'Update script content', self.request)

        # 规则驱动审核:内容或Category有变更时, re-match规则start审核(非Pending approvalStatus才start)
        if script.status != 2 and (old_content != script.content or old_category != script.category):
            self._create_approval(script, self.request)

    def perform_destroy(self, instance):
        if instance.status == 2:
            from rest_framework.exceptions import ValidationError
            raise ValidationError("Script is pending approval, please withdraw before deleting")
        self._record_audit(instance, 'delete', 'Delete script', self.request)
        instance.delete()

    @action(methods=['delete'], detail=False, url_path='multiple_delete')
    def multiple_delete(self, request, *args, **kwargs):
        """Batch delete, 排除Pending approvalStatus/Script"""
        request_data = request.data
        keys = request_data.get('keys', None)
        if not keys:
            return ErrorResponse(msg="keys field not provided")
        qs = self.get_queryset().filter(id__in=keys)
        pending_count = qs.filter(status=2).count()
        if pending_count > 0:
            pending_names = list(qs.filter(status=2).values_list('name', flat=True))
            return ErrorResponse(
                msg=f"Selected contains {pending_count} pending approval scripts, cannot delete. Please withdraw first: {', '.join(pending_names)}"
            )
        # recordauditLog
        for script in qs:
            self._record_audit(script, 'delete', 'Batch delete script', request)
        qs.delete()
        return SuccessResponse(data=[], msg="Deleted successfully")

    @action(detail=True, methods=['post'], url_path='toggle-status')
    @require_share_perm('script:toggle_status')
    def toggle_status(self, request, pk=None):
        """切换ScriptEnable/DisableStatus"""
        script = self.get_object()
        if script.status == 0:  # Active
            script.status = 1  # Disabled
            new_status = 'Disabled'
            old_status = 'Enabled'
            script.save()
            self._record_audit(script, 'status',
                               f'Script status changed from [{old_status}] to [{new_status}]', request)
            return DetailResponse(data={'status': script.status},
                                 msg=f"Script status changed to [{new_status}]")
        elif script.status == 2:  # Pending approval
            return ErrorResponse(msg="Script with pending approval must go through approval flow to enable")
        elif script.status == 3:  # Archived
            return ErrorResponse(msg="Archived script must be unarchived before enabling")
        else:
            # Disabled或其他Status, AttemptEnable
            if self._is_public_category(script.category):
                # Publicdirectory下/ScriptrequireCommitApproval才能Enable
                script.status = 2  # Pending approval
                script.save()
                self._create_approval(script, request)
                self._record_audit(script, 'status',
                                   'Submit public script for approval', request)
                return DetailResponse(data={'status': script.status},
                                     msg="Submitted for approval, awaiting admin approval")
            else:
                script.status = 0
                new_status = 'Enabled'
                old_status = 'Disabled'
                script.save()
                self._record_audit(script, 'status',
                                   f'Script status changed from [{old_status}] to [{new_status}]', request)
                return DetailResponse(data={'status': script.status},
                                     msg=f"Script status changed to [{new_status}]")

    @action(detail=True, methods=['post'], url_path='archive')
    @require_share_perm('script:archive')
    def archive(self, request, pk=None):
        """归档Script(Status变更为Archived)"""
        script = self.get_object()
        if script.status == 2:
            return ErrorResponse(msg="Script is pending approval, please cancel or wait before archiving")
        if script.status == 3:
            return ErrorResponse(msg="Script is already archived")
        old_status = dict(script.STATUS_CHOICES).get(script.status, 'Unknown')
        script.status = 3
        script.save()
        self._record_audit(script, 'status',
                           f'Script status changed to [Archived]', request)
        return DetailResponse(data={'status': script.status}, msg="ScriptArchived")

    @action(detail=True, methods=['post'], url_path='unarchive')
    @require_share_perm('script:archive')
    def unarchive(self, request, pk=None):
        """Cancel归档(StatusRestore为Active)"""
        script = self.get_object()
        if script.status != 3:
            return ErrorResponse(msg="Script is not archived")
        # PublicScriptCancel归档后需走Approval流程
        if self._is_public_category(script.category):
            script.status = 2  # Pending approval
            script.save()
            self._create_approval(script, request)
            self._record_audit(script, 'status',
                               'Cancel archive and submit for approval', request)
            return DetailResponse(data={'status': script.status},
                                 msg="Submitted for approval, awaiting admin approval")
        else:
            script.status = 0
            script.save()
            self._record_audit(script, 'status',
                               'Script status changed from [Archived] to [Enabled]', request)
            return DetailResponse(data={'status': script.status}, msg="Script unarchived")

    @action(detail=True, methods=['post'], url_path='copy')
    @require_share_perm('script:copy')
    def copy_script(self, request, pk=None):
        """CopyScript"""
        script = self.get_object()
        new_name = f"{script.name}_copy"
        # 确保NameUnique
        base_name = new_name
        count = 1
        while Script.objects.filter(name=new_name).exists():
            count += 1
            new_name = f"{base_name}{count}"
        new_script = Script.objects.create(
            name=new_name,
            script_type=script.script_type,
            category=script.category,
            auth_type=script.auth_type,
            tags=script.tags,
            desc=script.desc,
            content=script.content,
            current_version='V1.0',
            timeout=script.timeout,
            concurrent=script.concurrent,
            fail_strategy=script.fail_strategy,
            open_risk_check=script.open_risk_check,
            need_audit=script.need_audit,
            log_retention=script.log_retention,
            script_params=script.script_params,
            script_envs=script.script_envs,
            status=0,
        )
        # 自动detect风险等级
        self._detect_and_set_risk_level(new_script)
        # create初始Version
        ScriptVersion.objects.create(
            script=new_script,
            version='V1.0',
            content=new_script.content,
            desc='Create copy',
            is_current=True,
        )
        # 规则驱动审核:Attemptmatch规则
        self._create_approval(new_script, request)
        self._record_audit(new_script, 'copy',
                           f'Copied from Script[{script.name}]', request)
        return DetailResponse(data={'id': new_script.id, 'name': new_script.name},
                             msg="ScriptCopySuccess")

    @action(detail=False, methods=['post'], url_path='check-risk')
    def check_risk(self, request):
        """detectScript content/风险.

        body: {
            "content": "Script content",
            "script_type": "Shell"  // Optional
        }
        """
        from taurus.script_checker import ScriptCheckService

        content = request.data.get('content', '') or ''
        script_type = request.data.get('script_type', '') or ''

        result = ScriptCheckService.check(content, script_type)

        return DetailResponse(data=result.to_dict(), msg='Detection complete')

    @action(detail=False, methods=['get'], url_path='stats')
    def get_stats(self, request):
        """FetchScript统计Message"""
        from taurus.utils.share_permission import ShareVisibleQS
        from datetime import timedelta
        from django.db.models import Count
        user = request.user
        script_qs = Script.objects.all()
        if not getattr(user, 'is_superuser', False):
            script_qs = ShareVisibleQS.filter_visible_scripts(script_qs, user, request, include_shared=False)
        total = script_qs.count()
        public_count = script_qs.filter(auth_type='public').count()
        risk_count = script_qs.filter(need_audit=True).count()
        pending_approve = ScriptApprove.objects.filter(status='pending', script__in=script_qs.values('pk')).count()
        now = timezone.now()
        today = now.date()
        visible_script_ids = script_qs.values('pk')

        # 统计所有ScriptExecutionrecord(手动Execution + Scheduled taskExecution)
        # 过去 7 天内完成/Execution
        seven_days_ago = now - timedelta(days=7)
        # 手动ScriptExecution(OpsExecution 中 execution_type='script')
        # Note:OpsExecution no script foreign key(存/Yes内容Snapshot), so不Per可见ScriptFilter, 保持global统计
        ops_exec_qs = OpsExecution.objects.filter(
            execution_type='script',
            create_datetime__gte=seven_days_ago,
        )
        # Scheduled taskExecution(via task__script Related to Script, 可Per可见ScriptFilter)
        task_exec_qs = ScriptTaskExecution.objects.filter(
            create_datetime__gte=seven_days_ago,
            task__script__in=visible_script_ids,
        )

        # Success率compute:过去 7 天内(Success数 / 完成总数), 完成总数 = Success + Failed
        finished_ops = ops_exec_qs.filter(status__in=[2, 3]).count()
        success_ops = ops_exec_qs.filter(status=2).count()
        finished_task = task_exec_qs.filter(status__in=[2, 3]).count()
        success_task = task_exec_qs.filter(status=2).count()
        total_finished = finished_ops + finished_task
        total_success = success_ops + success_task
        if total_finished > 0:
            success_rate = round(total_success * 100.0 / total_finished, 1)
        else:
            success_rate = 0.0

        # 今日Execution count(所有ScriptExecution/总次数, ContainsSuccess/Failed/Running)
        today_ops = OpsExecution.objects.filter(
            execution_type='script', create_datetime__date=today
        ).count()
        today_task = ScriptTaskExecution.objects.filter(
            create_datetime__date=today, task__script__in=visible_script_ids
        ).count()
        today_exec = today_ops + today_task

        # Execution result分布(过去 7 天)
        result_success = total_success
        result_fail = finished_ops - success_ops + finished_task - success_task
        result_running = ops_exec_qs.filter(status=1).count() + task_exec_qs.filter(status=1).count()

        top_scripts = list(
            script_qs.order_by('-exec_count').values('name', 'exec_count')[:5]
        )
        type_dist = list(
            script_qs.values('script_type').annotate(count=Count('id'))
        )

        # 近 7 天Execution趋势(PerDate统计所有ScriptExecution count)
        trend_dates = []
        trend_values = []
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            trend_dates.append(d.strftime('%m-%d'))
            day_ops = OpsExecution.objects.filter(
                execution_type='script', create_datetime__date=d
            ).count()
            day_task = ScriptTaskExecution.objects.filter(
                create_datetime__date=d, task__script__in=visible_script_ids
            ).count()
            trend_values.append(day_ops + day_task)

        return DetailResponse(data={
            'totalScript': total,
            'publicScript': public_count,
            'todayExec': today_exec,
            'successRate': success_rate,
            'riskScript': risk_count,
            'pendingApproveCount': pending_approve,
            'topScripts': top_scripts,
            'trendDates': trend_dates,
            'trendValues': trend_values,
            'typeDistribution': type_dist,
            'resultDistribution': [
                {'name': 'Success', 'value': result_success},
                {'name': 'Failed', 'value': result_fail},
                {'name': 'Running', 'value': result_running},
            ],
        }, msg="Statistics fetched")

    @action(detail=False, methods=['get'], url_path='categories')
    def get_categories(self, request):
        """FetchScriptCategory(含数量统计)"""
        from django.db.models import Count
        from taurus.utils.share_permission import ShareVisibleQS
        user = request.user
        script_qs = Script.objects.all()
        if not getattr(user, 'is_superuser', False):
            script_qs = ShareVisibleQS.filter_visible_scripts(script_qs, user, request, include_shared=False)
        categories = script_qs.values('category').annotate(
            count=Count('id')).order_by('-count')
        data = [{'label': item['category'], 'value': item['category'],
                 'count': item['count']} for item in categories]
        # 添加Alloption
        data.insert(0, {'label': 'All', 'value': '', 'count': script_qs.count()})
        return SuccessResponse(data=data)

    @action(detail=True, methods=['post'], url_path='rollback')
    @require_share_perm('script:manage_version')
    def rollback_version(self, request, pk=None):
        """Rollback到指定Version"""
        script = self.get_object()
        if script.status == 2:
            return ErrorResponse(msg="Script is pending approval, cannot rollback version")
        version_id = request.data.get('version_id')
        if not version_id:
            return ErrorResponse(msg="Missing version ID")
        try:
            version = ScriptVersion.objects.get(id=version_id, script=script)
        except ScriptVersion.DoesNotExist:
            return ErrorResponse(msg="Version not found")
        # SavecurrentStatus为新Version(Convenient for再Rollback)
        script.versions.filter(is_current=True).update(is_current=False)
        versions = list(script.versions.values_list('version', flat=True))
        max_version = 1
        for v in versions:
            try:
                num = int(v.replace('V', '').split('.')[0])
                if num > max_version:
                    max_version = num
            except:
                pass
        new_version = f'V{max_version + 1}.0'
        script.content = version.content
        script.current_version = new_version
        script.save()
        ScriptVersion.objects.create(
            script=script,
            version=new_version,
            content=script.content,
            desc=f'Rollback to {version.version}',
            is_current=True,
        )
        self._record_audit(script, 'rollback',
                           f'Rollback to version {version.version}', request)
        # Rollback后若内容变更, requirere-match审核
        self._create_approval(script, request)
        return DetailResponse(data={'new_version': new_version, 'status': script.status},
                             msg=f"Rolled back to version {version.version}" + (
                                 ", submitted for approval" if script.status == 2 else ""
                             ))

    @action(detail=True, methods=['post'], url_path='save-as')
    @require_share_perm('script:copy')
    def save_as(self, request, pk=None):
        """另存为:将官方ScriptSave为UserPrivateScript

        body: {
            "name": "新ScriptName",  // Optional, 默认在原Name后加"副本"
            "category_id": null   // Optional, TargetCategoryID
        }
        """
        script = self.get_object()
        new_name = request.data.get('name') or f"{script.name}_copy"
        category_id = request.data.get('category_id')

        count = 1
        base_name = new_name
        while Script.objects.filter(name=new_name).exists():
            count += 1
            new_name = f"{base_name}{count}"

        category = None
        if category_id:
            try:
                category = ScriptCategory.objects.get(id=category_id)
            except ScriptCategory.DoesNotExist:
                pass

        new_script = Script.objects.create(
            name=new_name,
            script_type=script.script_type,
            category=category,
            auth_type='private',
            tags=script.tags,
            desc=f"Created from official script '{script.name}'\n\n{script.desc or ''}",
            content=script.content,
            current_version='V1.0',
            timeout=script.timeout,
            concurrent=script.concurrent,
            fail_strategy=script.fail_strategy,
            open_risk_check=script.open_risk_check,
            need_audit=script.need_audit,
            log_retention=script.log_retention,
            script_params=script.script_params,
            script_envs=script.script_envs,
            status=0,
            is_official=False,
            source='user',
            supported_systems=script.supported_systems,
            creator=request.user,
        )
        # 自动detect风险等级
        self._detect_and_set_risk_level(new_script)
        ScriptVersion.objects.create(
            script=new_script,
            version='V1.0',
            content=new_script.content,
            desc=f'Saved As from official script {script.name}',
            is_current=True,
        )
        self._record_audit(new_script, 'save_as',
                           f'Saved As from official Script[{script.name}]', request)
        return DetailResponse(
            data={'id': new_script.id, 'name': new_script.name},
            msg="Script saved as copy"
        )

    @staticmethod
    def _compare_version(v1: str, v2: str) -> int:
        """
        比较Version号, support v1.0.0 / V1.0 / 1.0 等Format
        return  1 表示 v1 > v2
        return -1 表示 v1 < v2
        return  0 表示 v1 == v2
        """
        import re

        def _parse(v: str):
            if not v:
                return (0, 0, 0)
            v = re.sub(r'^[vV]', '', str(v)).strip()
            parts = re.split(r'[.\-+]', v)
            result = []
            for p in parts[:3]:
                try:
                    result.append(int(re.sub(r'\D', '', p) or '0'))
                except (ValueError, TypeError):
                    result.append(0)
            while len(result) < 3:
                result.append(0)
            return tuple(result)

        p1, p2 = _parse(v1), _parse(v2)
        if p1 > p2:
            return 1
        if p1 < p2:
            return -1
        return 0

    @action(detail=False, methods=['get'], url_path='check-official-updates')
    def check_official_updates(self, request):
        """check官方ScriptUpdate报告(仅Administrator)

        return:
        - to_create:    代码library有, DB中不存在(待Create)
        - to_upgrade:   DB中存在, 但代码library官方Version更高(待upgrade)
        - to_discontinue: DB中存在 is_official=True, 但代码library已remove(待弃用)
        - up_to_date:   已Yes最新
        """
        if not request.user.is_staff:
            return ErrorResponse(msg="Only admin can check official script updates")

        from taurus.official_scripts import OFFICIAL_SCRIPTS

        official_names = [s['name'] for s in OFFICIAL_SCRIPTS]
        db_official_qs = Script.objects.filter(is_official=True)
        db_official_map = {s.name: s for s in db_official_qs}

        to_create = []
        to_upgrade = []
        to_discontinue = []
        up_to_date = []

        for script_data in OFFICIAL_SCRIPTS:
            name = script_data['name']
            new_version = script_data.get('official_version', 'v1.0.0')
            if name not in db_official_map:
                to_create.append({
                    'name': name,
                    'script_type': script_data.get('script_type', 'Shell'),
                    'category_name': script_data.get('category_name', ''),
                    'official_version': new_version,
                    'risk_level': script_data.get('risk_level', 'low'),
                    'desc': script_data.get('desc', ''),
                    'changelog': script_data.get('changelog', ''),
                })
            else:
                db_script = db_official_map[name]
                cmp_result = self._compare_version(new_version, db_script.official_version or '')
                if cmp_result > 0:
                    to_upgrade.append({
                        'id': db_script.id,
                        'name': name,
                        'db_version': db_script.official_version or '',
                        'new_version': new_version,
                        'risk_level': script_data.get('risk_level', 'low'),
                        'changelog': script_data.get('changelog', ''),
                    })
                else:
                    up_to_date.append({
                        'name': name,
                        'official_version': new_version,
                    })

        for name, db_script in db_official_map.items():
            if name not in official_names:
                to_discontinue.append({
                    'id': db_script.id,
                    'name': name,
                    'db_version': db_script.official_version or '',
                    'status': db_script.get_status_display() if hasattr(db_script, 'get_status_display') else db_script.status,
                })

        return DetailResponse(
            data={
                'to_create': to_create,
                'to_upgrade': to_upgrade,
                'to_discontinue': to_discontinue,
                'up_to_date': up_to_date,
                'summary': {
                    'total_codebase': len(OFFICIAL_SCRIPTS),
                    'total_db_official': db_official_qs.count(),
                    'to_create': len(to_create),
                    'to_upgrade': len(to_upgrade),
                    'to_discontinue': len(to_discontinue),
                    'up_to_date': len(up_to_date),
                }
            },
            msg="Official script update check complete"
        )

    @action(detail=False, methods=['post'], url_path='init-official')
    def init_official_scripts(self, request):
        """initialize / 同步官方内置Script(仅Administrator可operation)

        mode Parameters:
        - only_add (默认):仅Create DB 中Non-existent官方Script(向后兼容/Idempotency行为)
        - upgrade        :除Create外, 还会upgrade DB 中 official_version 低于代码libraryVersion/Script content
                          (只同步内容relatedField, 不覆盖Administrator调整/Category/timeout/Tags等Config)
        """
        if not request.user.is_staff:
            return ErrorResponse(msg="Only admin can sync official scripts")

        from taurus.official_scripts import OFFICIAL_SCRIPTS
        mode = (request.data or {}).get('mode', 'only_add')
        if mode not in ('only_add', 'upgrade'):
            return ErrorResponse(msg="Invalid mode parameter, only only_add/upgrade supported")

        official_names = [s['name'] for s in OFFICIAL_SCRIPTS]
        db_official_qs = Script.objects.filter(is_official=True)
        db_official_map = {s.name: s for s in db_official_qs}

        created_count = 0
        upgraded_count = 0
        skipped_count = 0
        error_messages = []

        for script_data in OFFICIAL_SCRIPTS:
            name = script_data['name']
            new_version = script_data.get('official_version', 'v1.0.0')

            if name in db_official_map:
                db_script = db_official_map[name]
                if mode == 'only_add':
                    skipped_count += 1
                    continue
                cmp_result = self._compare_version(new_version, db_script.official_version or '')
                if cmp_result <= 0:
                    skipped_count += 1
                    continue

                try:
                    updated_fields = []
                    if db_script.content != script_data.get('content', ''):
                        db_script.content = script_data.get('content', '')
                        updated_fields.append('content')
                    new_params = script_data.get('script_params', [])
                    if db_script.script_params != new_params:
                        db_script.script_params = new_params
                        updated_fields.append('script_params')
                    new_envs = script_data.get('script_envs', [])
                    if db_script.script_envs != new_envs:
                        db_script.script_envs = new_envs
                        updated_fields.append('script_envs')
                    db_script.official_version = new_version
                    updated_fields.append('official_version')
                    new_changelog = script_data.get('changelog', '')
                    if db_script.changelog != new_changelog:
                        db_script.changelog = new_changelog
                        updated_fields.append('changelog')
                    new_source_url = script_data.get('source_url', '')
                    if db_script.source_url != new_source_url:
                        db_script.source_url = new_source_url
                        updated_fields.append('source_url')
                    new_systems = script_data.get('supported_systems', '')
                    if db_script.supported_systems != new_systems:
                        db_script.supported_systems = new_systems
                        updated_fields.append('supported_systems')
                    new_risk = script_data.get('risk_level', 'low')
                    if db_script.risk_level != new_risk:
                        db_script.risk_level = new_risk
                        updated_fields.append('risk_level')
                        if new_risk == 'high':
                            db_script.need_audit = True
                            updated_fields.append('need_audit')

                    if updated_fields:
                        old_version = db_script.current_version
                        ScriptVersion.objects.filter(script=db_script, is_current=True).update(is_current=False)
                        ScriptVersion.objects.create(
                            script=db_script,
                            version=new_version,
                            content=db_script.content,
                            desc=f'Official version upgraded: {old_version or "N/A"} → {new_version}\n{new_changelog}',
                            is_current=True,
                            creator=request.user,
                        )
                        db_script.current_version = new_version
                        if 'current_version' not in updated_fields:
                            updated_fields.append('current_version')
                        db_script.save(update_fields=updated_fields + ['update_datetime', 'modifier'])
                        db_script.modifier = request.user
                        db_script.save(update_fields=['modifier'])
                        upgraded_count += 1
                    else:
                        skipped_count += 1
                except Exception as e:
                    error_messages.append(f"Script upgrade failed《{name}》: {str(e)}")
                    skipped_count += 1
                continue

            category = None
            category_name = script_data.get('category_name')
            if category_name:
                category, _ = ScriptCategory.objects.get_or_create(
                    name=category_name,
                    defaults={'sort': 1, 'category_type': 'script'}
                )

            need_audit = script_data.get('risk_level') == 'high'
            script = Script.objects.create(
                name=name,
                script_type=script_data.get('script_type', 'Shell'),
                category=category,
                auth_type='public',
                tags=script_data.get('tags', ''),
                desc=script_data.get('desc', ''),
                content=script_data.get('content', ''),
                current_version=new_version,
                timeout=script_data.get('timeout', 300),
                concurrent=10,
                fail_strategy='continue',
                open_risk_check=True,
                need_audit=need_audit,
                log_retention=3650,
                script_params=script_data.get('script_params', []),
                script_envs=script_data.get('script_envs', []),
                status=0,
                is_official=True,
                source='official_import',
                source_url=script_data.get('source_url', ''),
                license_type=script_data.get('license_type', 'MIT'),
                supported_systems=script_data.get('supported_systems', ''),
                risk_level=script_data.get('risk_level', 'low'),
                official_version=new_version,
                changelog=script_data.get('changelog', ''),
                creator=request.user,
                modifier=request.user,
            )
            ScriptVersion.objects.create(
                script=script,
                version=script.current_version,
                content=script.content,
                desc='Official initial version',
                is_current=True,
                creator=request.user,
            )
            created_count += 1

        discontinued_count = 0
        for name, _ in db_official_map.items():
            if name not in official_names:
                discontinued_count += 1

        msg_parts = [
            f"Official script sync done: added {created_count}",
            f"upgraded {upgraded_count}",
            f"skipped {skipped_count}",
        ]
        if discontinued_count > 0:
            msg_parts.append(f"Deprecated in codebase: {discontinued_count} (info only, not modified)")
        if error_messages:
            msg_parts.append(f"Failed: {len(error_messages)}")

        data = {
            'created': created_count,
            'upgraded': upgraded_count,
            'skipped': skipped_count,
            'discontinued': discontinued_count,
            'errors': error_messages,
            'total_processed': created_count + upgraded_count + skipped_count,
        }

        if error_messages:
            return ErrorResponse(data=data, msg=";".join(msg_parts) + "：" + "；".join(error_messages))
        return DetailResponse(data=data, msg=";".join(msg_parts))

    # ============================================================
    # Share-related actions
    # ============================================================

    @action(detail=False, methods=['GET'], url_path='perm-defs')
    def perm_defs(self, request):
        """FetchScript type/PermissionDefinitionDictionary(Per category group)"""
        qs = SharePermissionDef.objects.filter(
            resource_type='script', is_active=True
        ).order_by('category', 'sort')
        grouped = {}
        category_display_map = dict(SharePermissionDef.CATEGORY_CHOICES)
        for item in qs:
            grouped.setdefault(item.category, []).append(
                SharePermissionDefSerializer(item).data
            )
        data = [
            {
                'category': k,
                'category_display': category_display_map.get(k, k),
                'perms': v,
            }
            for k, v in grouped.items()
        ]
        return SuccessResponse(data=data, msg='Fetched successfully')

    @action(detail=True, methods=['GET', 'POST'], url_path='shares')
    def shares(self, request, pk=None):
        """
        GET  list该Script/直接分享record
        POST 批量Create直接分享(support多主体一次create)
        要求:script:manage_share Permission  +  F_SCRIPT_SHARING (EE-only)
        """
        try:
            from taurus_ee.utils.gate import ee_service_or_403
        except ImportError:
            from taurus.ee_fallback import ee_service_or_403
        from taurus.editions.features import F_SCRIPT_SHARING
        # Double Guard: Feature + EE Service 就绪（CE 会 403）
        ee_service_or_403('share_service', F_SCRIPT_SHARING)
        script = self.get_object()
        if not SharePermissionChecker.has_perm(request.user, 'script', script, 'script:manage_share', request=request):
            raise PermissionDenied('No permission to manage this script sharing')
        if request.method == 'GET':
            queryset = ScriptSharePermission.objects.filter(script=script
            ).order_by('-create_datetime')
            page = self.paginate_queryset(queryset)
            perm_defs = {
                p.perm_code: SharePermissionDefSerializer(p).data
                for p in SharePermissionDef.objects.filter(resource_type='script', is_active=True)
            }
            from dvadmin.system.models import Users, Role, Dept
            subject_ids = {'user': [], 'role': [], 'dept': []}
            for sp in queryset:
                subject_ids[sp.subject_type].append(sp.subject_id)
            subject_info_map = {}
            if subject_ids['user']:
                for u in Users.objects.filter(id__in=subject_ids['user']).values('id', 'username', 'name'):
                    subject_info_map[f"user:{u['id']}"] = {
                        'id': u['id'], 'name': u.get('name') or u['username'], 'username': u['username'],
                    }
            if subject_ids['role']:
                for r in Role.objects.filter(id__in=subject_ids['role']).values('id', 'name', 'key'):
                    subject_info_map[f"role:{r['id']}"] = {
                        'id': r['id'], 'name': r['name'], 'key': r.get('key', ''),
                    }
            if subject_ids['dept']:
                for d in Dept.objects.filter(id__in=subject_ids['dept']).values('id', 'name', 'key'):
                    subject_info_map[f"dept:{d['id']}"] = {
                        'id': d['id'], 'name': d['name'], 'dept_code': d.get('key', ''),
                    }
            ctx = {'perm_defs': perm_defs, 'subject_info_map': subject_info_map}
            if page is not None:
                serializer = ScriptSharePermissionSerializer(page, many=True, context=ctx)
                return self.get_paginated_response(serializer.data)
            serializer = ScriptSharePermissionSerializer(queryset, many=True, context=ctx)
            return SuccessResponse(data=serializer.data, msg='Fetched successfully')
        # POST: batch create / update_or_create
        ser = SharePermissionBatchCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        vd = ser.validated_data
        created = []
        for sub in vd['subjects']:
            stype = str(sub.get('subject_type', '')).strip()
            sid = str(sub.get('subject_id', '')).strip()
            if not stype or not sid or stype not in ('user', 'role', 'dept'):
                continue
            sname = sub.get('subject_name') or sub.get('name') or ''
            obj, _ = ScriptSharePermission.objects.update_or_create(
                script=script, subject_type=stype, subject_id=sid,
                defaults={
                    'subject_name_cache': sname,
                    'permissions': vd['permissions'],
                    'expire_time': vd.get('expire_time'),
                    'remark': vd.get('remark') or '',
                    'grant_user': request.user if getattr(request.user, 'id', None) else None,
                }
            )
            created.append(obj.id)
        return SuccessResponse(data={'created_ids': created}, msg='Share created successfully')

    @action(detail=True, methods=['PUT', 'DELETE'], url_path=r'shares/(?P<share_id>[^/.]+)')
    def share_detail(self, request, pk=None, share_id=None):
        """modify或Delete单entriesScript直接分享record(需 manage_share + EE能力gate)"""
        try:
            from taurus_ee.utils.gate import ee_service_or_403
        except ImportError:
            from taurus.ee_fallback import ee_service_or_403
        from taurus.editions.features import F_SCRIPT_SHARING
        ee_service_or_403('share_service', F_SCRIPT_SHARING)
        script = self.get_object()
        if not SharePermissionChecker.has_perm(request.user, 'script', script, 'script:manage_share', request=request):
            raise PermissionDenied('No permission to manage this script sharing')
        try:
            share = ScriptSharePermission.objects.get(pk=share_id, script=script)
        except ScriptSharePermission.DoesNotExist:
            return ErrorResponse(msg='Share record not found', status=404)
        if request.method == 'DELETE':
            share.delete()
            return SuccessResponse(msg='Deleted successfully')
        # PUT
        perms = request.data.get('permissions')
        if perms is None:
            return ErrorResponse(msg='permissions field is required')
        share.permissions = perms
        share.expire_time = request.data.get('expire_time', share.expire_time)
        share.remark = request.data.get('remark', share.remark)
        share.save()
        return DetailResponse(data=ScriptSharePermissionSerializer(share).data, msg='Updated successfully')

    @action(detail=True, methods=['GET'], url_path='effective-perms')
    @require_share_perm('script:view')
    def effective_perms(self, request, pk=None):
        """Fetchcurrent登录User对该Script/有效Permissionlist (EE-only)"""
        try:
            from taurus_ee.utils.gate import ee_service_or_403
        except ImportError:
            from taurus.ee_fallback import ee_service_or_403
        from taurus.editions.features import F_SCRIPT_SHARING
        ee_service_or_403('share_service', F_SCRIPT_SHARING)
        script = self.get_object()
        perms = SharePermissionChecker.get_user_effective_perms(
            request.user, 'script', script, request=request
        )
        perm_defs = {
            p.perm_code: SharePermissionDefSerializer(p).data
            for p in SharePermissionDef.objects.filter(resource_type='script', is_active=True)
        }
        details = [perm_defs[p] for p in perms if p in perm_defs]
        is_owner = bool(getattr(request.user, 'is_superuser', False))
        if not is_owner and script.creator_id is not None:
            is_owner = (str(script.creator_id) == str(getattr(request.user, 'pk', None)))
        data = {
            'permissions': sorted(list(perms)),
            'details': details,
            'is_owner': is_owner,
        }
        return SuccessResponse(data=data, msg='Fetched successfully')

    def _prefetch_share_info_for_list(self, page_items, user, request):
        """为list批量注入 share_summary 和 current_perms, Avoid N+1 Query"""
        from django.utils import timezone
        from django.contrib.auth import get_user_model
        from taurus.models import (
            ScriptSharePermission, ShareLink, SharePermissionDef,
        )
        from dvadmin.system.models import Role, Dept
        from taurus.utils.share_permission import SharePermissionChecker

        User = get_user_model()
        if not page_items:
            return
        script_ids = [s.id for s in page_items]
        script_by_id = {s.id: s for s in page_items}

        # ===== 1. 直接分享 =====
        now = timezone.now()
        now_cond = models.Q(expire_time__isnull=True) | models.Q(expire_time__gte=now)
        perms_qs = ScriptSharePermission.objects.filter(
            script_id__in=script_ids
        ).filter(now_cond).order_by('-create_datetime')
        perms_by_script: dict = {sid: [] for sid in script_ids}
        subject_user_ids: set = set()
        subject_role_ids: set = set()
        subject_dept_ids: set = set()
        for sp in perms_qs:
            perms_by_script.setdefault(sp.script_id, []).append(sp)
            if sp.subject_type == 'user':
                try:
                    subject_user_ids.add(int(sp.subject_id))
                except (ValueError, TypeError):
                    pass
            elif sp.subject_type == 'role':
                try:
                    subject_role_ids.add(int(sp.subject_id))
                except (ValueError, TypeError):
                    pass
            elif sp.subject_type == 'dept':
                try:
                    subject_dept_ids.add(int(sp.subject_id))
                except (ValueError, TypeError):
                    pass

        # Subject name map
        user_names: dict = {}
        if subject_user_ids:
            user_names = {
                str(u.id): u.username for u in User.objects.filter(id__in=subject_user_ids).only('id', 'username')
            }
        role_names: dict = {}
        if subject_role_ids:
            role_names = {
                str(r.id): r.name for r in Role.objects.filter(id__in=subject_role_ids).only('id', 'name')
            }
        dept_names: dict = {}
        if subject_dept_ids:
            dept_names = {
                str(d.id): d.name for d in Dept.objects.filter(id__in=subject_dept_ids).only('id', 'name')
            }
        subject_type_label = {'user': 'User', 'role': 'Role', 'dept': 'Department'}

        # ===== 2. Share link =====
        links_qs = ShareLink.objects.filter(
            resource_type='script',
            resource_id__in=[str(x) for x in script_ids] + [int(x) for x in script_ids],
            is_active=True,
        ).filter(now_cond).order_by('-create_datetime')
        links_by_script: dict = {sid: [] for sid in script_ids}
        for lk in links_qs:
            try:
                s_id = int(lk.resource_id)
            except (ValueError, TypeError):
                continue
            if s_id in links_by_script:
                links_by_script[s_id].append(lk)

        # ===== 3. 汇总 =====
        for script in page_items:
            sid = script.id
            direct_list = perms_by_script.get(sid, []) or []
            link_list = links_by_script.get(sid, []) or []
            subjects_out = []
            perm_names_set = set()
            for sp in direct_list:
                if sp.subject_type == 'user':
                    name = user_names.get(str(sp.subject_id), f"User#{sp.subject_id}")
                elif sp.subject_type == 'role':
                    name = role_names.get(str(sp.subject_id), f"Role#{sp.subject_id}")
                elif sp.subject_type == 'dept':
                    name = dept_names.get(str(sp.subject_id), f"Dept#{sp.subject_id}")
                else:
                    name = f"{sp.subject_type}#{sp.subject_id}"
                perms_list = list(sp.permissions or [])
                perm_names_set.update(perms_list)
                perm_display_list = []
                for pc in perms_list[:5]:
                    try:
                        perm_display_list.append(pc.split(':', 1)[-1])
                    except Exception:
                        perm_display_list.append(pc)
                subjects_out.append({
                    'subject_type': sp.subject_type,
                    'subject_type_label': subject_type_label.get(sp.subject_type, sp.subject_type),
                    'subject_id': str(sp.subject_id),
                    'subject_name': name,
                    'perm_count': len(perms_list),
                    'perm_sample': perm_display_list,
                    'expire_time': sp.expire_time.isoformat() if sp.expire_time else None,
                })
            links_out = []
            for lk in link_list:
                scope_label = 'Anyone' if lk.access_scope == 'anyone' else 'Logged-in only'
                bind_info = ''
                if lk.bind_subject_type:
                    type_map = {'user': 'User', 'role': 'Role', 'dept': 'Department'}
                    bind_info = f" bound to {type_map.get(lk.bind_subject_type, lk.bind_subject_type)}"
                links_out.append({
                    'id': lk.id,
                    'share_token': lk.share_token or '',
                    'name': lk.remark or f"Share link {scope_label}{bind_info}",
                    'scope': lk.access_scope or 'authenticated',
                    'access_count': lk.current_access_count or 0,
                    'max_access': lk.max_access_count or 0,
                    'expire_time': lk.expire_time.isoformat() if lk.expire_time else None,
                    'has_password': False,
                    'is_active': bool(lk.is_active),
                })
            setattr(script, '_share_summary', {
                'total': len(subjects_out) + len(links_out),
                'direct_count': len(subjects_out),
                'link_count': len(links_out),
                'subjects': subjects_out,
                'links': links_out,
            })

        # ===== 4. Current user effective perms (avoid N+1 per-script query) =====
        perm_defs_all = [
            p.perm_code for p in SharePermissionDef.objects.filter(
                resource_type='script', is_active=True
            ).only('perm_code')
        ]
        for script in page_items:
            owner = bool(getattr(user, 'is_superuser', False)) or (
                getattr(script, 'creator_id', None) is not None
                and str(script.creator_id) == str(getattr(user, 'pk', None))
            )
            if owner:
                setattr(script, '_current_perms', list(perm_defs_all))
            else:
                perms = SharePermissionChecker.get_user_effective_perms(
                    user, 'script', script, request=request
                )
                setattr(script, '_current_perms', list(perms))

    def list(self, request, *args, **kwargs):
        """list:叠加分享可见性后, 批量注入 share_summary / current_perms"""
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            self._prefetch_share_info_for_list(page, request.user, request)
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        data_list = list(queryset)
        self._prefetch_share_info_for_list(data_list, request.user, request)
        serializer = self.get_serializer(data_list, many=True)
        return SuccessResponse(data=serializer.data, msg='Fetched successfully')

    def retrieve(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            obj = self.get_object()
            if not SharePermissionChecker.has_perm(request.user, "script", obj, "script:view", request=request):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Missing share permission: script:view")
        return super().retrieve(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            obj = self.get_object()
            if not SharePermissionChecker.has_perm(request.user, "script", obj, "script:edit", request=request):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Missing share permission: script:edit")
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            obj = self.get_object()
            if not SharePermissionChecker.has_perm(request.user, "script", obj, "script:edit", request=request):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Missing share permission: script:edit")
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            obj = self.get_object()
            if not SharePermissionChecker.has_perm(request.user, "script", obj, "script:delete", request=request):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Missing share permission: script:delete")
        return super().destroy(request, *args, **kwargs)



class ScriptVersionViewSet(CustomModelViewSet):
    """Script version management"""
    queryset = ScriptVersion.objects.all()
    serializer_class = ScriptVersionSerializer
    filterset_fields = ['script']
    search_fields = ['version', 'desc']
    ordering = ['-create_datetime']

    def perform_create(self, serializer):
        # === 配额校验：社区版单脚本历史版本上限（FIFO 自动淘汰最旧非当前版本） ===
        from taurus.editions.loader import evict_to_quota as _evict
        script = serializer.validated_data.get('script')
        if script is not None:
            _evict(script.versions.all(), 'max_script_versions_per_script',
                   filter_exclude={'is_current': True})
        super().perform_create(serializer)


class ScriptPermissionViewSet(CustomModelViewSet):
    """ScriptPermissionConfig管理"""
    queryset = ScriptPermission.objects.all()
    serializer_class = ScriptPermissionSerializer
    filterset_fields = ['script', 'subject_type', 'auth_level']
    search_fields = ['subject_id']
    ordering = ['-create_datetime']

    def perform_create(self, serializer):
        serializer.save(grant_user=self.request.user)


class ScriptTaskViewSet(CustomModelViewSet):
    """ScriptScheduled task管理"""
    queryset = ScriptTask.objects.all()
    serializer_class = ScriptTaskSerializer
    create_serializer_class = ScriptTaskCreateSerializer
    update_serializer_class = ScriptTaskUpdateSerializer
    filterset_fields = ['script', 'enabled', 'schedule_type', 'last_exec_result']
    search_fields = ['name', 'cron_expression', 'description']
    ordering_fields = ['create_datetime', 'last_exec_time', 'next_exec_time', 'exec_count']
    ordering = ['-create_datetime']

    def get_queryset(self):
        from django.db.models import Count, Q
        qs = super().get_queryset().select_related('script', 'creator')
        qs = qs.annotate(
            _running_executions_count=Count(
                'executions',
                filter=Q(executions__status__in=[0, 1]),
                distinct=True,
            )
        )
        script_id = self.request.query_params.get('script_id') or self.request.query_params.get('script')
        if script_id:
            try:
                qs = qs.filter(script_id=int(script_id))
            except (TypeError, ValueError):
                pass
        return qs

    import_field_dict = {
        'name': 'Task name',
        'description': 'Description',
        'script': 'Script ID',
        'schedule_type': {'title': 'Schedule type', 'choices': {'data': {'Cron expression': 'cron', 'Fixed interval': 'interval', 'One-time': 'once'}}},
        'cron_expression': 'Cron expression',
        'interval_seconds': 'Interval seconds',
        'run_once_at': 'Run at (YYYY-MM-DD HH:MM:SS)',
        'hosts': 'Target host IDs (JSON array)',
        'timeout': 'Timeout (seconds)',
        'fail_notify': 'Notify on failure',
        'envs': 'Env vars (JSON)',
        'args': 'Execute args (JSON array)',
        'enabled': 'Enabled',
    }
    import_serializer_class = ScriptTaskCreateSerializer
    update_template_serializer_class = ScriptTaskSerializer
    export_field_label = {
        'name': 'Task name',
        'description': 'Description',
        'script_name': 'Script name',
        'schedule_type_display': 'Schedule type',
        'cron_expression': 'Cron expression',
        'interval_seconds': 'Interval seconds',
        'run_once_at': 'Run at',
        'host_count': 'Host count',
        'timeout': 'Timeout (seconds)',
        'fail_notify': 'Notify on failure',
        'enabled': 'Enabled',
        'exec_count': 'Execution count',
        'last_exec_time': 'Last execution time',
        'last_exec_result_display': 'Last execution result',
        'next_exec_time': 'Next execution time',
        'creator_name': 'Creator',
        'create_datetime': 'Created time',
    }
    export_serializer_class = ScriptTaskSerializer

    def _calc_next_exec_time(self, task):
        """computeNext execution time"""
        from datetime import datetime, timedelta
        now = datetime.now()

        if task.schedule_type == 'interval' and task.interval_seconds:
            return now + timedelta(seconds=task.interval_seconds)
        elif task.schedule_type == 'once' and task.run_once_at:
            return task.run_once_at if task.run_once_at > now else None
        elif task.schedule_type == 'cron' and task.cron_expression:
            # 简化版 cron Parse(只supportStandard 5 位或 6 位Expression)
            try:
                parts = task.cron_expression.strip().split()
                # 去除最后may/ ? 或 *
                if len(parts) >= 5:
                    hour = int(parts[1]) if parts[1].isdigit() else now.hour
                    minute = int(parts[0]) if parts[0].isdigit() else now.minute
                    next_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    if next_time <= now:
                        next_time += timedelta(days=1)
                    return next_time
            except (ValueError, IndexError):
                pass
        return None

    @staticmethod
    def _check_running_executions(task, action: str) -> str | None:
        from django.db.models import Q
        running = task.executions.filter(status__in=[0, 1]).count()
        if running > 0:
            return f"Task has {running} running instances, cannot {action} right now"
        return None

    def perform_create(self, serializer):
        # === 配额校验：ScriptTask + Schedule 总数 ===
        from taurus.editions.loader import check_quota as _check_quota
        from taurus.models import Schedule as _Schedule
        _total = ScriptTask.objects.count() + _Schedule.objects.count()
        _check_quota('max_scheduled_tasks', _total, '定时调度任务')
        script = serializer.validated_data.get('script')
        if script and script.status == 2:
            from rest_framework.exceptions import ValidationError
            raise ValidationError("Script is pending approval, cannot create schedule task")
        if script and script.status in (1, 3):
            from rest_framework.exceptions import ValidationError
            raise ValidationError("Script disabled or archived, cannot create schedule task")
        # 与 ScriptViewSet / dvadmin 规范保持一致:Creator=current登录User
        # (before漏填这行 → 任务 creator 恒为 None → 后续Scheduled triggerExecutionrecord/Creator也为Empty)
        extra = {"creator": self.request.user, "modifier": self.request.user}
        if hasattr(self.request.user, 'dept') and self.request.user.dept and self.request.user.dept_id:
            extra["dept_belong_id"] = self.request.user.dept_id
        task = serializer.save(**extra)
        if task.enabled:
            task.next_exec_time = self._calc_next_exec_time(task)
            task.save()

    def perform_update(self, serializer):
        task = serializer.instance
        script = getattr(task, 'script', None) or serializer.validated_data.get('script')
        if script and script.status == 2:
            from rest_framework.exceptions import ValidationError
            raise ValidationError("Script is pending approval, cannot edit schedule task")
        if script and script.status in (1, 3):
            from rest_framework.exceptions import ValidationError
            raise ValidationError("Script disabled or archived, cannot edit schedule task")
        saved = serializer.save()
        if saved.enabled:
            saved.next_exec_time = self._calc_next_exec_time(saved)
            saved.save()

    def perform_destroy(self, instance):
        script = getattr(instance, 'script', None)
        if script and script.status == 2:
            from rest_framework.exceptions import ValidationError
            raise ValidationError("Script is pending approval, cannot delete schedule task")
        err = self._check_running_executions(instance, "Delete")
        if err:
            from rest_framework.exceptions import ValidationError
            raise ValidationError(err)
        instance.delete()

    def get_serializer_class(self):
        if self.action == 'create':
            return ScriptTaskCreateSerializer
        if self.action in ['update', 'partial_update']:
            return ScriptTaskUpdateSerializer
        return ScriptTaskSerializer

    @action(detail=True, methods=['post'], url_path='toggle')
    def toggle_enabled(self, request, pk=None):
        """切换EnableStatus"""
        task = self.get_object()
        script = getattr(task, 'script', None)
        if script and script.status == 2:
            return ErrorResponse(msg="Script is pending approval, cannot toggle schedule")
        target_enabled = not task.enabled
        if target_enabled:
            if script and script.status in (1, 3):
                return ErrorResponse(msg="Script disabled or archived, cannot enable task")
            if task.schedule_type == 'once' and task.run_once_at and task.run_once_at <= timezone.now():
                return ErrorResponse(msg="One-time task execution time expired, please re-set execution time before enabling")
            # Enable时仍requireCheck:防止Dispatch重复叠加
            err = self._check_running_executions(task, "Enable")
            if err:
                return ErrorResponse(msg=err)
        # 停用:任何场景都allow(Running任务停用后不再Dispatch新Execution, 但不中断正在run/Instance)
        task.enabled = target_enabled
        if task.enabled:
            task.next_exec_time = self._calc_next_exec_time(task)
        else:
            task.next_exec_time = None
        task.save()
        return DetailResponse(
            data={'enabled': task.enabled, 'next_exec_time': task.next_exec_time},
            msg=f"Task {'enabled' if task.enabled else 'disabled'}"
        )

    @action(detail=True, methods=['post'], url_path='execute')
    def execute_now(self, request, pk=None):
        """立即Execution任务"""
        task = self.get_object()
        script = getattr(task, 'script', None)

        # 基础StatusCheck
        if not task.enabled:
            return ErrorResponse(msg="Task is disabled, please enable before executing")
        if script and script.status == 2:
            return ErrorResponse(msg="Script is pending approval, cannot execute now")
        if script and script.status in (1, 3):
            return ErrorResponse(msg="Script disabled or archived, cannot execute")

        # One-time任务:已Execution过/不allow重复立即Execution(unless exec_count=0)
        if task.schedule_type == 'once' and (task.exec_count or 0) > 0:
            return ErrorResponse(msg="One-time task already executed, cannot re-execute manually")

        # 重入protected:有RunningInstance时拒绝Execution, Avoid重复Queueing
        running = task.executions.filter(status__in=[0, 1]).count()
        if running > 0:
            return ErrorResponse(msg=f"There are {running} execution instances running, please wait for completion and retry")

        hosts = list(task.hosts or [])
        if not hosts:
            return ErrorResponse(msg="Task has no target hosts configured, cannot execute")

        # 1. createExecutionrecord(保持与 scheduler dispatcher.create_execution_record 行为一致)
        now = timezone.now()
        try:
            creator = getattr(request.user, 'id', None)
            # creator Yes ForeignKey, require Users Instance;若cannotFetch则不填(与原Implemented一致, 原Implementedroot本没填 creator)
            create_kwargs = dict(
                task=task,
                status=1,
                start_time=now,
                trigger_type='manual',
                executed_hosts=list(task.hosts or []),
            )
            if getattr(request.user, 'pk', None) and hasattr(request.user, 'is_authenticated') and request.user.is_authenticated:
                # 优先填 user object, Avoid 'Must be a Users instance' Error
                try:
                    create_kwargs['creator'] = request.user
                    create_kwargs['modifier'] = request.user
                except Exception:
                    pass
            execution = ScriptTaskExecution.objects.create(**create_kwargs)
        except Exception as e:
            logging.exception("execute_now.create_execution_failed", extra={"task_id": task.id, "error": str(e)})
            return ErrorResponse(msg=f"Failed to create execution record: {e}")

        # 2. 构建 payload(与 taurus-scheduler TaskDispatcher._build_payload Format严格一致)
        payload = {
            "source": "taurus-backend-execute_now",
            "v": 1,
            "task": {
                "id": task.id,
                "name": task.name,
                "script_id": task.script_id,
                "hosts": list(task.hosts or []),
                "timeout": task.timeout,
                "envs": dict(task.envs or {}),
                "args": list(task.args or []),
            },
            "execution": {
                "id": execution.id,
                "trigger_type": "manual",
                "scheduled_fire_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            },
        }

        # 3. Push到 Worker use/ Redis queue(与 taurus-scheduler TaskDispatcher._push_to_queue 相同逻辑)
        #
        # Note:直接复用 run_scheduler_worker.Command 里throughValidation/ _default_redis_url(), 
        # 不要自己再抄一份密码拼接逻辑, Avoid Worker 端能连上, 这里却 Authentication required.
        try:
            import json as _json
            import redis as _redis

            from taurus.management.commands.run_scheduler_worker import Command as _WorkerCmd
            redis_dsn = _WorkerCmd._default_redis_url()
            queue_key = os.environ.get("TAURUS_SCHEDULER_QUEUE", "taurus:scheduler:queue:script_task")

            redis_client = _redis.Redis.from_url(
                redis_dsn,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=10,
            )
            # 先 ping 一次, 密码Error会在这里直接raised by AuthenticationError, For easy排查
            redis_client.ping()
            msg = _json.dumps(payload, ensure_ascii=False)
            pushed = redis_client.lpush(queue_key, msg)
            # 防止无限制增长(与 dispatcher 一致 ltrim 0..9999)
            try:
                redis_client.ltrim(queue_key, 0, 9999)
            except Exception:
                pass
            try:
                redis_client.close()
            except Exception:
                pass
            if pushed <= 0:
                raise RuntimeError("redis lpush returned 0, message not queued")
        except Exception as e:
            logging.exception("execute_now.redis_push_failed", extra={"task_id": task.id, "error": str(e)})
            try:
                execution.status = 3
                execution.error_message = f"Failed to enqueue message: {type(e).__name__}: {e}"
                execution.end_time = timezone.now()
                execution.save(update_fields=["status", "error_message", "end_time"])
            except Exception:
                pass
            return ErrorResponse(msg=f"Failed to queue execution task: {type(e).__name__}: {e}")

        # 4. 回写任务元数据(exec_count + last_exec_time 由 Worker Success派发后UnifiedUpdate, Avoid重复 +1)
        return DetailResponse(
            data={
                "execution_id": execution.id,
                "queue": queue_key,
                "status": "queued",
                "message": f"Task committed to dispatch queue (execution id={execution.id}), worker will process. Refresh Execution History tab to view results",
            },
            msg="Task committed to dispatch queue"
        )

    @action(detail=True, methods=['get'], url_path='executions')
    def list_executions(self, request, pk=None):
        """Fetch任务Execution历史(supportPagination)"""
        task = self.get_object()
        page = max(1, int(request.query_params.get('page', 1) or 1))
        page_size = int(request.query_params.get('page_size', 20) or 20)
        page_size = max(1, min(page_size, 500))
        status_filter = request.query_params.get('exec_status') or request.query_params.get('status') or ''
        trigger_filter = request.query_params.get('exec_trigger_type') or request.query_params.get('trigger_type') or ''

        qs = task.executions.all().order_by('-create_datetime')
        if status_filter:
            try:
                qs = qs.filter(status=int(status_filter))
            except (TypeError, ValueError):
                pass
        if trigger_filter:
            qs = qs.filter(trigger_type=trigger_filter)

        total = qs.count()
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        start = (page - 1) * page_size
        end = start + page_size
        page_data = qs[start:end]

        serializer = ScriptTaskExecutionSerializer(page_data, many=True)
        return SuccessResponse(data={
            'results': serializer.data,
            'total': total,
            'page': {
                'current': page,
                'size': page_size,
                'total': total,
                'total_pages': total_pages,
            },
        }, msg='Fetched successfully')

    # ===================================================================
    # [M2.3 EE] ScriptTask × Schedule 双轨统一调度 — 入口（EE 专属）
    # Double Guard: action 首行 ee_service_or_403，CE 直接 403。
    # ===================================================================
    @action(detail=True, methods=['post'], url_path='trigger-now-unified')
    def trigger_now_unified(self, request, pk=None):
        """[EE] ScriptTask 立刻执行（统一调度入口，SCRIPT_TASK_UNIFIED）."""
        try:
            from taurus_ee.utils.gate import ee_service_or_403
        except ImportError:
            from taurus.ee_fallback import ee_service_or_403
        from taurus.editions.features import F_SCRIPT_TASK_UNIFIED
        svc_cls = ee_service_or_403('scheduler_unified_service', F_SCRIPT_TASK_UNIFIED)
        ok, msg = svc_cls.trigger_now(self, request, source='script_task', pk=int(pk))
        return SuccessResponse(msg=msg) if ok else ErrorResponse(msg=msg)

    @action(detail=False, methods=['get'], url_path='unified-stats')
    def unified_stats(self, request):
        """[EE] ScriptTask 统一 KPI 汇总."""
        try:
            from taurus_ee.utils.gate import ee_service_or_403
        except ImportError:
            from taurus.ee_fallback import ee_service_or_403
        from taurus.editions.features import F_SCRIPT_TASK_UNIFIED
        svc_cls = ee_service_or_403('scheduler_unified_service', F_SCRIPT_TASK_UNIFIED)
        stats = svc_cls.unified_stats(self, request)
        return SuccessResponse(data=stats, msg='Unified stats for ScriptTask (EE)')

    @action(detail=True, methods=['post'], url_path='alert-rule')
    def set_alert_rule(self, request, pk=None):
        """[EE] 设置单任务失败告警+重试规则（SCHEDULE_ALERT_RETRY）."""
        try:
            from taurus_ee.utils.gate import ee_service_or_403
        except ImportError:
            from taurus.ee_fallback import ee_service_or_403
        from taurus.editions.features import F_SCHEDULE_ALERT_RETRY
        from rest_framework.exceptions import ValidationError
        from taurus.serializers import SchedulerAlertRuleSerializer
        ee_service_or_403('scheduler_alert_service', F_SCHEDULE_ALERT_RETRY)
        task = self.get_object()
        ser = SchedulerAlertRuleSerializer(data=request.data)
        if not ser.is_valid():
            raise ValidationError(ser.errors)
        # 目前只落日志 + in-app inbox（真实持久化可在 Service 侧扩展）
        import logging as _lgg
        _lgg.getLogger(__name__).info(
            "[SchedulerAlertRule] task_id=%s rule=%s", task.id, dict(ser.validated_data))
        return SuccessResponse(data=ser.validated_data, msg='Alert rule accepted (EE)')


class ScriptTaskExecutionViewSet(CustomModelViewSet):
    """Script scheduled task execution record"""
    queryset = ScriptTaskExecution.objects.all()
    serializer_class = ScriptTaskExecutionSerializer
    filterset_fields = ['task', 'status', 'trigger_type']
    search_fields = ['error_message']
    ordering_fields = ['create_datetime', 'start_time', 'end_time']
    ordering = ['-create_datetime']

    export_field_label = {
        'task_name': 'Task name',
        'status_display': 'Status',
        'trigger_type_display': 'Trigger type',
        'start_time': 'Start time',
        'end_time': 'End time',
        'duration': 'Duration (s)',
        'executed_hosts': 'Host',
        'error_message': 'Error',
        'creator_name': 'Executor',
        'create_datetime': 'Created time',
    }
    export_serializer_class = ScriptTaskExecutionSerializer

    @action(detail=True, methods=['get'], url_path='host_outputs')
    def host_outputs(self, request, pk=None):
        execution = self.get_object()
        batch_id = None
        host_details_from_result = []
        if execution.result and isinstance(execution.result, dict):
            batch_id = execution.result.get('batch_id')
            host_details_from_result = execution.result.get('hosts_detail') or []

        is_lazy = str(request.query_params.get('lazy', '0')) in ('1', 'true', 'yes')
        keyword = (request.query_params.get('keyword') or '').strip().lower()
        status_filter = request.query_params.get('host_status') or request.query_params.get('status') or ''
        page = max(1, int(request.query_params.get('page', 1) or 1))
        page_size = int(request.query_params.get('page_size', 20) or 20)
        page_size = max(1, min(page_size, 500))

        ops_execs: list = []
        if batch_id:
            from taurus.models import OpsExecution
            from django.db.models import Q
            oids = [h.get('ops_execution_id') for h in host_details_from_result if h.get('ops_execution_id')]
            qs = OpsExecution.objects.filter(
                Q(batch_id=batch_id) | Q(execution_id__in=oids)
            ).select_related('host').order_by('-create_datetime')
            for oe in qs:
                stdout = ''
                stderr = ''
                if not is_lazy:
                    output_buffer = oe.output_buffer or []
                    stdout_lines: list = []
                    stderr_lines: list = []
                    for item in output_buffer:
                        if not isinstance(item, dict):
                            continue
                        so = item.get('stdout')
                        se = item.get('stderr')
                        if so:
                            stdout_lines.append(so if isinstance(so, str) else str(so))
                        if se:
                            stderr_lines.append(se if isinstance(se, str) else str(se))
                        data = item.get('data') or item.get('text')
                        if isinstance(data, str) and data:
                            stream = item.get('stream', 'stdout')
                            if stream in ('stderr', 'err'):
                                stderr_lines.append(data)
                            else:
                                stdout_lines.append(data)
                    stdout = ''.join(stdout_lines)
                    stderr = ''.join(stderr_lines)
                ops_execs.append({
                    'ops_execution_id': oe.execution_id,
                    'host_uuid': str(oe.host.host_uuid) if oe.host_id else '',
                    'host_ip': oe.host.host_ip if oe.host_id else '',
                    'host_name': oe.host.host_name if oe.host_id else '',
                    'status': oe.status,
                    'status_display': dict(OpsExecution.STATUS_CHOICES).get(oe.status, str(oe.status)),
                    'exit_code': oe.exit_code,
                    'error_message': oe.error_message or '',
                    'stdout': stdout,
                    'stderr': stderr,
                    'started_at': oe.started_at.isoformat() if oe.started_at else None,
                    'finished_at': oe.finished_at.isoformat() if oe.finished_at else None,
                })

        ops_map = {o['ops_execution_id']: o for o in ops_execs}
        merged: list = list(ops_execs)
        for hd in host_details_from_result:
            oid = hd.get('ops_execution_id')
            if oid and oid in ops_map:
                continue
            host_uuid = hd.get('host_uuid') or ''
            if host_uuid and any(m.get('host_uuid') == host_uuid for m in merged):
                continue
            status_display = 'Failed' if hd.get('status') == 'fail' else 'Submitted'
            merged.append({
                'ops_execution_id': oid or '',
                'host_uuid': host_uuid,
                'host_ip': hd.get('host_ip') or '',
                'host_name': hd.get('host_name') or '',
                'status': -1,
                'status_display': status_display,
                'exit_code': None,
                'error_message': hd.get('error') or '',
                'stdout': '',
                'stderr': hd.get('error') or '',
                'started_at': None,
                'finished_at': None,
            })

        def _status_category(m):
            s = m.get('status')
            sd = m.get('status_display')
            if s == 2:
                return 'success'
            if s in (3, 4) or sd == 'Failed':
                return 'failed'
            if s in (0, 1):
                return 'running'
            return 'pending'

        if keyword:
            merged = [
                m for m in merged
                if keyword in (m.get('host_ip') or '').lower()
                or keyword in (m.get('host_name') or '').lower()
                or keyword in (m.get('host_uuid') or '').lower()
            ]
        if status_filter:
            merged = [m for m in merged if _status_category(m) == status_filter]

        success_hosts = 0
        failed_hosts = 0
        running_hosts = 0
        pending_hosts = 0
        for m in merged:
            cat = _status_category(m)
            if cat == 'success':
                success_hosts += 1
            elif cat == 'failed':
                failed_hosts += 1
            elif cat == 'running':
                running_hosts += 1
            else:
                pending_hosts += 1

        total = len(merged)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        start = (page - 1) * page_size
        end = start + page_size
        page_items = merged[start:end]

        return DetailResponse(data={
            'task': {'id': execution.task_id, 'name': execution.task.name if execution.task_id else ''},
            'host_outputs': page_items,
            'summary': {
                'total': total,
                'success': success_hosts,
                'failed': failed_hosts,
                'running': running_hosts,
                'pending': pending_hosts,
            },
            'page': {
                'current': page,
                'size': page_size,
                'total': total,
                'total_pages': total_pages,
            },
            'error_message': execution.error_message or '',
        }, msg='Fetched successfully')

    @action(detail=True, methods=['get'], url_path='host_output_detail')
    def host_output_detail(self, request, pk=None):
        execution = self.get_object()
        ops_execution_id = request.query_params.get('ops_execution_id') or ''
        host_uuid = (request.query_params.get('host_uuid') or '').strip()
        host_ip = (request.query_params.get('host_ip') or '').strip()

        from taurus.models import OpsExecution

        oe = None
        if ops_execution_id:
            oe = OpsExecution.objects.filter(execution_id=ops_execution_id).select_related('host').first()
        if oe is None and host_uuid:
            batch_id = execution.result.get('batch_id') if isinstance(execution.result, dict) else None
            if batch_id:
                oe = OpsExecution.objects.filter(
                    batch_id=batch_id, host__host_uuid=host_uuid
                ).select_related('host').first()
        if oe is None and host_ip:
            batch_id = execution.result.get('batch_id') if isinstance(execution.result, dict) else None
            if batch_id:
                oe = OpsExecution.objects.filter(
                    batch_id=batch_id, host__host_ip=host_ip
                ).select_related('host').first()

        if oe is None:
            host_details_from_result = (execution.result or {}).get('hosts_detail') or [] if isinstance(execution.result, dict) else []
            match = None
            for hd in host_details_from_result:
                if ops_execution_id and hd.get('ops_execution_id') == ops_execution_id:
                    match = hd
                    break
                if host_uuid and str(hd.get('host_uuid', '')) == host_uuid:
                    match = hd
                    break
                if host_ip and hd.get('host_ip') == host_ip:
                    match = hd
                    break
            if match:
                return DetailResponse(data={
                    'ops_execution_id': match.get('ops_execution_id') or '',
                    'host_uuid': match.get('host_uuid') or '',
                    'host_ip': match.get('host_ip') or '',
                    'host_name': match.get('host_name') or '',
                    'status': -1,
                    'status_display': 'Failed' if match.get('status') == 'fail' else 'Submitted',
                    'exit_code': None,
                    'error_message': match.get('error') or '',
                    'stdout': '',
                    'stderr': match.get('error') or '',
                    'started_at': None,
                    'finished_at': None,
                }, msg='Fetched successfully')
            return ErrorResponse(msg='No execution record found for this host')

        output_buffer = oe.output_buffer or []
        stdout_lines: list = []
        stderr_lines: list = []
        for item in output_buffer:
            if not isinstance(item, dict):
                continue
            so = item.get('stdout')
            se = item.get('stderr')
            if so:
                stdout_lines.append(so if isinstance(so, str) else str(so))
            if se:
                stderr_lines.append(se if isinstance(se, str) else str(se))
            data = item.get('data') or item.get('text')
            if isinstance(data, str) and data:
                stream = item.get('stream', 'stdout')
                if stream in ('stderr', 'err'):
                    stderr_lines.append(data)
                else:
                    stdout_lines.append(data)
        return DetailResponse(data={
            'ops_execution_id': oe.execution_id,
            'host_uuid': str(oe.host.host_uuid) if oe.host_id else '',
            'host_ip': oe.host.host_ip if oe.host_id else '',
            'host_name': oe.host.host_name if oe.host_id else '',
            'status': oe.status,
            'status_display': dict(OpsExecution.STATUS_CHOICES).get(oe.status, str(oe.status)),
            'exit_code': oe.exit_code,
            'error_message': oe.error_message or '',
            'stdout': ''.join(stdout_lines),
            'stderr': ''.join(stderr_lines),
            'started_at': oe.started_at.isoformat() if oe.started_at else None,
            'finished_at': oe.finished_at.isoformat() if oe.finished_at else None,
        }, msg='Fetched successfully')


from django.utils.decorators import method_decorator

# ============================================================================
# Script 审批 / 审计 / 检查 / 分享  ViewSets — Thin Wrapper (EE 物理迁移到 taurus_ee)
# 这里保留类名与原实现一致（继承 EE ViewSet），EditionGate 在 EE 类已装饰，此处再 double-check
# ============================================================================
from taurus.editions import require_feature  # noqa: F811 （前面已经 import，重复不影响）
from taurus.editions.features import (
    F_SCRIPT_AUDIT_LOG,
    F_SCRIPT_APPROVAL_FLOW,
    F_SCRIPT_SECURITY_CHECK,
    F_SCRIPT_SHARING,
)
try:
    from taurus_ee.views.script_audit_view import ScriptAuditViewSet as _EEScriptAuditViewSet
    from taurus_ee.views.script_approval_views import (
        ScriptApproveViewSet as _EEScriptApproveViewSet,
        ScriptApprovalRuleViewSet as _EEScriptApprovalRuleViewSet,
        ScriptApprovalRuleNodeViewSet as _EEScriptApprovalRuleNodeViewSet,
        ScriptApprovalInstanceViewSet as _EEScriptApprovalInstanceViewSet,
    )
    from taurus_ee.views.script_check_view import ScriptCheckRuleViewSet as _EEScriptCheckRuleViewSet
    from taurus_ee.views.share_permission_view import (
        SharePermissionDefViewSet as _EESharePermissionDefViewSet,
        ShareLinkViewSet as _EEShareLinkViewSet,
    )
    from taurus_ee.services.script_approval_engine import (
        ApprovalRuleMatcher as _EEApprovalRuleMatcher,
        ApprovalFlowEngine as _EEApprovalFlowEngine,
    )
    _SCRIPT_EE_OK = True
except ImportError:
    from taurus.ee_fallback import _EEFallbackViewSet as _FBVS
    class _EEScriptAuditViewSet(_FBVS): pass
    class _EEScriptApproveViewSet(_FBVS): pass
    class _EEScriptApprovalRuleViewSet(_FBVS): pass
    class _EEScriptApprovalRuleNodeViewSet(_FBVS): pass
    class _EEScriptApprovalInstanceViewSet(_FBVS): pass
    class _EEScriptCheckRuleViewSet(_FBVS): pass
    class _EESharePermissionDefViewSet(_FBVS): pass
    class _EEShareLinkViewSet(_FBVS): pass
    class _EEApprovalRuleMatcher: pass
    class _EEApprovalFlowEngine: pass
    _SCRIPT_EE_OK = False


class ApprovalRuleMatcher(_EEApprovalRuleMatcher):
    """(Thin Wrapper, EE 实现在 taurus_ee.services.script_approval_engine)"""
    pass


class ApprovalFlowEngine(_EEApprovalFlowEngine):
    """(Thin Wrapper, EE 实现在 taurus_ee.services.script_approval_engine)"""
    pass


@method_decorator(require_feature(F_SCRIPT_AUDIT_LOG), name='dispatch')
class ScriptAuditViewSet(_EEScriptAuditViewSet):
    """Script audit ViewSet Thin Wrapper — 保持旧类名，让 taurus/urls.py router 不改动。"""
    pass


@method_decorator(require_feature(F_SCRIPT_APPROVAL_FLOW), name='dispatch')
class ScriptApproveViewSet(_EEScriptApproveViewSet):
    """Script approval Thin Wrapper。"""
    pass


@method_decorator(require_feature(F_SCRIPT_APPROVAL_FLOW), name='dispatch')
class ScriptApprovalRuleViewSet(_EEScriptApprovalRuleViewSet):
    pass


@method_decorator(require_feature(F_SCRIPT_APPROVAL_FLOW), name='dispatch')
class ScriptApprovalRuleNodeViewSet(_EEScriptApprovalRuleNodeViewSet):
    pass


@method_decorator(require_feature(F_SCRIPT_APPROVAL_FLOW), name='dispatch')
class ScriptApprovalInstanceViewSet(_EEScriptApprovalInstanceViewSet):
    pass


@method_decorator(require_feature(F_SCRIPT_SECURITY_CHECK), name='dispatch')
class ScriptCheckRuleViewSet(_EEScriptCheckRuleViewSet):
    pass


@method_decorator(require_feature(F_SCRIPT_SHARING), name='dispatch')
class SharePermissionDefViewSet(_EESharePermissionDefViewSet):
    pass


@method_decorator(require_feature(F_SCRIPT_SHARING), name='dispatch')
class ShareLinkViewSet(_EEShareLinkViewSet):
    pass


# ========================== Workflow审核规则(规则驱动审核) ==========================
# （M2.2 Thin Wrapper 已在文件前面：WorkflowApproveViewSet / WorkflowApprovalRuleViewSet /
#   WorkflowApprovalRuleNodeViewSet / WorkflowApprovalInstanceViewSet + WorkflowApprovalRuleMatcher /
#   WorkflowApprovalFlowEngine。原实现体已迁入 taurus_ee.views.workflow_approval_views /
#   taurus_ee.services.workflow_approval_engine。此处保留注释占位，避免意外重复定义。）

    @classmethod
    def match(cls, workflow, submitter):
        from taurus.models import WorkflowApprovalRule

        rules = WorkflowApprovalRule.objects.filter(is_active=True).order_by('priority')
        for rule in rules:
            if cls._check_rule(rule, workflow, submitter):
                return rule
        return None

    @classmethod
    def _check_rule(cls, rule, workflow, submitter):
        condition_groups = rule.condition_groups or []
        if not condition_groups:
            return True

        for group in condition_groups:
            if cls._check_condition_group(group, workflow, submitter):
                return True
        return False

    @classmethod
    def _check_condition_group(cls, group, workflow, submitter):
        if 'category_ids' in group and group['category_ids']:
            all_category_ids = cls._get_all_child_category_ids(group['category_ids'])
            if workflow.category_id not in all_category_ids:
                return False

        if 'workflow_modes' in group and group['workflow_modes']:
            if workflow.workflow_mode not in group['workflow_modes']:
                return False

        if 'risk_levels' in group and group['risk_levels']:
            risk_level, _ = WorkflowViewSet._evaluate_workflow_risk(workflow)
            if risk_level not in group['risk_levels']:
                return False

        if 'min_risk_points' in group and group['min_risk_points']:
            _, risk_points = WorkflowViewSet._evaluate_workflow_risk(workflow)
            if len(risk_points) < group['min_risk_points']:
                return False

        if 'auth_types' in group and group['auth_types']:
            if workflow.auth_type not in group['auth_types']:
                return False

        if 'submitter_roles' in group and group['submitter_roles']:
            if not hasattr(submitter, 'roles'):
                return False
            user_roles = set(submitter.roles.values_list('code', flat=True))
            if not user_roles & set(group['submitter_roles']):
                return False

        if 'workflow_ids' in group and group['workflow_ids']:
            if workflow.id not in group['workflow_ids']:
                return False

        if 'script_ids' in group and group['script_ids']:
            script_ids_required = set(int(x) for x in group['script_ids'])
            step_template_ids = set(
                workflow.steps.values_list('template_id', flat=True)
            ) if hasattr(workflow, 'steps') else set()
            if not (script_ids_required & step_template_ids):
                return False

        if 'tags' in group and group['tags']:
            tag_list = [str(t).strip().lower() for t in group['tags'] if str(t).strip()]
            if tag_list:
                wf_tags = set()
                if getattr(workflow, 'tags', None):
                    for t in str(workflow.tags).split(','):
                        if t.strip():
                            wf_tags.add(t.strip().lower())
                if getattr(workflow, 'name', None):
                    wf_tags.add(str(workflow.name).lower())
                if getattr(workflow, 'description', None):
                    wf_tags.add(str(workflow.description).lower())
                step_names = list(
                    workflow.steps.values_list('step_name', 'template__template_name')
                ) if hasattr(workflow, 'steps') else []
                for step_name, tpl_name in step_names:
                    if step_name:
                        wf_tags.add(str(step_name).lower())
                    if tpl_name:
                        wf_tags.add(str(tpl_name).lower())
                hit = False
                for t in tag_list:
                    for source in wf_tags:
                        if t in source:
                            hit = True
                            break
                    if hit:
                        break
                if not hit:
                    return False

        return True

    @classmethod
    def _get_all_child_category_ids(cls, parent_ids):
        from taurus.models import WorkflowCategory
        result = set(parent_ids)
        categories = list(WorkflowCategory.objects.all().values('id', 'parent_id'))
        changed = True
        while changed:
            changed = False
            for cat in categories:
                if cat['parent_id'] in result and cat['id'] not in result:
                    result.add(cat['id'])
                    changed = True
        return list(result)


# 【M2.2 占位】原 WorkflowApprovalFlowEngine / WorkflowApprovalRuleMatcher 完整实现
# （约~740 行）已经迁移至 taurus_ee.services.workflow_approval_engine，前面已经通过
# Thin Wrapper 暴露（taurus.views.WorkflowApprovalFlowEngine / RuleMatcher）。
# 此处避免重复定义覆盖前面的 Thin Wrapper。


# 【M2.2 占位】原 WorkflowApprovalRuleViewSet / WorkflowApprovalRuleNodeViewSet /
# WorkflowApprovalInstanceViewSet 3 个 ViewSet 已迁入 taurus_ee.views.workflow_approval_views，
# 在本文件前面已经以 Thin Wrapper 形式暴露（保持 basename/path 不变），此处不再重复定义。

# 【M2.2 占位】原 WorkflowApprovalRuleViewSet / WorkflowApprovalRuleNodeViewSet /
# WorkflowApprovalInstanceViewSet 3 个 ViewSet 已迁入 taurus_ee.views.workflow_approval_views，
# 在本文件前面已经以 Thin Wrapper 形式暴露（保持 basename/path 不变），此处不再重复定义。
# ============================================================
# 分享 - PermissionDictionary / link
# ============================================================
# （Thin Wrapper 已在文件前面定义：SharePermissionDefViewSet / ShareLinkViewSet，
# 这里不再重复定义，避免覆盖前面的 Thin Wrapper 带 Feature Gate。）


# ---------------------------------------------------------------------------
# [M2.3 Wrapper] TaskCenterViewSet — 聚合 Schedule × ScriptTask × WorkflowApproval
# 待办/失败告警/激活分享 — 本身就是 EE 统一中心，这里在 CE 模式对整个 ViewSet 拦
# F_SCRIPT_TASK_UNIFIED。实现在这里保留原地（无独立 class 迁移），装饰器方式最省。
# ---------------------------------------------------------------------------
from django.utils.decorators import method_decorator as _tc_method_decorator
from taurus.editions.loader import require_feature as _tc_require_feature
from taurus.editions.features import F_SCRIPT_TASK_UNIFIED as _F_TC_UNIFIED


@_tc_method_decorator(_tc_require_feature(_F_TC_UNIFIED), name='dispatch')
class TaskCenterViewSet(viewsets.ViewSet):
    """任务中心:aggregate展示ScriptScheduled task + 定时Workflow（EE 专属，统一视图）"""

    SCHEDULE_TYPE_MAP = {
        'cron': 'Cron expression',
        'interval': 'Fixed interval',
        'once': 'One-time',
    }

    EXEC_RESULT_MAP = {
        'success': 'Success',
        'fail': 'Failed',
        'running': 'Running',
    }

    def list(self, request):
        user = request.user
        is_superuser = getattr(user, 'is_superuser', False)
        item_type = request.query_params.get('type')
        keyword = request.query_params.get('keyword', '').strip()
        status_filter = request.query_params.get('status')
        ordering = request.query_params.get('ordering', '-create_datetime')
        page = int(request.query_params.get('page', 1))
        limit = int(request.query_params.get('limit', 20))

        items = []

        # ---------- 1. ScriptScheduled task ----------
        if not item_type or item_type == 'script_task':
            st_qs = ScriptTask.objects.select_related('script', 'creator').all()
            if not is_superuser:
                st_qs = st_qs.filter(creator=user)
            if keyword:
                st_qs = st_qs.filter(
                    models.Q(name__icontains=keyword)
                    | models.Q(description__icontains=keyword)
                    | models.Q(cron_expression__icontains=keyword)
                )
            if status_filter is not None:
                st_qs = st_qs.filter(enabled=bool(int(status_filter)))

            for st in st_qs:
                items.append({
                    'item_type': 'script_task',
                    'id': st.id,
                    'script_id': st.script_id,
                    'name': st.name,
                    'description': st.description,
                    'target_name': st.script.name if st.script else '-',
                    'schedule_type': st.schedule_type,
                    'schedule_type_display': st.schedule_type_display,
                    'cron_expression': st.cron_expression,
                    'interval_seconds': st.interval_seconds,
                    'run_once_at': st.run_once_at,
                    'status': 1 if st.enabled else 0,
                    'status_display': 'Running' if st.enabled else 'Paused',
                    'last_exec_time': st.last_exec_time,
                    'next_exec_time': st.next_exec_time,
                    'last_exec_result': st.last_exec_result,
                    'last_exec_result_display': st.get_last_exec_result_display(),
                    'exec_count': st.exec_count,
                    'creator_name': st.creator.username if st.creator else '-',
                    'create_datetime': st.create_datetime,
                })

        # ---------- 2. 定时Workflow ----------
        if not item_type or item_type == 'workflow':
            wf_qs = Workflow.objects.filter(has_schedule=True).select_related('creator').all()
            if not is_superuser:
                wf_qs = wf_qs.filter(creator=user)
            if keyword:
                wf_qs = wf_qs.filter(
                    models.Q(name__icontains=keyword)
                    | models.Q(description__icontains=keyword)
                    | models.Q(cron_expression__icontains=keyword)
                )
            if status_filter is not None:
                wf_qs = wf_qs.filter(schedule_enabled=bool(int(status_filter)))

            for wf in wf_qs:
                items.append({
                    'item_type': 'workflow',
                    'id': wf.id,
                    'name': wf.name,
                    'description': wf.description,
                    'target_name': wf.name,
                    'schedule_type': wf.schedule_type or '',
                    'schedule_type_display': self.SCHEDULE_TYPE_MAP.get(wf.schedule_type, wf.schedule_type or '-'),
                    'cron_expression': wf.cron_expression,
                    'interval_seconds': wf.interval_seconds,
                    'run_once_at': wf.run_once_at,
                    'status': 1 if wf.schedule_enabled else 0,
                    'status_display': 'Running' if wf.schedule_enabled else 'Paused',
                    'last_exec_time': wf.last_exec_time,
                    'next_exec_time': wf.next_exec_time,
                    'last_exec_result': wf.last_exec_result,
                    'last_exec_result_display': self.EXEC_RESULT_MAP.get(wf.last_exec_result, wf.last_exec_result),
                    'exec_count': wf.exec_count,
                    'creator_name': wf.creator.username if wf.creator else '-',
                    'create_datetime': wf.create_datetime,
                })

        # ---------- 3. UnifiedOrder ----------
        reverse = ordering.startswith('-')
        sort_key = ordering.lstrip('-')
        valid_keys = {'create_datetime', 'last_exec_time', 'next_exec_time', 'exec_count', 'name'}
        if sort_key not in valid_keys:
            sort_key = 'create_datetime'
        items.sort(key=lambda x: (x.get(sort_key) or ''), reverse=reverse)

        # ---------- 4. 手动Pagination ----------
        total = len(items)
        start = (page - 1) * limit
        end = start + limit
        paged_items = items[start:end]

        serializer = TaskCenterItemSerializer(paged_items, many=True)
        return SuccessResponse(data={
            'results': serializer.data,
            'count': total,
            'page': page,
            'limit': limit,
        }, msg='Fetched successfully')


class ContactLeadViewSet(CustomModelViewSet):
    """Contact leads from taurus-portal contact form. (M2.5 Mixed Gate: create=CE，其他=EE)

    Security design:
    - POST (create): AllowAny, anonymous clients from portal may submit (CE保留)
    - Other methods (list/retrieve/update/partial_update/destroy): require EE (F_CONTACT_LEAD_PORTAL) + IsAdminUser
    - IP-level throttling on create (60/min/IP) to mitigate spam
    """
    # M2.5 Mixed Gate: dispatch 显式重写 — POST 无 pk (=create) CE 放行，其他 EE Gate
    def dispatch(self, request, *args, **kwargs):
        method = getattr(request, 'method', '').upper()
        pk = kwargs.get('pk') if isinstance(kwargs, dict) else None
        if method == 'POST' and pk is None and not args:
            return super().dispatch(request, *args, **kwargs)
        @_cl_require_feature(_F_CL_GATE)
        def _inner(_req, *_a, **_kw):
            return super().dispatch(_req, *_a, **_kw)
        return _inner(request, *args, **kwargs)

    from rest_framework.permissions import IsAdminUser
    from rest_framework.throttling import SimpleRateThrottle

    # lazy imported classes above cannot directly decorate permission_classes
    # attribute; assign via `permission_classes` and `throttle_scope` + Django
    # REST Framework DEFAULT_THROTTLE_RATES configuration (fallback scopes).
    # To avoid env-dependent DEFAULT config, we re-define rate explicitly:

    class _ContactLeadRateThrottle(SimpleRateThrottle):
        scope = 'contact_lead_submit'
        THROTTLE_RATES_OVERRIDE = {'contact_lead_submit': '60/min'}

        def get_cache_key(self, request, view):
            ident = self.get_ident(request)
            return self.cache_format % {'scope': self.scope, 'ident': ident}

        def allow_request(self, request, view):
            # Prefer the hard-coded override rate above over the project-wide
            # DEFAULT_THROTTLE_RATES. This way the 60/min cap is reliable even
            # when the project settings lack an explicit `contact_lead_submit`
            # scope entry.
            from rest_framework.throttling import SimpleRateThrottle as _SRT
            original_rates = getattr(_SRT, 'THROTTLE_RATES', None)
            merged = dict(original_rates or {})
            merged.update(self.THROTTLE_RATES_OVERRIDE)
            # Monkey-patch for just this call (process-level, acceptable).
            _SRT.THROTTLE_RATES = merged
            self.THROTTLE_RATES = merged
            try:
                return _SRT.allow_request(self, request, view)
            finally:
                if original_rates is None:
                    try:
                        del _SRT.THROTTLE_RATES
                    except AttributeError:
                        pass
                else:
                    _SRT.THROTTLE_RATES = original_rates

    serializer_class = None  # resolved per-action below

    def get_permissions(self):
        from rest_framework.permissions import AllowAny, IsAdminUser
        if self.action == 'create':
            return [AllowAny()]
        return [IsAdminUser()]

    def get_throttles(self):
        if self.action == 'create':
            return [self._ContactLeadRateThrottle()]
        return super().get_throttles()

    def get_serializer_class(self):
        from taurus.serializers import ContactLeadSerializer
        return ContactLeadSerializer

    def get_queryset(self):
        from taurus.models import ContactLead
        return ContactLead.objects.all().order_by('-create_datetime')

    def perform_create(self, serializer):
        # capture client identity transparently; do not trust caller-supplied
        # ip/source/user_agent (read-only fields already declared on serializer)
        from taurus.utils.request_util import get_request_ip  # dvadmin util (optional)
        meta = self.request.META
        try:
            ip = get_request_ip(self.request) if callable(get_request_ip) else None
        except Exception:  # noqa: BLE001
            ip = None
        if not ip:
            xff = meta.get('HTTP_X_FORWARDED_FOR', '') or ''
            ip = (xff.split(',')[0].strip() if xff else meta.get('REMOTE_ADDR')) or None
        ua = (meta.get('HTTP_USER_AGENT') or '')[:512]
        serializer.save(
            source='portal',
            ip=ip,
            user_agent=ua,
        )

# ===========================================================
# [M2.5 扩展中心 5 空壳 ViewSet — 占位 + EE Feature Gate]
# 目前 urls.py 暂未注册；如将来 EE frontend 挂载它们，basename 使用：
#   knowledge-base / inspection-center / tools-center / backup-restore-center / download-center
# ===========================================================
from django.utils.decorators import method_decorator as _ext_method_decorator  # noqa: E402
from taurus.editions.loader import require_feature as _ext_require_feature  # noqa: E402
from taurus.editions.features import (  # noqa: E402
    F_KNOWLEDGE_BASE as _F_KB,
    F_INSPECTION_CENTER as _F_IC,
    F_TOOLS_CENTER as _F_TC,
    F_BACKUP_RESTORE as _F_BR,
    F_DOWNLOAD_CENTER as _F_DC,
)
try:
    from taurus_ee.views.log_ext_views import (  # noqa: E402
        _EEKnowledgeBaseViewSet,
        _EEInspectionCenterViewSet,
        _EEToolsCenterViewSet,
        _EEBackupRestoreCenterViewSet,
        _EEDownloadCenterViewSet,
    )
    _EXT_EE_OK = True
except ImportError:
    from taurus.ee_fallback import _EEFallbackViewSet as _FBVS  # noqa: E402
    class _EEKnowledgeBaseViewSet(_FBVS): pass
    class _EEInspectionCenterViewSet(_FBVS): pass
    class _EEToolsCenterViewSet(_FBVS): pass
    class _EEBackupRestoreCenterViewSet(_FBVS): pass
    class _EEDownloadCenterViewSet(_FBVS): pass
    _EXT_EE_OK = False

@_ext_method_decorator(_ext_require_feature(_F_KB), name='dispatch')
class KnowledgeBaseViewSet(_EEKnowledgeBaseViewSet):
    """知识库（Thin Wrapper, M2.5 EE 空壳）"""
    pass


@_ext_method_decorator(_ext_require_feature(_F_IC), name='dispatch')
class InspectionCenterViewSet(_EEInspectionCenterViewSet):
    """巡检中心（Thin Wrapper, M2.5 EE 空壳）"""
    pass


@_ext_method_decorator(_ext_require_feature(_F_TC), name='dispatch')
class ToolsCenterViewSet(_EEToolsCenterViewSet):
    """工具中心（Thin Wrapper, M2.5 EE 空壳）"""
    pass


@_ext_method_decorator(_ext_require_feature(_F_BR), name='dispatch')
class BackupRestoreCenterViewSet(_EEBackupRestoreCenterViewSet):
    """备份恢复中心（Thin Wrapper, M2.5 EE 空壳）"""
    pass


@_ext_method_decorator(_ext_require_feature(_F_DC), name='dispatch')
class DownloadCenterViewSet(_EEDownloadCenterViewSet):
    """客户端打包下载中心（Thin Wrapper, M2.5 EE 空壳）"""
    pass


# =============================================================================
# 通用：轻量用户选择器接口（M3 工作流审批人 / 通知人 picker 专用）
# dvadmin 自带 /api/system/user/ list 被 @require_viewset_perms(user:list) 锁死，
# 普通业务用户没有 user:list 权限会 401。该接口只暴露选择器需要的 4 个公开字段，
# 登录态即可访问，不要求额外权限。
# =============================================================================
class LightweightUserOptionsView(APIView):
    """轻量用户选择列表（UserPicker 专用）。权限：登录即可；按姓名/用户名模糊搜索。

    返回格式保持 dvadmin SuccessResponse 包壳，字段扁平、不包含邮箱/手机等隐私字段。
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from dvadmin.system.models import Users as _Users
        from dvadmin.utils.json_response import SuccessResponse
        from django.db.models import Q as _Q

        limit_raw = request.query_params.get('limit') or request.query_params.get('size') or 200
        try:
            limit = max(1, min(1000, int(limit_raw)))
        except (TypeError, ValueError):
            limit = 200
        search = (request.query_params.get('search') or request.query_params.get('keyword') or '').strip()
        dept_id = request.query_params.get('dept')
        only_active = (request.query_params.get('only_active') or '1') not in ('0', 'false', 'False')

        qs = _Users.objects.all()
        if only_active:
            qs = qs.filter(is_active=True)
        if search:
            qs = qs.filter(_Q(name__icontains=search) | _Q(username__icontains=search))
        if dept_id and str(dept_id).isdigit():
            qs = qs.filter(dept_id=int(dept_id))
        # CE 下配合 max_users 配额；但 picker 一般不会刷满全量，默认倒序取最近注册活跃账号
        qs = qs.order_by('-is_active', '-create_datetime')[:limit]
        items = [
            {
                'id': u.id,
                'username': u.username,
                'name': u.name,
                'avatar': u.avatar or '',
                'dept_id': u.dept_id,
                'is_active': bool(u.is_active),
            }
            for u in qs
        ]
        return SuccessResponse(
            data=items,
            msg='success',
            page=1,
            limit=len(items) or 1,
            total=len(items),
        )