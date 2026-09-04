from typing import Any

from django.utils import timezone
from rest_framework import serializers
from dvadmin.utils.serializers import CustomModelSerializer
from dvadmin.utils.validator import CustomUniqueValidator
from .models import (
    Workflow, WorkflowCategory, WorkflowStep, WorkflowExecution, WorkflowStepExecution,
    Host, Schedule, ScheduleExecution, RegistrationToken, HostHeartbeat,
    HeartbeatServer, ProgramInstallConfig, ProgramInstallPolicy, ProgramCommand,
    ManagedProgram, ProgramInstallTemplate, ProgramHostBinding,
    OpsExecution, ScriptCategory, Script, ScriptVersion, ScriptPermission, ScriptTask,
    ScriptTaskExecution,
)


class WorkflowStepSerializer(CustomModelSerializer):
    template_name = serializers.CharField(source='template.template_name', read_only=True)
    script_type = serializers.CharField(source='template.script_type', read_only=True)

    class Meta:
        model = WorkflowStep
        fields = [
            'id', 'workflow', 'template', 'template_name', 'script_type',
            'step_name', 'step_order', 'step_envs', 'step_args',
            'on_failure', 'timeout', 'create_datetime', 'update_datetime'
        ]
        read_only_fields = ['create_datetime', 'update_datetime']


class WorkflowSerializer(CustomModelSerializer):
    steps = WorkflowStepSerializer(many=True, read_only=True)
    hosts_detail = serializers.SerializerMethodField()
    dag_published_version_id = serializers.PrimaryKeyRelatedField(
        source='dag_published_version', read_only=True, default=None,
    )
    category_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    auth_type_display = serializers.CharField(source='get_auth_type_display', read_only=True)
    creator_name = serializers.CharField(source='creator.username', read_only=True, default='')
    pending_approve_count = serializers.SerializerMethodField()
    share_summary = serializers.SerializerMethodField(read_only=True)
    current_perms = serializers.SerializerMethodField(read_only=True)
    hosts = serializers.PrimaryKeyRelatedField(
        many=True,
        required=False,
        allow_empty=True,
        queryset=Host.objects.all(),
        help_text="Workflow-level target hosts (can be empty in DAG orchestration mode, each node specifies its own target_hosts internally)",
    )

    class Meta:
        model = Workflow
        fields = [
            'id', 'name', 'description', 'category', 'category_name', 'hosts', 'hosts_detail',
            'global_envs', 'status', 'status_display', 'share',
            'auth_type', 'auth_type_display', 'need_audit', 'custom_approver_ids',
            'exec_count', 'last_exec_time',
            'pending_approve_count',
            'steps',
            'workflow_mode', 'graph_definition', 'graph_version',
            'dag_published_version', 'dag_published_version_id',
            'global_timeout_sec', 'fail_strategy',
            'has_schedule', 'schedule_type', 'cron_expression', 'interval_seconds',
            'run_once_at', 'schedule_enabled',
            'create_datetime', 'update_datetime', 'creator', 'creator_name', 'modifier',
            'share_summary', 'current_perms',
        ]
        read_only_fields = ['create_datetime', 'update_datetime', 'creator', 'modifier',
                            'status_display', 'auth_type_display', 'exec_count', 'last_exec_time',
                            'pending_approve_count']

    def get_hosts_detail(self, obj):
        hosts = obj.hosts.all()
        return [{'id': h.id, 'host_name': h.host_name, 'host_ip': h.host_ip} for h in hosts]

    def get_category_name(self, obj):
        return obj.category.name if obj.category else '-'

    def get_pending_approve_count(self, obj):
        old_count = obj.approvals.filter(status='pending').count()
        new_count = obj.approval_instances.filter(status__in=['pending', 'approving']).count()
        return old_count + new_count

    def get_share_summary(self, obj):
        return getattr(obj, '_share_summary', {
            'total': 0,
            'direct_count': 0,
            'link_count': 0,
            'subjects': [],
            'links': [],
        })

    def get_current_perms(self, obj):
        perms = getattr(obj, '_current_perms', None)
        if perms is None:
            return []
        if isinstance(perms, (list, tuple, set)):
            return sorted(list(perms))
        return []


class WorkflowListSerializer(CustomModelSerializer):
    steps_count = serializers.SerializerMethodField()
    hosts_count = serializers.SerializerMethodField()
    workflow_mode = serializers.CharField(read_only=True)
    dag_published_version_id = serializers.PrimaryKeyRelatedField(
        source='dag_published_version', read_only=True, default=None,
    )
    category_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    auth_type_display = serializers.CharField(source='get_auth_type_display', read_only=True)
    creator_name = serializers.CharField(source='creator.username', read_only=True, default='')
    pending_approve_count = serializers.SerializerMethodField()
    exec_count = serializers.SerializerMethodField()
    last_exec_time = serializers.SerializerMethodField()
    share_summary = serializers.SerializerMethodField(read_only=True)
    current_perms = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Workflow
        fields = [
            'id', 'name', 'description', 'category', 'category_name',
            'status', 'status_display', 'share',
            'auth_type', 'auth_type_display', 'need_audit', 'custom_approver_ids',
            'exec_count', 'last_exec_time', 'pending_approve_count',
            'workflow_mode', 'dag_published_version_id',
            'steps_count', 'hosts_count',
            'global_timeout_sec', 'fail_strategy',
            'has_schedule', 'schedule_type', 'schedule_enabled',
            'create_datetime', 'update_datetime', 'creator', 'creator_name', 'modifier',
            'share_summary', 'current_perms',
        ]

    def get_steps_count(self, obj):
        if obj.workflow_mode == 'dag' and obj.graph_definition:
            nodes = obj.graph_definition.get('nodes') or []
            return len(nodes)
        return obj.steps.count()

    def get_hosts_count(self, obj):
        return obj.hosts.count()

    def get_category_name(self, obj):
        return obj.category.name if obj.category else '-'

    def get_pending_approve_count(self, obj):
        old_count = obj.approvals.filter(status='pending').count()
        new_count = obj.approval_instances.filter(status__in=['pending', 'approving']).count()
        return old_count + new_count

    def get_exec_count(self, obj):
        """Display metric: based on real WorkflowExecution records (excluding dry run), consistent with execution records."""
        from taurus.models import WorkflowExecution
        actual = getattr(obj, '_actual_exec_count', None)
        if actual is not None:
            return int(actual)
        return int(WorkflowExecution.objects.filter(
            workflow_id=obj.id,
        ).exclude(trigger_type='dryrun').count())

    def get_last_exec_time(self, obj):
        """Display metric: based on WorkflowExecution max(start_time), consistent with execution records."""
        from taurus.models import WorkflowExecution
        actual = getattr(obj, '_actual_last_exec_time', None)
        if actual is not None:
            return actual
        latest = WorkflowExecution.objects.filter(
            workflow_id=obj.id,
        ).exclude(trigger_type='dryrun').order_by('-start_time').values_list('start_time', flat=True).first()
        return latest if latest is not None else obj.last_exec_time

    def get_share_summary(self, obj):
        return getattr(obj, '_share_summary', {
            'total': 0,
            'direct_count': 0,
            'link_count': 0,
            'subjects': [],
            'links': [],
        })

    def get_current_perms(self, obj):
        perms = getattr(obj, '_current_perms', None)
        if perms is None:
            return []
        if isinstance(perms, (list, tuple, set)):
            return sorted(list(perms))
        return []


class WorkflowExportSerializer(CustomModelSerializer):
    """Workflow export serializer: format all datetime fields to strings,
    to avoid dvadmin import_export_mixin errors when openpyxl writes float values.
    """
    category_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    auth_type_display = serializers.CharField(source='get_auth_type_display', read_only=True)
    creator_name = serializers.CharField(source='creator.username', read_only=True, default='')
    pending_approve_count = serializers.SerializerMethodField()
    steps_count = serializers.SerializerMethodField()
    exec_count = serializers.SerializerMethodField()
    last_exec_time = serializers.SerializerMethodField()
    create_datetime = serializers.SerializerMethodField()
    update_datetime = serializers.SerializerMethodField()

    class Meta:
        model = Workflow
        fields = [
            'name', 'category_name', 'share', 'need_audit',
            'status_display', 'pending_approve_count', 'steps_count',
            'creator_name', 'exec_count', 'last_exec_time',
            'auth_type_display', 'workflow_mode',
            'create_datetime', 'update_datetime',
        ]

    @staticmethod
    def _fmt_dt(v):
        if v is None:
            return ''
        if hasattr(v, 'strftime'):
            return v.strftime('%Y-%m-%d %H:%M:%S')
        return str(v)

    def get_category_name(self, obj):
        return obj.category.name if obj.category else '-'

    def get_pending_approve_count(self, obj):
        old_count = obj.approvals.filter(status='pending').count()
        new_count = obj.approval_instances.filter(status__in=['pending', 'approving']).count()
        return old_count + new_count

    def get_steps_count(self, obj):
        return obj.steps.count()

    def get_exec_count(self, obj):
        from taurus.models import WorkflowExecution
        actual = getattr(obj, '_actual_exec_count', None)
        if actual is not None:
            return int(actual)
        return int(WorkflowExecution.objects.filter(
            workflow_id=obj.id,
        ).exclude(trigger_type='dryrun').count())

    def get_last_exec_time(self, obj):
        from taurus.models import WorkflowExecution
        actual = getattr(obj, '_actual_last_exec_time', None)
        if actual is not None:
            return self._fmt_dt(actual)
        latest = WorkflowExecution.objects.filter(
            workflow_id=obj.id,
        ).exclude(trigger_type='dryrun').order_by('-start_time').values_list('start_time', flat=True).first()
        return self._fmt_dt(latest if latest is not None else obj.last_exec_time)

    def get_create_datetime(self, obj):
        return self._fmt_dt(obj.create_datetime)

    def get_update_datetime(self, obj):
        return self._fmt_dt(obj.update_datetime)


# ========================== Workflow Category Serializers ==========================

class WorkflowCategorySerializer(CustomModelSerializer):
    """Workflow category serializer"""
    workflow_count = serializers.SerializerMethodField()
    reviewer_ids = serializers.PrimaryKeyRelatedField(
        source='reviewers', many=True, read_only=True
    )
    reviewer_names = serializers.SerializerMethodField()

    class Meta:
        model = WorkflowCategory
        fields = ['id', 'name', 'parent', 'category_code', 'sort', 'is_system', 'remark',
                  'reviewer_ids', 'reviewer_names',
                  'workflow_count', 'create_datetime', 'update_datetime', 'creator', 'modifier']
        read_only_fields = ['create_datetime', 'update_datetime', 'creator', 'modifier']

    def get_workflow_count(self, obj):
        """Count workflows under this category and all its child categories"""
        from taurus.models import Workflow

        def _get_all_child_ids(parent_obj):
            if not parent_obj:
                return []
            ids = [parent_obj.id]
            children = WorkflowCategory.objects.filter(parent=parent_obj)
            for child in children:
                ids.extend(_get_all_child_ids(child))
            return ids

        category_ids = _get_all_child_ids(obj)
        return Workflow.objects.filter(category_id__in=category_ids).count()

    def get_reviewer_names(self, obj):
        names = []
        for u in (obj.reviewers.all() if obj.pk else []):
            names.append(getattr(u, 'username', '') or getattr(u, 'name', '') or str(u.id))
        return names


class WorkflowCategoryCreateSerializer(CustomModelSerializer):
    """Workflow category create serializer"""
    name = serializers.CharField(
        max_length=100,
        validators=[CustomUniqueValidator(
            queryset=WorkflowCategory.objects.all(),
            message="Category name already exists"
        )]
    )
    reviewers = serializers.PrimaryKeyRelatedField(
        many=True, required=False, allow_empty=True,
        queryset=__import__('django.contrib.auth', fromlist=['get_user_model']).get_user_model().objects.all()
    )

    class Meta:
        model = WorkflowCategory
        fields = ['name', 'parent', 'category_code', 'sort', 'remark', 'reviewers']


class WorkflowCategoryUpdateSerializer(CustomModelSerializer):
    """Workflow category update serializer"""
    reviewers = serializers.PrimaryKeyRelatedField(
        many=True, required=False, allow_empty=True,
        queryset=__import__('django.contrib.auth', fromlist=['get_user_model']).get_user_model().objects.all()
    )

    class Meta:
        model = WorkflowCategory
        fields = ['name', 'parent', 'category_code', 'sort', 'remark', 'reviewers']
        read_only_fields = ['id', 'is_system', 'create_datetime', 'creator']


class WorkflowExecutionListSerializer(CustomModelSerializer):
    workflow_name = serializers.CharField(source='workflow.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    trigger_type_display = serializers.CharField(source='get_trigger_type_display', read_only=True)
    creator_name = serializers.CharField(source='creator.username', read_only=True, default='-')
    duration = serializers.SerializerMethodField()

    class Meta:
        model = WorkflowExecution
        fields = [
            'id', 'workflow', 'workflow_name',
            'status', 'status_display',
            'trigger_type', 'trigger_type_display',
            'start_time', 'end_time', 'duration',
            'creator', 'creator_name',
            'create_datetime',
        ]

    def get_duration(self, obj) -> str | None:
        start = getattr(obj, 'start_time', None)
        end = getattr(obj, 'end_time', None)
        if start and end:
            delta = end - start
            total_seconds = int(delta.total_seconds())
            if total_seconds < 0:
                return None
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                return f'{hours}h{minutes}m{seconds}s'
            if minutes > 0:
                return f'{minutes}m{seconds}s'
            return f'{seconds}s'
        return None


class WorkflowExecutionSerializer(CustomModelSerializer):
    workflow_name = serializers.CharField(source='workflow.name', read_only=True)
    step_executions = serializers.SerializerMethodField()
    node_execution_groups = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    dag_version_detail = serializers.SerializerMethodField()
    trigger_type_display = serializers.CharField(source='get_trigger_type_display', read_only=True)
    is_dryrun = serializers.SerializerMethodField(method_name='calculate_is_dryrun')
    creator_name = serializers.CharField(source='creator.username', read_only=True, default='-')
    is_latest_dag_version = serializers.SerializerMethodField()
    dag_version_version = serializers.SerializerMethodField()
    latest_dag_version_version = serializers.SerializerMethodField()

    class Meta:
        model = WorkflowExecution
        fields = [
            'id', 'workflow', 'workflow_name', 'status', 'status_display',
            'start_time', 'end_time', 'context', 'error_message',
            'current_step', 'step_executions', 'node_execution_groups',
            'dag_version', 'dag_version_detail', 'fail_strategy', 'trigger_type', 'trigger_type_display',
            'trigger_params', 'is_dryrun', 'creator_name', 'creator',
            'create_datetime', 'update_datetime',
            'is_latest_dag_version', 'dag_version_version', 'latest_dag_version_version',
        ]
        read_only_fields = ['create_datetime', 'update_datetime']

    def calculate_is_dryrun(self, obj) -> bool:
        return str(getattr(obj, 'trigger_type', '')) == 'dryrun'

    def get_is_latest_dag_version(self, obj) -> bool:
        exec_vid = getattr(obj, 'dag_version_id', None)
        wf = getattr(obj, 'workflow', None)
        latest_vid = getattr(wf, 'dag_published_version_id', None) if wf is not None else None
        if exec_vid is None or latest_vid is None:
            return True
        return exec_vid == latest_vid

    def get_dag_version_version(self, obj) -> int | None:
        dag_ver = getattr(obj, 'dag_version', None)
        return getattr(dag_ver, 'version', None) if dag_ver is not None else None

    def get_latest_dag_version_version(self, obj) -> int | None:
        wf = getattr(obj, 'workflow', None)
        latest = getattr(wf, 'dag_published_version', None) if wf is not None else None
        return getattr(latest, 'version', None) if latest is not None else None

    @staticmethod
    def _topo_order_by_definition(definition: dict | None) -> dict[str, int]:
        """Perform BFS topological ordering on DAG definition, return {node_key: index} map, missing nodes placed at end."""
        if not definition or not isinstance(definition, dict):
            return {}
        nodes = definition.get('nodes') or []
        edges = definition.get('edges') or []
        key_list = [str(n['node_key']) for n in nodes if isinstance(n, dict) and n.get('node_key')]
        forward: dict[str, list[str]] = {k: [] for k in key_list}
        indeg: dict[str, int] = {k: 0 for k in key_list}
        for e in edges:
            if not isinstance(e, dict):
                continue
            src = (
                e.get('from') or e.get('from_key')
                or e.get('src') or e.get('source') or ''
            )
            dst = (
                e.get('to') or e.get('to_key')
                or e.get('dst') or e.get('target') or ''
            )
            src, dst = str(src), str(dst)
            if src not in indeg or dst not in indeg:
                continue
            forward[src].append(dst)
            indeg[dst] += 1
        order: list[str] = []
        q: list[str] = [k for k, d in indeg.items() if d == 0]
        _visited = set(q)
        while q:
            k = q.pop(0)
            order.append(k)
            for nb in forward.get(k, []):
                indeg[nb] -= 1
                if indeg[nb] == 0 and nb not in _visited:
                    _visited.add(nb)
                    q.append(nb)
        for k in key_list:
            if k not in _visited:
                order.append(k)
        tail = len(order)
        return {k: i for i, k in enumerate(order)}

    @staticmethod
    def _build_host_cache(rows: list[Any]) -> dict[str, Any]:
        """Batch pre-fetch all related Host objects, avoid serial N+1 queries.
        return {str(host_id_or_uuid): Host_instance}.

        Frontend selector / legacy code writing host_id may have 4 formats:
          (A) UUID string             → Query by Host.pk
          (B) Integer / numeric string → Query by Host.pk (int/bigint auto-increment)
          (C) host_uuid custom string  → Query by Host.host_uuid
          (D) Host object itself       → Direct pk/host_uuid bidirectional index
        """
        host_ids: set[str] = set()
        for r in rows:
            hid = getattr(r, "host_id", None)
            if not hid:
                continue
            s = str(hid)
            if s == "__NO_HOST__" or not s.strip():
                continue
            host_ids.add(s)
        cache: dict[str, Any] = {}
        if not host_ids:
            return cache
        try:
            from taurus.models import Host
            import uuid as _uuid

            # — split into 4 groups: uuid_pk / int_pk / host_uuid_str —
            uuid_pks: list = []
            int_pks: list[int] = []
            host_uuid_strs: list[str] = []
            for s in host_ids:
                # (A) First try UUID (pk)
                try:
                    uuid_pks.append(_uuid.UUID(s))
                    continue
                except (ValueError, AttributeError, TypeError):
                    pass
                # (B) Then try integer pk
                try:
                    if s.isdigit():
                        int_pks.append(int(s))
                        continue
                except (ValueError, AttributeError, TypeError):
                    pass
                # (C) Others: query by custom host_uuid string
                host_uuid_strs.append(s)

            _index_host = lambda h: None
            def _index_host(h):  # noqa: F811
                cache[str(h.pk)] = h
                hu = getattr(h, "host_uuid", None)
                if hu:
                    cache[str(hu)] = h
                # Compatibility: when host_id stores host name/ip, it won't match here but won't error

            # 1) UUID pk
            for h in Host.objects.filter(id__in=uuid_pks).iterator():
                _index_host(h)
            # 2) Integer pk (e.g. when user intercepts graph 17)
            if int_pks:
                for h in Host.objects.filter(id__in=int_pks).iterator():
                    _index_host(h)
            # 3) host_uuid string
            if host_uuid_strs:
                for h in Host.objects.filter(host_uuid__in=host_uuid_strs).iterator():
                    _index_host(h)
            # 4) Fallback: for unresolved ones, try matching by ip (extreme case where host_id stores an ip)
            unresolved = [s for s in host_ids if s not in cache]
            if unresolved:
                for h in Host.objects.filter(host_ip__in=unresolved).iterator():
                    cache[str(getattr(h, "host_ip", ""))] = h
                    _index_host(h)
        except Exception:
            pass
        return cache

    @staticmethod
    def _sort_node_rows(rows: list[Any], dag_ver_id: Any) -> list[Any]:
        """Sort WorkflowNodeExecution rows by temporal + topological stable ordering (consistent with old get_step_executions)."""
        topo_idx: dict[str, int] = {}
        if dag_ver_id is not None:
            try:
                from taurus.workflow.models import WorkflowDAGVersion
                ver = WorkflowDAGVersion.objects.filter(pk=dag_ver_id).values('definition').first()
                if ver:
                    topo_idx = WorkflowExecutionSerializer._topo_order_by_definition(ver.get('definition'))
            except Exception:
                topo_idx = {}
        tail = len(topo_idx) or 1_000_000
        import datetime as _dt
        _SENTINEL_EMPTY = _dt.datetime(2999, 12, 31, 23, 59, 59)

        def _is_datetime(v) -> bool:
            return v is not None and all(
                hasattr(v, a) for a in ('year', 'month', 'day', 'hour', 'minute', 'second')
            ) and callable(getattr(v, 'strftime', None))

        def _f(v):
            if _is_datetime(v):
                if getattr(v, 'tzinfo', None) is not None:
                    import calendar as _cal
                    try:
                        epoch = int(_cal.timegm(v.utctimetuple()))
                        return _dt.datetime.utcfromtimestamp(epoch).replace(microsecond=getattr(v, 'microsecond', 0))
                    except Exception:
                        pass
                return v
            return _SENTINEL_EMPTY

        sorted_rows = list(rows)
        sorted_rows.sort(key=lambda r: (
            _f(r.queued_at),
            _f(r.started_at),
            _f(r.finished_at),
            topo_idx.get(str(r.node_key), tail),
            str(r.node_key),
            -(r.attempt_no or 0),
        ))
        return sorted_rows

    def _collect_dag_rows(self, obj: Any) -> list[Any] | None:
        """Return ordered WorkflowNodeExecution rows for this execution instance in DAG mode; return None in non-DAG mode."""
        if getattr(obj, 'dag_version_id', None) is None:
            return None
        try:
            from taurus.workflow.models import WorkflowNodeExecution
            rows = list(WorkflowNodeExecution.objects.filter(execution_id=obj.pk))
        except Exception:
            return None
        return self._sort_node_rows(rows, getattr(obj, 'dag_version_id', None))

    def get_step_executions(self, obj):
        # DAG mode
        rows = self._collect_dag_rows(obj)
        if rows is not None:
            try:
                host_cache = self._build_host_cache(rows)
                ctx = {"host_cache": host_cache}
                return WorkflowNodeExecutionSerializer(rows, many=True, context=ctx).data
            except Exception:
                return []
        # Linear mode: legacy step_executions reverse relation
        try:
            step_execs = obj.step_executions.select_related('step__template').all()
            return WorkflowStepExecutionSerializer(step_execs, many=True).data
        except Exception:
            return []

    def get_node_execution_groups(self, obj):
        """**Core aggregation field for multi-host scenario**: group by node_key, each node returns:
          - status_counts: {pending, running, success, failed, skipped, cancelled} host counts
          - overall_status: aggregated node status (for topology graph rendering color: success→green, failed→red…)
          - hosts: simplified host list [{host_id, host_detail, status, exit_code, duration_ms, started_at, finished_at}]
          - first_started_at / last_finished_at / total_duration_ms
          - attempts_max: max retry count in this group (for UI display "host N retried M times" tags)
        Returns [] in non-DAG mode.
        """
        rows = self._collect_dag_rows(obj)
        if rows is None:
            return []
        try:
            host_cache = self._build_host_cache(rows)
        except Exception:
            return []
        # Only care about the latest attempt per (node_key, host_id) (discard historical attempts): overwrite by later occurrence
        latest_map: dict[tuple[str, str], Any] = {}
        for r in rows:
            key = (str(r.node_key), str(getattr(r, "host_id", "") or ""))
            cur = latest_map.get(key)
            if cur is None or (getattr(r, "attempt_no", 1) or 1) > (getattr(cur, "attempt_no", 1) or 1):
                latest_map[key] = r

        groups: dict[str, list[Any]] = {}
        for (nk, _hid), row in latest_map.items():
            groups.setdefault(nk, []).append(row)

        # Topological order fallback (groups output order = definition order)
        dag_ver_id = getattr(obj, 'dag_version_id', None)
        topo_idx: dict[str, int] = {}
        try:
            from taurus.workflow.models import WorkflowDAGVersion
            ver = WorkflowDAGVersion.objects.filter(pk=dag_ver_id).values('definition').first() if dag_ver_id else None
            if ver:
                topo_idx = self._topo_order_by_definition(ver.get('definition'))
        except Exception:
            topo_idx = {}
        tail = len(topo_idx) or 1_000_000

        # Status aggregation priority (higher number = more critical, used as group's overall status upper bound)
        # pending=0, running=1, skipped=4, cancelled=5, success=2, failed=3
        # Rule: if any failed aggregate → failed; if running and no failed → running;
        #       all success → success; all skipped/cancelled → corresponding.
        def _aggregate(statuses: list[int]) -> int:
            from taurus.workflow.engine.schemas import (
                STATUS_PENDING, STATUS_RUNNING, STATUS_SUCCESS, STATUS_FAILED,
                STATUS_SKIPPED, STATUS_CANCELLED,
            )
            s = set(statuses)
            if not s:
                return STATUS_PENDING
            if STATUS_FAILED in s:
                return STATUS_FAILED
            if STATUS_RUNNING in s:
                return STATUS_RUNNING
            if STATUS_PENDING in s:
                return STATUS_PENDING
            if STATUS_CANCELLED in s and STATUS_SUCCESS in s:
                return STATUS_CANCELLED
            if STATUS_SKIPPED in s and len(s) > 1:
                # Has skipped but simultaneously has other non-terminal states, use actual terminal state
                rest = s - {STATUS_SKIPPED}
                if rest == {STATUS_SUCCESS}:
                    return STATUS_SUCCESS
                return _aggregate(list(rest))
            # Unitary: directly return (SUCCESS / SKIPPED / CANCELLED)
            return next(iter(s))

        _STATUS_DISPLAY = {0: 'pending', 1: 'running', 2: 'success', 3: 'failed', 4: 'skipped', 5: 'cancelled'}
        import datetime as _dt
        _SENTINEL_FUTURE = _dt.datetime(2999, 12, 31, 23, 59, 59)
        _SENTINEL_PAST = _dt.datetime(1970, 1, 1)

        result: list[dict[str, Any]] = []
        for nk, grp_rows in groups.items():
            status_counts = {k: 0 for k in ("pending", "running", "success", "failed", "skipped", "cancelled")}
            statuses: list[int] = []
            hosts_slim: list[dict[str, Any]] = []
            attempts_max = 0
            first_started = _SENTINEL_FUTURE
            last_finished = _SENTINEL_PAST
            total_dur_ms = 0

            for r in grp_rows:
                st = int(r.status or 0)
                statuses.append(st)
                key = _STATUS_DISPLAY.get(st, 'unknown')
                if key in status_counts:
                    status_counts[key] += 1
                attempt = int(getattr(r, "attempt_no", 1) or 1)
                if attempt > attempts_max:
                    attempts_max = attempt
                sa = getattr(r, "started_at", None)
                if isinstance(sa, _dt.datetime) and sa < first_started:
                    first_started = sa
                fi = getattr(r, "finished_at", None)
                if isinstance(fi, _dt.datetime) and fi > last_finished:
                    last_finished = fi
                dur = getattr(r, "duration_ms", None)
                if isinstance(dur, int) and dur > 0:
                    total_dur_ms += dur

                # — host_detail — (using host_cache, avoid re-querying the library)
                hid = getattr(r, "host_id", None)
                host_detail: dict | None
                if not hid or str(hid) == "__NO_HOST__" or str(hid).strip() == "":
                    host_detail = None
                else:
                    ho = host_cache.get(str(hid)) if isinstance(host_cache, dict) else None
                    if ho is None:
                        host_detail = {"id": str(hid), "host_name": f"Unknown host ({str(hid)[:8]})",
                                       "host_ip": "", "host_type": "", "deleted": True, "resolved": False}
                    else:
                        host_detail = {
                            "id": str(ho.pk),
                            "host_uuid": str(getattr(ho, "host_uuid", "") or ""),
                            "host_name": str(getattr(ho, "host_name", "") or "") or str(ho.pk)[:8],
                            "host_ip": str(getattr(ho, "host_ip", "") or ""),
                            "host_type": str(getattr(ho, "host_type", "") or ""),
                            "resolved": True,
                        }
                row_output = getattr(r, "output", None) or None
                hosts_slim.append({
                    "id": getattr(r, "id", None),
                    "host_id": str(hid or ""),
                    "host_detail": host_detail,
                    "status": st,
                    "status_display": _STATUS_DISPLAY.get(st, 'unknown'),
                    "exit_code": getattr(r, "exit_code", None),
                    "duration_ms": getattr(r, "duration_ms", None),
                    "attempt_no": attempt,
                    "started_at": sa.isoformat() if isinstance(sa, _dt.datetime) else None,
                    "finished_at": fi.isoformat() if isinstance(fi, _dt.datetime) else None,
                    "error_message": getattr(r, "error_message", None) or None,
                    "output": row_output,
                    "workflow_node_execution_id": getattr(r, "id", None),
                })

            # hosts_slim stable ordering: by status_display (criticality first), then by host_name
            _SEVERITY = {'failed': 0, 'running': 1, 'pending': 2, 'cancelled': 3, 'skipped': 4, 'success': 5}
            hosts_slim.sort(key=lambda h: (
                _SEVERITY.get(h["status_display"], 99),
                (h.get("host_detail") or {}).get("host_name", "") or h["host_id"],
            ))

            overall = _aggregate(statuses)
            total_hosts = len(grp_rows)
            completed_hosts = sum(
                1 for s in statuses if s in {2, 3, 4, 5}  # success/failed/skipped/cancelled
            )
            result.append({
                "node_key": nk,
                "node_type": str(getattr(grp_rows[0], "node_type", "") or ""),
                "node_name": str(getattr(grp_rows[0], "node_name", "") or nk),
                "total_hosts": total_hosts,
                "completed_hosts": completed_hosts,
                "overall_status": overall,
                "overall_status_display": _STATUS_DISPLAY.get(overall, 'unknown'),
                "status_counts": status_counts,
                "attempts_max": attempts_max,
                "first_started_at": first_started.isoformat() if isinstance(first_started, _dt.datetime) and first_started != _SENTINEL_FUTURE else None,
                "last_finished_at": last_finished.isoformat() if isinstance(last_finished, _dt.datetime) and last_finished != _SENTINEL_PAST else None,
                "total_duration_ms": total_dur_ms or None,
                "hosts": hosts_slim,
            })

        # groups ordered by topological index
        result.sort(key=lambda g: (
            topo_idx.get(str(g["node_key"]), tail),
            g["node_key"],
        ))
        return result

    def get_dag_version_detail(self, obj):
        if not getattr(obj, 'dag_version_id', None):
            return None
        try:
            from taurus.workflow.models import WorkflowDAGVersion
            from taurus.workflow.engine.runner import _sanitize_canonical_node_name
            ver = WorkflowDAGVersion.objects.filter(pk=obj.dag_version_id).values(
                'id', 'version', 'definition', 'global_envs', 'release_note',
            ).first()
            if ver and isinstance(ver.get('definition'), dict):
                nodes = ver['definition'].get('nodes') or []
                for n in nodes:
                    if not isinstance(n, dict):
                        continue
                    nt = str(n.get('node_type') or '')
                    nn = n.get('node_name')
                    if nn is not None:
                        n['node_name'] = _sanitize_canonical_node_name(nt, str(nn))
            return ver
        except Exception:
            return None


class WorkflowStepExecutionSerializer(CustomModelSerializer):
    step_name = serializers.SerializerMethodField()
    template_name = serializers.CharField(source='step.template.template_name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = WorkflowStepExecution
        fields = [
            'id', 'execution', 'step', 'step_name', 'template_name',
            'status', 'status_display', 'start_time', 'end_time',
            'return_code', 'stdout', 'stderr', 'error_message',
            'create_datetime', 'update_datetime'
        ]
        read_only_fields = ['create_datetime', 'update_datetime']

    def get_step_name(self, obj):
        return obj.step.step_name or obj.step.template.template_name


class WorkflowExecuteSerializer(serializers.Serializer):
    host_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text="ExecutionTarget hostIDlist"
    )
    global_envs = serializers.DictField(
        child=serializers.CharField(),
        required=False,
        default=dict,
        help_text="globalEnvironment variables"
    )


# [M2.2 Thin Wrapper] WorkflowDAGVersionSerializer → EE 实现
#
# 先提供 CE 基础版本（真实 Meta.model），否则 EE 缺失时 WorkflowViewSet.publish
# （CE 功能，无 EE Gate）在成功返回时会访问 WorkflowDAGVersionSerializer(dag_ver).data
# 触发 DRF get_field_info → AttributeError: 'NoneType' object has no attribute '_meta'。
from taurus.workflow.models import WorkflowDAGVersion as _WorkflowDAGVersionModel  # noqa: E402 (circular import guard)
class WorkflowDAGVersionSerializer(CustomModelSerializer):
    """Workflow DAG published version serializer (CE base — EE overrides via Thin Wrapper below)."""
    class Meta:
        model = _WorkflowDAGVersionModel
        fields = '__all__'
        read_only_fields = ['id', 'create_datetime', 'update_datetime', 'creator']


try:
    from taurus_ee.serializers.workflow_dag import (
    WorkflowDAGVersionSerializer as _EEWorkflowDAGVersionSerializer,
    )
    _EE_WORKFLOW_DAG_SER_OK = True
except ImportError:
    _EE_WORKFLOW_DAG_SER_OK = False
    from taurus.ee_fallback import _EEFallbackSerializer as _FBSer
    class _EEWorkflowDAGVersionSerializer(_FBSer): pass


if _EE_WORKFLOW_DAG_SER_OK:
    class WorkflowDAGVersionSerializer(_EEWorkflowDAGVersionSerializer):  # noqa: F811
        """Thin wrapper — EE implementation in taurus_ee.serializers.workflow_dag"""
        pass


class WorkflowNodeExecutionSerializer(CustomModelSerializer):
    status_display = serializers.SerializerMethodField()
    node_name = serializers.SerializerMethodField()
    host_detail = serializers.SerializerMethodField()

    class Meta:
        model = None
        fields = [
            'id', 'execution', 'dag_version', 'node_key', 'node_type', 'node_name',
            'host_id', 'host_detail', 'dispatch_id', 'attempt_no', 'rendered_params',
            'status', 'status_display', 'exit_code', 'error_message',
            'adapter_state', 'output', 'output_refs',
            'queued_at', 'started_at', 'finished_at', 'duration_ms',
            'timeout_sec', 'fail_strategy',
            'create_datetime', 'update_datetime',
        ]
        read_only_fields = ['create_datetime', 'update_datetime']

    def __init__(self, *args, **kwargs):
        from taurus.workflow.models import WorkflowNodeExecution
        self.Meta.model = WorkflowNodeExecution
        super().__init__(*args, **kwargs)

    def get_status_display(self, obj):
        _MAP = {0: 'pending', 1: 'running', 2: 'success', 3: 'failed', 4: 'skipped', 5: 'cancelled'}
        return _MAP.get(obj.status, 'unknown')

    def get_node_name(self, obj):
        try:
            from taurus.workflow.engine.runner import _sanitize_canonical_node_name
        except Exception:
            _sanitize_canonical_node_name = lambda nt, name: name
        return _sanitize_canonical_node_name(str(obj.node_type or ''), str(obj.node_name or ''))

    def get_host_detail(self, obj) -> dict | None:
        """Map WorkflowNodeExecution.host_id (may be UUID string / integer / __NO_HOST__)
        to a readable object with name/ip/type, so the frontend can display host names directly without exposing raw UUIDs."""
        hid = getattr(obj, "host_id", None)
        if not hid or str(hid) == "__NO_HOST__" or str(hid).strip() == "":
            return None
        cache = getattr(self.context, "host_cache", None) if hasattr(self, "context") else None
        if cache is None and isinstance(self.context, dict):
            cache = self.context.get("host_cache")
        host_obj = None
        if isinstance(cache, dict):
            host_obj = cache.get(str(hid))
        if host_obj is None:
            try:
                from taurus.models import Host
                import uuid as _uuid
                hs = str(hid)
                # 1) By UUID pk
                try:
                    u = _uuid.UUID(hs)
                    host_obj = Host.objects.filter(id=u).first()
                except (ValueError, AttributeError, TypeError):
                    host_obj = None
                # 2) By integer pk (e.g. when frontend selector stores int pk)
                if host_obj is None and hs.isdigit():
                    host_obj = Host.objects.filter(id=int(hs)).first()
                # 3) By host_uuid string
                if host_obj is None:
                    host_obj = Host.objects.filter(host_uuid=hs).first()
                # 4) By host_ip fallback (extreme case where host_id stores an IP)
                if host_obj is None:
                    host_obj = Host.objects.filter(host_ip=hs).first()
            except Exception:
                host_obj = None
        if host_obj is None:
            # Not found (host deleted / dirty data): return minimal structure to avoid frontend undefined
            return {"id": str(hid), "host_name": f"Unknown host ({str(hid)[:8]})", "host_ip": "", "host_type": "",
                    "deleted": True, "resolved": False}
        return {
            "id": str(host_obj.pk),
            "host_uuid": str(getattr(host_obj, "host_uuid", "") or ""),
            "host_name": str(getattr(host_obj, "host_name", "") or "") or str(host_obj.pk)[:8],
            "host_ip": str(getattr(host_obj, "host_ip", "") or ""),
            "host_type": str(getattr(host_obj, "host_type", "") or ""),
            "status": getattr(host_obj, "status", None),
            "resolved": True,
        }


class ScheduleSerializer(CustomModelSerializer):
    template_name = serializers.CharField(source='template.template_name', read_only=True)
    workflow_name = serializers.CharField(source='workflow.name', read_only=True)
    dag_version_info = serializers.SerializerMethodField()
    hosts_detail = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    schedule_type_display = serializers.CharField(source='get_schedule_type_display', read_only=True)
    target_type_display = serializers.CharField(source='get_target_type_display', read_only=True)

    class Meta:
        model = Schedule
        fields = [
            'id', 'name', 'description', 'schedule_type', 'schedule_type_display',
            'cron_expression', 'interval_seconds', 'run_once_at',
            'target_type', 'target_type_display', 'template', 'template_name',
            'workflow', 'workflow_name', 'dag_version', 'dag_version_info',
            'hosts', 'hosts_detail',
            'envs', 'args', 'status', 'status_display',
            'last_run_time', 'next_run_time', 'celery_task_id',
            'create_datetime', 'update_datetime', 'creator', 'modifier'
        ]
        read_only_fields = ['create_datetime', 'update_datetime', 'creator', 'modifier', 'celery_task_id']

    def get_dag_version_info(self, obj):
        if obj.dag_version_id is None:
            return None
        return {'id': obj.dag_version_id, 'version': obj.dag_version.version}

    def get_hosts_detail(self, obj):
        hosts = obj.hosts.all()
        return [{'id': h.id, 'host_name': h.host_name, 'host_ip': h.host_ip} for h in hosts]


class ScheduleListSerializer(CustomModelSerializer):
    template_name = serializers.CharField(source='template.template_name', read_only=True)
    workflow_name = serializers.CharField(source='workflow.name', read_only=True)
    hosts_count = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    schedule_type_display = serializers.CharField(source='get_schedule_type_display', read_only=True)
    target_type_display = serializers.CharField(source='get_target_type_display', read_only=True)

    class Meta:
        model = Schedule
        fields = [
            'id', 'name', 'description', 'schedule_type', 'schedule_type_display',
            'cron_expression', 'interval_seconds', 'run_once_at',
            'target_type', 'target_type_display', 'template_name', 'workflow_name',
            'hosts_count', 'status', 'status_display',
            'last_run_time', 'next_run_time',
            'create_datetime', 'update_datetime', 'creator', 'modifier'
        ]

    def get_hosts_count(self, obj):
        return obj.hosts.count()


class ScheduleExecutionSerializer(CustomModelSerializer):
    schedule_name = serializers.CharField(source='schedule.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = ScheduleExecution
        fields = [
            'id', 'schedule', 'schedule_name', 'status', 'status_display',
            'start_time', 'end_time', 'result', 'error_message',
            'create_datetime', 'update_datetime'
        ]
        read_only_fields = ['create_datetime', 'update_datetime']


class HostSerializer(CustomModelSerializer):
    users_detail = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    certificate_status_display = serializers.CharField(source='get_certificate_status_display', read_only=True)
    heartbeat_server_name = serializers.CharField(source='heartbeat_server.name', read_only=True)

    class Meta:
        model = Host
        fields = '__all__'
        read_only_fields = ['create_datetime', 'update_datetime', 'creator', 'modifier', 'status', 
                           'certificate_serial', 'certificate_status', 'certificate_revoked_at', 'certificate_revocation_reason']

    def get_users_detail(self, obj):
        return [{'id': u.id, 'name': u.name, 'username': u.username} for u in obj.users.all()]


class ExecutorRegisterSerializer(serializers.Serializer):
    """Executor register request serializer"""
    token = serializers.CharField(max_length=64, help_text="RegisterToken")
    host_info = serializers.DictField(help_text="HostMessage")
    supervisor_version = serializers.CharField(max_length=50, required=False, default='1.0.0', help_text="Supervisor version")

    def validate_host_info(self, value):
        if not value.get('hostname'):
            raise serializers.ValidationError("Missing hostname")
        if not value.get('ip'):
            raise serializers.ValidationError("Missing ip")
        return value


class RegistrationTokenSerializer(CustomModelSerializer):
    """Registration token serializer"""
    is_expired = serializers.SerializerMethodField()
    is_used_up = serializers.SerializerMethodField()
    plain_token = serializers.CharField(read_only=True, help_text="Plaintext token (returned only once on creation, not displayed afterwards)")

    class Meta:
        model = RegistrationToken
        fields = '__all__'
        read_only_fields = ['token', 'token_prefix', 'used_count', 'create_datetime', 'update_datetime']

    def get_is_expired(self, obj):
        from django.utils import timezone
        return obj.expires_at < timezone.now()

    def get_is_used_up(self, obj):
        return obj.used_count >= obj.max_uses

    def create(self, validated_data):
        plain_token = RegistrationToken.generate_token()
        validated_data['token'] = RegistrationToken.hash_token(plain_token)
        validated_data['token_prefix'] = plain_token[:12]
        instance = super().create(validated_data)
        instance.plain_token = plain_token
        return instance

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        if not hasattr(instance, 'plain_token'):
            ret.pop('plain_token', None)
        return ret


class SupervisorHeartbeatSerializer(serializers.Serializer):
    """Supervisor heartbeat request serializer"""
    host_id = serializers.UUIDField(help_text="Host ID")
    host_name = serializers.CharField(max_length=255, required=False, default='', allow_blank=True, help_text="Host name")
    host_username = serializers.CharField(max_length=255, required=False, default='', allow_blank=True, help_text="Host username")
    supervisor_version = serializers.CharField(max_length=50, required=False, default='1.0.0', allow_blank=True, help_text="Supervisor version")
    timestamp = serializers.DateTimeField(help_text="Heartbeat timestamp")
    metrics = serializers.DictField(required=False, default=dict, help_text="System metrics")
    programs = serializers.ListField(
        required=False,
        default=list,
        child=serializers.DictField(),
        help_text="Status list of all managed programs",
    )
    current_server_url = serializers.CharField(max_length=255, required=False, default='', allow_blank=True, help_text="Heartbeat service server URL that the host is currently joined to")


class HostHeartbeatSerializer(CustomModelSerializer):
    """Heartbeat record serializer (read-only)"""
    host_name = serializers.CharField(source='host.host_name', read_only=True)
    host_ip = serializers.CharField(source='host.host_ip', read_only=True)

    class Meta:
        model = HostHeartbeat
        fields = '__all__'
        read_only_fields = ['create_datetime', 'update_datetime']


class HeartbeatServerSerializer(CustomModelSerializer):
    """HeartbeatServiceserverSerializationserver"""
    load_ratio = serializers.FloatField(read_only=True)

    class Meta:
        model = HeartbeatServer
        fields = '__all__'
        read_only_fields = ['current_connections', 'create_datetime', 'update_datetime']


class ProgramInstallTemplateSerializer(CustomModelSerializer):
    """Program installtemplate-Serializationserver"""
    bound_hosts_count = serializers.SerializerMethodField()

    def get_bound_hosts_count(self, obj):
        return obj.host_bindings.count()

    class Meta:
        model = ProgramInstallTemplate
        fields = '__all__'
        read_only_fields = ['id', 'create_datetime', 'update_datetime']


class ProgramInstallTemplateCreateSerializer(CustomModelSerializer):
    """Program install template create serializer"""
    name = serializers.CharField(
        max_length=255,
        validators=[CustomUniqueValidator(queryset=ProgramInstallTemplate.objects.all(), message="Template name must be unique")],
    )

    class Meta:
        model = ProgramInstallTemplate
        fields = '__all__'
        read_only_fields = ['id', 'create_datetime', 'update_datetime']


class ProgramInstallTemplateUpdateSerializer(CustomModelSerializer):
    """Program install template update serializer"""

    class Meta:
        model = ProgramInstallTemplate
        fields = '__all__'
        read_only_fields = ['id', 'create_datetime', 'update_datetime', 'creator']


class ProgramHostBindingSerializer(CustomModelSerializer):
    """Host program binding serializer"""
    host_name = serializers.CharField(source='host.host_name', read_only=True)
    host_ip = serializers.CharField(source='host.host_ip', read_only=True)
    template_name = serializers.CharField(source='template.name', read_only=True)
    program_name = serializers.CharField(source='template.program_name', read_only=True)
    template_version = serializers.CharField(source='template.version', read_only=True)

    class Meta:
        model = ProgramHostBinding
        fields = '__all__'
        read_only_fields = ['id', 'create_datetime', 'update_datetime']


class ProgramHostBindingCreateSerializer(CustomModelSerializer):
    """Host program binding create serializer"""

    class Meta:
        model = ProgramHostBinding
        fields = '__all__'
        read_only_fields = ['id', 'installed', 'create_datetime', 'update_datetime']


class ProgramHostBindingUpdateSerializer(CustomModelSerializer):
    """Host program binding update serializer"""

    class Meta:
        model = ProgramHostBinding
        fields = '__all__'
        read_only_fields = ['id', 'host', 'template', 'create_datetime', 'update_datetime', 'creator']


class ProgramInstallConfigSerializer(CustomModelSerializer):
    """Program installConfig-Serializationserver"""
    host_name = serializers.CharField(source='host.host_name', read_only=True)
    host_ip = serializers.CharField(source='host.host_ip', read_only=True)

    class Meta:
        model = ProgramInstallConfig
        fields = '__all__'
        read_only_fields = ['id', 'create_datetime', 'update_datetime']


class ProgramInstallConfigCreateSerializer(CustomModelSerializer):
    """Program installConfig-CreateSerializationserver"""

    class Meta:
        model = ProgramInstallConfig
        fields = '__all__'
        read_only_fields = ['id', 'installed', 'create_datetime', 'update_datetime']


class ProgramInstallConfigUpdateSerializer(CustomModelSerializer):
    """Program installConfig-modifySerializationserver"""

    class Meta:
        model = ProgramInstallConfig
        fields = '__all__'
        read_only_fields = ['id', 'installed', 'create_datetime', 'update_datetime', 'creator']


class ProgramCommandSerializer(CustomModelSerializer):
    """Program management command serializer"""
    host_name = serializers.CharField(source='host.host_name', read_only=True)
    host_ip = serializers.CharField(source='host.host_ip', read_only=True)
    action_display = serializers.CharField(source='get_action_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = ProgramCommand
        fields = '__all__'
        read_only_fields = ['id', 'create_datetime', 'update_datetime']


class ProgramCommandCreateSerializer(CustomModelSerializer):
    """Program management command create serializer"""

    class Meta:
        model = ProgramCommand
        fields = '__all__'
        read_only_fields = ['id', 'status', 'result_message', 'executed_at', 'create_datetime', 'update_datetime']


class ProgramCommandUpdateSerializer(CustomModelSerializer):
    """Program management command update serializer"""

    class Meta:
        model = ProgramCommand
        fields = '__all__'
        read_only_fields = ['id', 'host', 'program_name', 'action', 'create_datetime', 'update_datetime', 'creator']


class ProgramInstallPolicySerializer(CustomModelSerializer):
    """Program install policy serializer"""
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = ProgramInstallPolicy
        fields = '__all__'
        read_only_fields = ['id', 'matched_hosts_count', 'applied_hosts_count', 'create_datetime', 'update_datetime']


class ProgramInstallPolicyCreateSerializer(CustomModelSerializer):
    """Program install policy create serializer"""

    class Meta:
        model = ProgramInstallPolicy
        fields = '__all__'
        read_only_fields = ['id', 'matched_hosts_count', 'applied_hosts_count', 'create_datetime', 'update_datetime']


class ProgramInstallPolicyUpdateSerializer(CustomModelSerializer):
    """Program install policy update serializer"""

    class Meta:
        model = ProgramInstallPolicy
        fields = '__all__'
        read_only_fields = ['id', 'matched_hosts_count', 'applied_hosts_count', 'create_datetime', 'update_datetime', 'creator']


# ============================================================================
# [M2.4 Thin Wrapper] Supervisor 程序管理高级 Serializers（F_PROGRAM_INSTALL_TEMPLATE /
#  F_PROGRAM_HOST_BINDING / F_PROGRAM_INSTALL_CONFIG / F_PROGRAM_COMMAND_BATCH /
#  F_PROGRAM_INSTALL_POLICY — EE 专属。原类名保留不动，thin pass 继承 taurus_ee.*。
#
# 注意：这些 Serializer 均已在上方定义了 CE 基础版本（含正确的 Meta.model）。
# Thin Wrapper 仅在 EE 成功导入时才重新定义（继承 EE 扩展）；若 EE 缺失，
# 必须保留原 CE 版本不变，否则继承 _EEFallbackSerializer (model=None) 会导致
# DRF 在 get_field_info 阶段抛出 AttributeError: 'NoneType' object has no attribute '_meta'
# （尤其是 ProgramCommandViewSet，其基础 CRUD 不走 EE Gate）。
# ============================================================================
try:
    from taurus_ee.serializers.supervisor_program import (
    _EEProgramInstallTemplateSerializer as _EE_PIT_Ser,
    _EEProgramInstallTemplateCreateSerializer as _EE_PIT_CreateSer,
    _EEProgramInstallTemplateUpdateSerializer as _EE_PIT_UpdateSer,
    _EEProgramHostBindingSerializer as _EE_PHB_Ser,
    _EEProgramHostBindingCreateSerializer as _EE_PHB_CreateSer,
    _EEProgramHostBindingUpdateSerializer as _EE_PHB_UpdateSer,
    _EEProgramInstallConfigSerializer as _EE_PIC_Ser,
    _EEProgramInstallConfigCreateSerializer as _EE_PIC_CreateSer,
    _EEProgramInstallConfigUpdateSerializer as _EE_PIC_UpdateSer,
    _EEProgramCommandSerializer as _EE_PC_Ser,
    _EEProgramCommandCreateSerializer as _EE_PC_CreateSer,
    _EEProgramCommandUpdateSerializer as _EE_PC_UpdateSer,
    _EEProgramInstallPolicySerializer as _EE_PIP_Ser,
    _EEProgramInstallPolicyCreateSerializer as _EE_PIP_CreateSer,
    _EEProgramInstallPolicyUpdateSerializer as _EE_PIP_UpdateSer,
    )
    _EE_SUPERVISOR_PROGRAM_OK = True
except ImportError:
    _EE_SUPERVISOR_PROGRAM_OK = False
    from taurus.ee_fallback import _EEFallbackSerializer as _FBSer
    class _EE_PC_CreateSer(_FBSer): pass
    class _EE_PC_Ser(_FBSer): pass
    class _EE_PC_UpdateSer(_FBSer): pass
    class _EE_PHB_CreateSer(_FBSer): pass
    class _EE_PHB_Ser(_FBSer): pass
    class _EE_PHB_UpdateSer(_FBSer): pass
    class _EE_PIC_CreateSer(_FBSer): pass
    class _EE_PIC_Ser(_FBSer): pass
    class _EE_PIC_UpdateSer(_FBSer): pass
    class _EE_PIP_CreateSer(_FBSer): pass
    class _EE_PIP_Ser(_FBSer): pass
    class _EE_PIP_UpdateSer(_FBSer): pass
    class _EE_PIT_CreateSer(_FBSer): pass
    class _EE_PIT_Ser(_FBSer): pass
    class _EE_PIT_UpdateSer(_FBSer): pass


if _EE_SUPERVISOR_PROGRAM_OK:
    class ProgramInstallTemplateSerializer(_EE_PIT_Ser): pass  # noqa: E701 Thin Wrapper
    class ProgramInstallTemplateCreateSerializer(_EE_PIT_CreateSer): pass  # noqa: E701
    class ProgramInstallTemplateUpdateSerializer(_EE_PIT_UpdateSer): pass  # noqa: E701
    class ProgramHostBindingSerializer(_EE_PHB_Ser): pass  # noqa: E701
    class ProgramHostBindingCreateSerializer(_EE_PHB_CreateSer): pass  # noqa: E701
    class ProgramHostBindingUpdateSerializer(_EE_PHB_UpdateSer): pass  # noqa: E701
    class ProgramInstallConfigSerializer(_EE_PIC_Ser): pass  # noqa: E701
    class ProgramInstallConfigCreateSerializer(_EE_PIC_CreateSer): pass  # noqa: E701
    class ProgramInstallConfigUpdateSerializer(_EE_PIC_UpdateSer): pass  # noqa: E701
    class ProgramCommandSerializer(_EE_PC_Ser): pass  # noqa: E701
    class ProgramCommandCreateSerializer(_EE_PC_CreateSer): pass  # noqa: E701
    class ProgramCommandUpdateSerializer(_EE_PC_UpdateSer): pass  # noqa: E701
    class ProgramInstallPolicySerializer(_EE_PIP_Ser): pass  # noqa: E701
    class ProgramInstallPolicyCreateSerializer(_EE_PIP_CreateSer): pass  # noqa: E701
    class ProgramInstallPolicyUpdateSerializer(_EE_PIP_UpdateSer): pass  # noqa: E701


# ------------------ [M2.5 Thin Wrapper] HostLog & LogCommand (log center EE) ------------------
# 这些 Serializer 是 EE 专属功能（HostLogViewSet / LogCommandViewSet / TaskCenterViewSet
# 等均带 dispatch EE Gate），但仍然加 _EE_OK 守卫，避免未来有人绕过 gate 直接使用
# 时踩到 _EEFallbackSerializer 的 Meta.model=None 问题。
try:
    from taurus_ee.serializers.log_ext_center import (
    _EEHostLogSerializer,
    _EEHostLogReceiveSerializer,
    _EELogCommandSerializer,
    _EELogCommandCreateSerializer,
    _EELogCommandUpdateSerializer,
    _EETaskCenterItemSerializer,
    _EEContactLeadSerializer,
    _EEKnowledgeBasePlaceholderSerializer,
    _EEInspectionCenterPlaceholderSerializer,
    _EEToolsCenterPlaceholderSerializer,
    _EEBackupRestoreCenterPlaceholderSerializer,
    _EEDownloadCenterPlaceholderSerializer,
    )
    _EE_LOG_EXT_OK = True
except ImportError:
    _EE_LOG_EXT_OK = False
    from taurus.ee_fallback import _EEFallbackSerializer as _FBSer
    class _EEBackupRestoreCenterPlaceholderSerializer(_FBSer): pass
    class _EEContactLeadSerializer(_FBSer): pass
    class _EEDownloadCenterPlaceholderSerializer(_FBSer): pass
    class _EEHostLogReceiveSerializer(_FBSer): pass
    class _EEHostLogSerializer(_FBSer): pass
    class _EEInspectionCenterPlaceholderSerializer(_FBSer): pass
    class _EEKnowledgeBasePlaceholderSerializer(_FBSer): pass
    class _EELogCommandCreateSerializer(_FBSer): pass
    class _EELogCommandSerializer(_FBSer): pass
    class _EELogCommandUpdateSerializer(_FBSer): pass
    class _EETaskCenterItemSerializer(_FBSer): pass
    class _EEToolsCenterPlaceholderSerializer(_FBSer): pass


if _EE_LOG_EXT_OK:
    class HostLogSerializer(_EEHostLogSerializer):
        """Thin Wrapper — EE impl: taurus_ee.serializers.log_ext_center._EEHostLogSerializer"""
        pass


    class HostLogReceiveSerializer(_EEHostLogReceiveSerializer):
        """Thin Wrapper — EE impl: taurus_ee.serializers.log_ext_center._EEHostLogReceiveSerializer"""
        pass


class ManagedProgramSerializer(CustomModelSerializer):
    """Managed program serializer"""
    host_name = serializers.CharField(source='host.host_name', read_only=True)
    host_ip = serializers.CharField(source='host.host_ip', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = ManagedProgram
        fields = '__all__'
        read_only_fields = ['id', 'create_datetime', 'update_datetime', 'creator', 'modifier']





if _EE_LOG_EXT_OK:
    class LogCommandSerializer(_EELogCommandSerializer):
        """Thin Wrapper — EE impl: taurus_ee.serializers.log_ext_center._EELogCommandSerializer"""
        pass


    class LogCommandCreateSerializer(_EELogCommandCreateSerializer):
        """Thin Wrapper — EE impl: taurus_ee.serializers.log_ext_center._EELogCommandCreateSerializer"""
        pass


    class LogCommandUpdateSerializer(_EELogCommandUpdateSerializer):
        """Thin Wrapper (NEW M2.5) — EE impl: taurus_ee.serializers.log_ext_center._EELogCommandUpdateSerializer"""
        pass





# ==================== Ops Center Serializers ====================

class OpsCommandExecuteSerializer(serializers.Serializer):
    """Command execution request serializer"""
    host_id = serializers.CharField(help_text="Target host ID (integer PK or UUID)")
    command = serializers.CharField(max_length=1000, help_text="Command to execute")
    args = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=[],
        help_text="Command argument list"
    )
    working_directory = serializers.CharField(
        max_length=500,
        required=False,
        default="",
        help_text="Working directory"
    )
    timeout_seconds = serializers.IntegerField(
        required=False,
        default=300,
        help_text="Timeout (seconds)"
    )
    environment = serializers.DictField(
        required=False,
        default={},
        help_text="Environment variables"
    )
    use_shell = serializers.BooleanField(
        required=False,
        default=None,
        allow_null=True,
        help_text="Whether to use shell mode, null means default enabled"
    )
    merge_streams = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Whether to merge stdout and stderr"
    )
    load_profile = serializers.ChoiceField(
        choices=[('false', 'Clean environment'), ('true', 'Load bashrc'), ('login', 'Login Shell')],
        required=False,
        default='false',
        help_text="Shell environment load mode: false=clean env (--noprofile --norc), true=load ~/.bashrc, login=full login shell"
    )
    privileged = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Whether to execute with privileged user"
    )
    su_user = serializers.CharField(
        required=False,
        default=None,
        allow_null=True,
        max_length=64,
        help_text="su target user (effective when privileged=True)"
    )
    su_password = serializers.CharField(
        required=False,
        default=None,
        allow_null=True,
        max_length=256,
        help_text="su password (effective when privileged=True, plaintext transmission not recommended)"
    )
    batch_id = serializers.CharField(
        required=False,
        default=None,
        allow_null=True,
        max_length=64,
        help_text="Batch ID, used to identify records of the same batch execution"
    )


class OpsScriptExecuteSerializer(serializers.Serializer):
    """Script execution request serializer"""
    host_id = serializers.CharField(help_text="Target host ID (integer PK or UUID)")
    script_type = serializers.ChoiceField(
        choices=[('sh', 'Shell'), ('python', 'Python')],
        help_text="Script type"
    )
    script_content = serializers.CharField(help_text="Script content")
    args = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=[],
        help_text="Script parameters"
    )
    working_directory = serializers.CharField(
        required=False,
        default=None,
        allow_null=True,
        allow_blank=True,
        max_length=500,
        help_text="Working directory"
    )
    environment = serializers.DictField(
        required=False,
        default={},
        help_text="Environment variables"
    )
    timeout_seconds = serializers.IntegerField(
        required=False,
        default=300,
        help_text="Timeout (seconds)"
    )
    merge_streams = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Whether to merge stdout and stderr"
    )
    load_profile = serializers.ChoiceField(
        choices=[('false', 'Clean environment'), ('true', 'Load bashrc'), ('login', 'Login Shell')],
        required=False,
        default='false',
        help_text="Shell environment load mode: false=clean env (--noprofile --norc), true=load ~/.bashrc, login=full login shell"
    )
    privileged = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Whether to execute with privileged user"
    )
    su_user = serializers.CharField(
        required=False,
        default=None,
        allow_null=True,
        max_length=64,
        help_text="su target user (effective when privileged=True)"
    )
    su_password = serializers.CharField(
        required=False,
        default=None,
        allow_null=True,
        max_length=256,
        help_text="su password (effective when privileged=True, plaintext transmission not recommended)"
    )
    batch_id = serializers.CharField(
        required=False,
        default=None,
        allow_null=True,
        max_length=64,
        help_text="Batch ID, used to identify records of the same batch execution"
    )


class OpsFileUploadSerializer(serializers.Serializer):
    """File upload request serializer"""
    host_id = serializers.CharField(help_text="Target host ID (integer PK or UUID)")
    file_path = serializers.CharField(max_length=1000, help_text="Target file path")
    file = serializers.FileField(help_text="File to upload")


class OpsBackendTempUploadSerializer(serializers.Serializer):
    """Upload file to backend temporary directory request serializer"""
    file = serializers.FileField(help_text="File to upload")
    original_filename = serializers.CharField(max_length=500, required=False, default="", help_text="Original filename (optional)")


class OpsBatchBackendTempUploadSerializer(serializers.Serializer):
    """Batch upload files to backend temporary directory request serializer"""
    files = serializers.ListField(child=serializers.FileField(), help_text="Multiple files to upload")
    default_target_prefix = serializers.CharField(max_length=500, required=False, default="", help_text="Target path prefix (optional, e.g. /opt/app)")


class OpsFileListSerializer(serializers.Serializer):
    """File list request serializer"""
    host_id = serializers.CharField(help_text="Target host ID (integer PK or UUID)")
    path = serializers.CharField(max_length=1000, required=False, default="/", help_text="Directory path")


class OpsFileDownloadSerializer(serializers.Serializer):
    """File download request serializer"""
    host_id = serializers.CharField(help_text="Target host ID (integer PK or UUID)")
    path = serializers.CharField(max_length=1000, help_text="File path")


class OpsExecutionSerializer(CustomModelSerializer):
    host_name = serializers.CharField(source='host.host_name', read_only=True)
    host_ip = serializers.CharField(source='host.host_ip', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    execution_type_display = serializers.CharField(source='get_execution_type_display', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True, default='')

    class Meta:
        model = OpsExecution
        fields = [
            'id', 'execution_id', 'batch_id', 'execution_type', 'execution_type_display',
            'host', 'host_name', 'host_ip', 'user', 'username',
            'command', 'script_type', 'script_content', 'args',
            'file_path', 'file_size',
            'working_directory', 'timeout_seconds', 'environment',
            'use_shell', 'merge_streams', 'privileged', 'su_user',
            'exec_mode', 'concurrent', 'fail_strategy',
            'pilot_count', 'pilot_success_rate',
            'need_audit', 'auto_notify',
            'approval_mode', 'approver_ids', 'countersign_ids', 'submit_desc',
            'status', 'status_display', 'exit_code', 'error_message',
            'output_buffer', 'started_at', 'finished_at',
            'create_datetime', 'update_datetime', 'creator', 'modifier',
        ]
        read_only_fields = [
            'id', 'execution_id', 'batch_id', 'execution_type',
            'host', 'user', 'command', 'script_type', 'script_content', 'args',
            'file_path', 'file_size',
            'working_directory', 'timeout_seconds', 'environment',
            'use_shell', 'merge_streams', 'privileged', 'su_user',
            'exec_mode', 'concurrent', 'fail_strategy',
            'pilot_count', 'pilot_success_rate',
            'need_audit', 'auto_notify',
            'approval_mode', 'approver_ids', 'countersign_ids', 'submit_desc',
            'status', 'exit_code', 'error_message', 'output_buffer',
            'started_at', 'finished_at',
            'create_datetime', 'update_datetime', 'creator', 'modifier',
        ]


class OpsExecutionListSerializer(CustomModelSerializer):
    host_name = serializers.CharField(source='host.host_name', read_only=True)
    host_ip = serializers.CharField(source='host.host_ip', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    execution_type_display = serializers.CharField(source='get_execution_type_display', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True, default='')
    duration = serializers.SerializerMethodField()

    class Meta:
        model = OpsExecution
        fields = [
            'id', 'execution_id', 'batch_id', 'execution_type', 'execution_type_display',
            'host', 'host_name', 'host_ip', 'user', 'username',
            'command', 'script_type', 'script_content', 'args',
            'working_directory', 'file_path', 'file_size',
            'timeout_seconds', 'environment', 'use_shell', 'merge_streams',
            'privileged', 'su_user',
            'exec_mode', 'concurrent', 'fail_strategy',
            'pilot_count', 'pilot_success_rate',
            'need_audit', 'auto_notify',
            'approval_mode', 'approver_ids', 'countersign_ids', 'submit_desc',
            'status', 'status_display', 'exit_code', 'error_message',
            'duration', 'started_at', 'finished_at',
            'create_datetime',
        ]

    def get_duration(self, obj):
        if obj.started_at and obj.finished_at:
            delta = obj.finished_at - obj.started_at
            total_seconds = int(delta.total_seconds())
            if total_seconds >= 60:
                minutes = total_seconds // 60
                seconds = total_seconds % 60
                return f"{minutes}m{seconds}s"
            return f"{total_seconds}s"
        return '-'


# ========================== Execution Task Approval Serializers (Thin Wrapper, 真实实现迁 taurus_ee.serializers.ops_approval) ==========================
try:
    from taurus_ee.serializers.ops_approval import (
    _EEOpsExecutionApprovalSerializer,
    _EEOpsExecutionApprovalListSerializer,
    _EEOpsExecutionApprovalCreateSerializer,
    _EEOpsExecutionApprovalActionSerializer,
    )
    _EE_OPS_APPROVAL_OK = True
except ImportError:
    _EE_OPS_APPROVAL_OK = False
    from taurus.ee_fallback import _EEFallbackSerializer as _FBSer
    class _EEOpsExecutionApprovalActionSerializer(_FBSer): pass
    class _EEOpsExecutionApprovalCreateSerializer(_FBSer): pass
    class _EEOpsExecutionApprovalListSerializer(_FBSer): pass
    class _EEOpsExecutionApprovalSerializer(_FBSer): pass


if _EE_OPS_APPROVAL_OK:
    class OpsExecutionApprovalSerializer(_EEOpsExecutionApprovalSerializer): pass
    class OpsExecutionApprovalListSerializer(_EEOpsExecutionApprovalListSerializer): pass
    class OpsExecutionApprovalCreateSerializer(_EEOpsExecutionApprovalCreateSerializer): pass
    class OpsExecutionApprovalActionSerializer(_EEOpsExecutionApprovalActionSerializer): pass


# ========================== ScriptlibrarySerializationserver ==========================

class ScriptCategorySerializer(CustomModelSerializer):
    """ScriptCategorySerializationserver"""
    category_type_display = serializers.CharField(source='get_category_type_display', read_only=True)
    script_count = serializers.SerializerMethodField()
    reviewers = serializers.SerializerMethodField()
    reviewer_ids = serializers.PrimaryKeyRelatedField(
        source='reviewers', many=True, read_only=False,
        queryset=__import__('django.contrib.auth', fromlist=['get_user_model']).get_user_model().objects.all(),
        write_only=True, required=False
    )

    class Meta:
        model = ScriptCategory
        fields = ['id', 'name', 'parent', 'category_type', 'category_type_display', 'sort', 'is_system', 'is_virtual', 'remark', 'script_count', 'reviewers', 'reviewer_ids', 'create_datetime', 'update_datetime', 'creator', 'modifier']
        read_only_fields = ['create_datetime', 'update_datetime', 'creator', 'modifier']

    def get_reviewers(self, obj):
        return [
            {'id': u.id, 'username': u.username, 'name': getattr(u, 'name', u.username)}
            for u in obj.reviewers.all()
        ]

    def get_script_count(self, obj):
        """Count scripts under this category and its child categories (with permission filter, consistent with ScriptViewSet)"""
        from taurus.models import Script, ScriptCategory
        from django.db import models as django_models

        request = self.context.get('request')
        user = request.user if request else None

        visible_scripts = Script.objects.all()
        if user and not user.is_superuser:
            visible_scripts = visible_scripts.filter(
                django_models.Q(creator=user) | django_models.Q(auth_type='Public')
            )

        def _is_under_mine(category):
            """Check if category is under 'My scripts' tree"""
            current = category
            visited = set()
            while current and current.id not in visited:
                visited.add(current.id)
                if current.category_type == 'system' and current.name == 'My scripts':
                    return True
                current = current.parent
            return False

        def _is_under_public(category):
            """Check if category is under 'Public scripts' tree"""
            current = category
            visited = set()
            while current and current.id not in visited:
                visited.add(current.id)
                if current.category_type == 'system' and current.name == 'Public scripts':
                    return True
                current = current.parent
            return False

        def _get_descendant_ids(parent_obj):
            """Fetch IDs of this category and all its child categories"""
            if not parent_obj:
                return []
            ids = [parent_obj.id]
            children = ScriptCategory.objects.filter(parent=parent_obj)
            for child in children:
                ids.extend(_get_descendant_ids(child))
            return ids

        if obj.category_type == 'system':
            if obj.name == 'All scripts':
                return visible_scripts.count()
            elif obj.name == 'My scripts':
                # Regular user: limited to "My scripts" category tree, only count scripts created by current user
                # Super admin: limited to "My scripts" category tree, see all scripts in that tree
                mine_ids = _get_descendant_ids(obj)
                qs = visible_scripts.filter(category_id__in=mine_ids)
                if user and not user.is_superuser:
                    qs = qs.filter(creator=user)
                return qs.count()
            elif obj.name == 'Public scripts':
                # Limited to "Public scripts" category tree, only count public scripts (consistent for all users)
                public_ids = _get_descendant_ids(obj)
                return visible_scripts.filter(
                    category_id__in=public_ids, auth_type='Public'
                ).count()
            elif obj.name == 'Pending approval scripts':
                return visible_scripts.filter(status=2).count()
            elif obj.name == 'Archived scripts':
                return visible_scripts.filter(status=3).count()

        category_ids = self._get_all_child_ids(obj.id)
        qs = visible_scripts.filter(category_id__in=category_ids)
        # Child categories under "My scripts": regular users only count their own created, super admins see all in that category tree
        if _is_under_mine(obj):
            if user and not user.is_superuser:
                qs = qs.filter(creator=user)
        elif _is_under_public(obj):
            qs = qs.filter(auth_type='Public')
        return qs.count()

    def _get_all_child_ids(self, parent_id):
        ids = [parent_id]
        children = ScriptCategory.objects.filter(parent=parent_id)
        for child in children:
            ids.extend(self._get_all_child_ids(child.id))
        return ids


class ScriptCategoryCreateSerializer(CustomModelSerializer):
    """Script category create serializer"""
    name = serializers.CharField(
        max_length=100,
        validators=[CustomUniqueValidator(
            queryset=ScriptCategory.objects.all(),
            message="Category name already exists"
        )]
    )
    reviewer_ids = serializers.PrimaryKeyRelatedField(
        source='reviewers', many=True, read_only=False,
        queryset=__import__('django.contrib.auth', fromlist=['get_user_model']).get_user_model().objects.all(),
        required=False, default=list
    )

    class Meta:
        model = ScriptCategory
        fields = ['name', 'parent', 'category_type', 'sort', 'is_virtual', 'remark', 'reviewer_ids']


class ScriptCategoryUpdateSerializer(CustomModelSerializer):
    """ScriptCategoryUpdateSerializationserver"""
    reviewer_ids = serializers.PrimaryKeyRelatedField(
        source='reviewers', many=True, read_only=False,
        queryset=__import__('django.contrib.auth', fromlist=['get_user_model']).get_user_model().objects.all(),
        required=False, default=list
    )

    class Meta:
        model = ScriptCategory
        fields = ['name', 'parent', 'sort', 'is_virtual', 'remark', 'reviewer_ids']
        read_only_fields = ['id', 'category_type', 'is_system', 'create_datetime', 'creator']


class ScriptSerializer(CustomModelSerializer):
    """ScriptDetailSerializationserver"""
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    type_display = serializers.CharField(source='get_script_type_display', read_only=True)
    auth_type_display = serializers.CharField(source='get_auth_type_display', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True, default='')
    fail_strategy_display = serializers.CharField(source='get_fail_strategy_display', read_only=True)
    creator_name = serializers.CharField(source='creator.username', read_only=True, default='')
    risk_level_display = serializers.SerializerMethodField(read_only=True)

    def get_risk_level_display(self, obj):
        risk_map = {'low': 'Low', 'medium': 'Medium', 'high': 'High'}
        return risk_map.get(obj.risk_level, obj.risk_level or 'Not detected')

    class Meta:
        model = Script
        fields = '__all__'
        read_only_fields = ['id', 'create_datetime', 'update_datetime', 'is_official']


class ScriptListSerializer(CustomModelSerializer):
    """Script list serializer"""
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    type_display = serializers.CharField(source='get_script_type_display', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True, default='')
    auth_type_display = serializers.CharField(source='get_auth_type_display', read_only=True)
    creator_name = serializers.CharField(source='creator.username', read_only=True, default='')
    version_count = serializers.IntegerField(read_only=True, default=0)
    risk_level_display = serializers.SerializerMethodField(read_only=True)
    share_summary = serializers.SerializerMethodField(read_only=True)
    current_perms = serializers.SerializerMethodField(read_only=True)

    def get_risk_level_display(self, obj):
        risk_map = {'low': 'Low', 'medium': 'Medium', 'high': 'High'}
        return risk_map.get(obj.risk_level, obj.risk_level or 'Not detected')

    def get_share_summary(self, obj):
        return getattr(obj, '_share_summary', {
            'total': 0,
            'direct_count': 0,
            'link_count': 0,
            'subjects': [],
            'links': [],
        })

    def get_current_perms(self, obj):
        perms = getattr(obj, '_current_perms', None)
        if perms is None:
            return []
        if isinstance(perms, (list, tuple, set)):
            return sorted(list(perms))
        return []

    class Meta:
        model = Script
        fields = [
            'id', 'name', 'script_type', 'type_display', 'category', 'category_name',
            'auth_type', 'auth_type_display', 'tags', 'desc', 'current_version',
            'timeout', 'concurrent', 'fail_strategy', 'open_risk_check', 'need_audit',
            'status', 'status_display', 'exec_count', 'last_exec_time',
            'creator_name', 'version_count', 'create_datetime', 'update_datetime',
            'is_official', 'risk_level', 'risk_level_display', 'supported_systems',
            'source', 'official_version',
            'share_summary', 'current_perms',
        ]


class ScriptCreateSerializer(CustomModelSerializer):
    """Script create serializer"""
    name = serializers.CharField(
        validators=[CustomUniqueValidator(
            queryset=Script.objects.all(),
            message="Script name already exists"
        )],
        max_length=200
    )

    class Meta:
        model = Script
        fields = [
            'name', 'script_type', 'category', 'auth_type', 'tags', 'desc',
            'content', 'timeout', 'concurrent', 'fail_strategy',
            'open_risk_check', 'need_audit', 'log_retention', 'script_params', 'script_envs',
            'risk_level',
        ]


class ScriptUpdateSerializer(CustomModelSerializer):
    """ScriptUpdateSerializationserver"""

    class Meta:
        model = Script
        fields = [
            'name', 'script_type', 'category', 'auth_type', 'tags', 'desc',
            'content', 'timeout', 'concurrent', 'fail_strategy',
            'open_risk_check', 'need_audit', 'log_retention', 'script_params', 'script_envs', 'status',
            'risk_level',
        ]
        read_only_fields = ['id', 'create_datetime']


class ScriptVersionSerializer(CustomModelSerializer):
    """ScriptVersionSerializationserver"""
    script_name = serializers.CharField(source='script.name', read_only=True)
    creator_name = serializers.CharField(source='creator.username', read_only=True, default='')

    class Meta:
        model = ScriptVersion
        fields = [
            'id', 'script', 'script_name', 'version', 'content', 'desc',
            'is_current', 'creator_name', 'create_datetime',
        ]


class ScriptPermissionSerializer(CustomModelSerializer):
    """ScriptPermissionConfigSerializationserver"""
    script_name = serializers.CharField(source='script.name', read_only=True)
    subject_type_display = serializers.CharField(source='get_subject_type_display', read_only=True)
    auth_level_display = serializers.CharField(source='get_auth_level_display', read_only=True)

    class Meta:
        model = ScriptPermission
        fields = [
            'id', 'script', 'script_name', 'subject_type', 'subject_type_display',
            'subject_id', 'auth_level', 'auth_level_display', 'grant_user',
            'create_datetime',
        ]


class ScriptTaskSerializer(CustomModelSerializer):
    """ScriptScheduled taskSerializationserver"""
    script_name = serializers.CharField(source='script.name', read_only=True)
    script_type = serializers.CharField(source='script.script_type', read_only=True)
    script_status = serializers.IntegerField(source='script.status', read_only=True)
    last_exec_result_display = serializers.CharField(
        source='get_last_exec_result_display', read_only=True
    )
    creator_name = serializers.CharField(source='creator.username', read_only=True, default='')
    schedule_type_display = serializers.CharField(
        source='get_schedule_type_display', read_only=True
    )
    host_count = serializers.SerializerMethodField()
    running_executions_count = serializers.SerializerMethodField()
    is_once_executed = serializers.SerializerMethodField()

    class Meta:
        model = ScriptTask
        fields = [
            'id', 'script', 'script_name', 'script_type', 'script_status',
            'name', 'description',
            'schedule_type', 'schedule_type_display',
            'cron_expression', 'interval_seconds', 'run_once_at',
            'hosts', 'host_count', 'timeout', 'fail_notify',
            'envs', 'args', 'enabled',
            'exec_count',
            'last_exec_time', 'last_exec_result', 'last_exec_result_display',
            'next_exec_time',
            'running_executions_count', 'is_once_executed',
            'creator_name', 'create_datetime', 'update_datetime',
        ]

    def get_host_count(self, obj):
        if isinstance(obj.hosts, list):
            return len(obj.hosts)
        return 0

    def get_running_executions_count(self, obj):
        annotated = getattr(obj, '_running_executions_count', None)
        if annotated is not None:
            return int(annotated or 0)
        cached = getattr(obj, 'running_executions_count', None)
        if cached is not None:
            return int(cached or 0)
        return obj.executions.filter(status__in=[0, 1]).count()

    def get_is_once_executed(self, obj):
        if obj.schedule_type != 'once':
            return False
        if not obj.run_once_at:
            return False
        if obj.exec_count and obj.exec_count > 0:
            return True
        return bool(obj.last_exec_time)


class ScriptTaskCreateSerializer(CustomModelSerializer):
    """ScriptScheduled taskcreateSerializationserver"""
    class Meta:
        model = ScriptTask
        fields = [
            'script', 'name', 'description',
            'schedule_type', 'cron_expression',
            'interval_seconds', 'run_once_at',
            'hosts', 'timeout', 'fail_notify',
            'envs', 'args', 'enabled',
        ]

    def validate(self, attrs):
        schedule_type = attrs.get('schedule_type', 'cron')
        if schedule_type == 'cron' and not attrs.get('cron_expression'):
            raise serializers.ValidationError({'cron_expression': 'Cron expression cannot be empty'})
        if schedule_type == 'interval' and not attrs.get('interval_seconds'):
            raise serializers.ValidationError({'interval_seconds': 'Interval seconds cannot be empty'})
        if schedule_type == 'once':
            run_once = attrs.get('run_once_at')
            if not run_once:
                raise serializers.ValidationError({'run_once_at': 'Execution time cannot be empty'})
            if run_once <= timezone.now():
                raise serializers.ValidationError({'run_once_at': 'Execution time has expired, please set a future time'})
        return attrs


class ScriptTaskUpdateSerializer(CustomModelSerializer):
    """Script scheduled task update serializer"""
    class Meta:
        model = ScriptTask
        fields = [
            'name', 'description',
            'schedule_type', 'cron_expression',
            'interval_seconds', 'run_once_at',
            'hosts', 'timeout', 'fail_notify',
            'envs', 'args', 'enabled',
        ]

    def validate(self, attrs):
        schedule_type = attrs.get('schedule_type')
        if not schedule_type:
            schedule_type = self.instance.schedule_type if self.instance else 'cron'
        if schedule_type == 'cron':
            cron = attrs.get('cron_expression') or (self.instance.cron_expression if self.instance else None)
            if not cron:
                raise serializers.ValidationError({'cron_expression': 'Cron expression cannot be empty'})
        if schedule_type == 'interval':
            interval = attrs.get('interval_seconds') or (self.instance.interval_seconds if self.instance else None)
            if not interval:
                raise serializers.ValidationError({'interval_seconds': 'Interval seconds cannot be empty'})
        if schedule_type == 'once':
            run_once = attrs.get('run_once_at') or (self.instance.run_once_at if self.instance else None)
            if not run_once:
                raise serializers.ValidationError({'run_once_at': 'Execution time cannot be empty'})
            if run_once <= timezone.now():
                raise serializers.ValidationError({'run_once_at': 'Execution time has expired, please set a future time'})
        return attrs


class ScriptTaskExecutionSerializer(CustomModelSerializer):
    """ScriptScheduled taskExecutionrecordSerializationserver"""
    task_name = serializers.CharField(source='task.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    trigger_type_display = serializers.CharField(
        source='get_trigger_type_display', read_only=True
    )
    creator_name = serializers.CharField(source='creator.username', read_only=True, default='')
    host_summary = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ScriptTaskExecution
        fields = [
            'id', 'task', 'task_name', 'status', 'status_display',
            'start_time', 'end_time', 'duration',
            'trigger_type', 'trigger_type_display',
            'result', 'error_message', 'executed_hosts',
            'host_summary',
            'creator_name', 'create_datetime',
        ]

    def get_host_summary(self, obj) -> dict:
        total = len(obj.executed_hosts) if obj.executed_hosts else 0
        success = 0
        failed = 0
        running = 0
        pending = 0
        batch_id = None
        if isinstance(obj.result, dict):
            batch_id = obj.result.get('batch_id')
        if batch_id:
            from taurus.models import OpsExecution
            from django.db.models import Count
            counts = OpsExecution.objects.filter(batch_id=batch_id).values('status').annotate(c=Count('id'))
            for row in counts:
                st = row.get('status')
                c = int(row.get('c', 0))
                if st == 2:
                    success += c
                elif st in (3, 4):
                    failed += c
                elif st == 1:
                    running += c
                else:
                    pending += c
            if counts:
                total = sum(r['c'] for r in counts)
        else:
            # When no batch_id, fall back to overall ScriptTaskExecution status
            if obj.status == 2:
                success = total
            elif obj.status == 3:
                failed = total
            elif obj.status == 1:
                running = total
            else:
                pending = total
        return {
            'total': total,
            'success': success,
            'failed': failed,
            'running': running,
            'pending': pending,
        }


# ================================================================
# Script* EE 系列 Thin Wrapper（实现在 taurus_ee.serializers.*）
# 保留原名：taurus/urls.py + views.py 中 `from taurus.serializers import X`
# 所有旧引用保持 0 改动。
#
# 防御性修复：所有 try/except 均引入 _*_OK 标志位，仅在 EE 导入成功时才重新定义类。
# 否则不产生 class 重新定义（这些 Serializer 本就只能在 EE-gated 路径下实例化；
# 若强行实例化会报 NameError，但这比 Meta.model=None 静默抛 AttributeError 好诊断）。
# ================================================================
try:
    from taurus_ee.serializers.script_audit import ScriptAuditSerializer as _EEScriptAuditSerializer
    _EE_SCRIPT_AUDIT_OK = True
except ImportError:
    _EE_SCRIPT_AUDIT_OK = False
    from taurus.ee_fallback import _EEFallbackSerializer as _FBSer
    class _EEScriptAuditSerializer(_FBSer): pass

try:
    from taurus_ee.serializers.script_approval import (
    ScriptApproveSerializer as _EEScriptApproveSerializer,
    ScriptApprovalRuleSerializer as _EEScriptApprovalRuleSerializer,
    ScriptApprovalNodeSerializer as _EEScriptApprovalNodeSerializer,
    ScriptApprovalInstanceSerializer as _EEScriptApprovalInstanceSerializer,
    ScriptApprovalNodeExecutionSerializer as _EEScriptApprovalNodeExecutionSerializer,
    )
    _EE_SCRIPT_APPROVAL_OK = True
except ImportError:
    _EE_SCRIPT_APPROVAL_OK = False
    from taurus.ee_fallback import _EEFallbackSerializer as _FBSer
    class _EEScriptApprovalInstanceSerializer(_FBSer): pass
    class _EEScriptApprovalNodeExecutionSerializer(_FBSer): pass
    class _EEScriptApprovalNodeSerializer(_FBSer): pass
    class _EEScriptApprovalRuleSerializer(_FBSer): pass
    class _EEScriptApproveSerializer(_FBSer): pass

try:
    from taurus_ee.serializers.script_check import ScriptCheckRuleSerializer as _EEScriptCheckRuleSerializer
    _EE_SCRIPT_CHECK_OK = True
except ImportError:
    _EE_SCRIPT_CHECK_OK = False
    from taurus.ee_fallback import _EEFallbackSerializer as _FBSer
    class _EEScriptCheckRuleSerializer(_FBSer): pass

try:
    from taurus_ee.serializers.share_permission import (
    SharePermissionDefSerializer as _EESharePermissionDefSerializer,
    ScriptSharePermissionSerializer as _EEScriptSharePermissionSerializer,
    WorkflowSharePermissionSerializer as _EEWorkflowSharePermissionSerializer,
    SharePermissionBatchCreateSerializer as _EESharePermissionBatchCreateSerializer,
    ShareLinkSerializer as _EEShareLinkSerializer,
    ShareLinkActivateSerializer as _EEShareLinkActivateSerializer,
    ShareLinkAccessLogSerializer as _EEShareLinkAccessLogSerializer,
    )
    _EE_SHARE_PERM_OK = True
except ImportError:
    _EE_SHARE_PERM_OK = False
    from taurus.ee_fallback import _EEFallbackSerializer as _FBSer
    class _EEScriptSharePermissionSerializer(_FBSer): pass
    class _EEShareLinkAccessLogSerializer(_FBSer): pass
    class _EEShareLinkActivateSerializer(_FBSer): pass
    class _EEShareLinkSerializer(_FBSer): pass
    class _EESharePermissionBatchCreateSerializer(_FBSer): pass
    class _EESharePermissionDefSerializer(_FBSer): pass
    class _EEWorkflowSharePermissionSerializer(_FBSer): pass


if _EE_SCRIPT_AUDIT_OK:
    class ScriptAuditSerializer(_EEScriptAuditSerializer):
        """Thin Wrapper → taurus_ee.serializers.script_audit.ScriptAuditSerializer"""
        pass


if _EE_SCRIPT_APPROVAL_OK:
    class ScriptApproveSerializer(_EEScriptApproveSerializer):
        """Thin Wrapper."""
        pass


    class ScriptApprovalRuleSerializer(_EEScriptApprovalRuleSerializer):
        pass


    class ScriptApprovalNodeSerializer(_EEScriptApprovalNodeSerializer):
        pass


    class ScriptApprovalInstanceSerializer(_EEScriptApprovalInstanceSerializer):
        pass


    class ScriptApprovalNodeExecutionSerializer(_EEScriptApprovalNodeExecutionSerializer):
        pass


if _EE_SCRIPT_CHECK_OK:
    class ScriptCheckRuleSerializer(_EEScriptCheckRuleSerializer):
        pass


# ---------------- share permission Thin Wrappers ----------------
if _EE_SHARE_PERM_OK:
    class SharePermissionDefSerializer(_EESharePermissionDefSerializer):
        pass


    class ScriptSharePermissionSerializer(_EEScriptSharePermissionSerializer):
        pass


    class WorkflowSharePermissionSerializer(_EEWorkflowSharePermissionSerializer):
        pass


    class SharePermissionBatchCreateSerializer(_EESharePermissionBatchCreateSerializer):
        pass


    class ShareLinkSerializer(_EEShareLinkSerializer):
        pass


    class ShareLinkActivateSerializer(_EEShareLinkActivateSerializer):
        pass


    class ShareLinkAccessLogSerializer(_EEShareLinkAccessLogSerializer):
        pass


# ================================================================
# 历史遗留：以下 ScriptApproveSerializer 的原始定义（仅 Meta 一段）被上面
# Thin Wrapper 提前覆盖，此处不再保留，避免与继承 EE 的版本双定义。
# 原 class ScriptApproveSerializer -> model = ScriptApprove 由 EE Meta 继承保证。
# ================================================================
if _EE_SCRIPT_APPROVAL_OK:
    class ScriptApproveSerializer(_EEScriptApproveSerializer):  # noqa: F811
        """(Double re-declare 仅兼容，与上方 Thin Wrapper 等价。)"""
    # 保留 Meta 注释以展示老代码模型
    # model = ScriptApprove (已在 EE 版 Meta 中定义)
    pass


class _LegacyMetaAnchor_ScriptApprove:  # Never used
    """占位符：防后续 Edit old_string 被截断。"""
    model = None


# ============================================================================
# 以下 Script* / Share* 原始 Serializer 定义已全部迁移到 taurus_ee.serializers 子包，
# 本段老代码（L2213~L2385）被上方 Thin Wrapper 覆盖，仅保留注释占位：
#
#   - ScriptApproveSerializer（原 L2129-2152）
#   - ScriptApprovalRuleSerializer（原 L2154-2168）
#   - ScriptApprovalNodeSerializer（原 L2171-2187）
#   - ScriptApprovalInstanceSerializer（原 L2190-2268）
#   - ScriptApprovalNodeExecutionSerializer（原 L2270-2298）
#   - ScriptCheckRuleSerializer（原 L2301-2315）
#   - SharePermissionDefSerializer ~ ShareLinkAccessLogSerializer × 9 类
#
# 对应 Thin Wrapper 已在文件本区段顶部定义完毕。
# ============================================================================
# ========================== Workflow Orchestration Approval Record Serializers ==========================
#
# [M2.2 Thin Wrapper] 原 9 类（WorkflowApproveCompatSerializer + WorkflowApprove*4 + WorkflowApproval*4）
# 已迁入 taurus_ee.serializers.workflow_approval。这里保持原名（保证 100% 兼容），以 Thin Wrapper
# pass 继承 EE 实现。
# ---------------------------------------------------------------------------
try:
    from taurus_ee.serializers.workflow_approval import (
    WorkflowApproveSerializer as _EEWorkflowApproveSerializer,
    WorkflowApproveCreateSerializer as _EEWorkflowApproveCreateSerializer,
    WorkflowApproveApproveSerializer as _EEWorkflowApproveApproveSerializer,
    WorkflowApproveRejectSerializer as _EEWorkflowApproveRejectSerializer,
    WorkflowApproveCompatSerializer as _EEWorkflowApproveCompatSerializer,
    WorkflowApprovalRuleSerializer as _EEWorkflowApprovalRuleSerializer,
    WorkflowApprovalNodeSerializer as _EEWorkflowApprovalNodeSerializer,
    WorkflowApprovalInstanceSerializer as _EEWorkflowApprovalInstanceSerializer,
    WorkflowApprovalNodeExecutionSerializer as _EEWorkflowApprovalNodeExecutionSerializer,
    )
    _EE_WF_APPROVAL_OK = True
except ImportError:
    _EE_WF_APPROVAL_OK = False
    from taurus.ee_fallback import _EEFallbackSerializer as _FBSer
    class _EEWorkflowApprovalInstanceSerializer(_FBSer): pass
    class _EEWorkflowApprovalNodeExecutionSerializer(_FBSer): pass
    class _EEWorkflowApprovalNodeSerializer(_FBSer): pass
    class _EEWorkflowApprovalRuleSerializer(_FBSer): pass
    class _EEWorkflowApproveApproveSerializer(_FBSer): pass
    class _EEWorkflowApproveCompatSerializer(_FBSer): pass
    class _EEWorkflowApproveCreateSerializer(_FBSer): pass
    class _EEWorkflowApproveRejectSerializer(_FBSer): pass
    class _EEWorkflowApproveSerializer(_FBSer): pass


if _EE_WF_APPROVAL_OK:
    class WorkflowApproveSerializer(_EEWorkflowApproveSerializer): pass
    class WorkflowApproveCreateSerializer(_EEWorkflowApproveCreateSerializer): pass
    class WorkflowApproveApproveSerializer(_EEWorkflowApproveApproveSerializer): pass
    class WorkflowApproveRejectSerializer(_EEWorkflowApproveRejectSerializer): pass
    class WorkflowApproveCompatSerializer(_EEWorkflowApproveCompatSerializer): pass
    class WorkflowApprovalRuleSerializer(_EEWorkflowApprovalRuleSerializer): pass
    class WorkflowApprovalNodeSerializer(_EEWorkflowApprovalNodeSerializer): pass
    class WorkflowApprovalInstanceSerializer(_EEWorkflowApprovalInstanceSerializer): pass
    class WorkflowApprovalNodeExecutionSerializer(_EEWorkflowApprovalNodeExecutionSerializer): pass


# ============================================================
# Share functionality related serializers
# ============================================================
# （Thin Wrapper 已在文件顶部定义：SharePermissionDef / ScriptSharePermission /
# WorkflowSharePermission / SharePermissionBatchCreate / ShareLink /
# ShareLinkActivate / ShareLinkAccessLog × 8 类。
# 本段原老代码（约 220 行）已迁移至 taurus_ee.serializers.share_permission，不再重复。）
#
class TaskCenterItemSerializer(_EETaskCenterItemSerializer):
    """Thin Wrapper — EE impl: taurus_ee.serializers.log_ext_center._EETaskCenterItemSerializer"""
    pass


# ---------------------------------------------------------------------------
# [M2.3 Thin Wrapper] 调度 HA / 告警 / 统一双轨（SCHEDULE_HA_CLUSTER / ALERT / UNIFIED）
# Serializers 实际实现位于 taurus_ee.serializers.scheduler_ha；此处保留原名占位，
# 避免 CE/EE 侧对 taurus.serializers.* 的 import 断裂。FeatureGate 在使用侧挂。
# ---------------------------------------------------------------------------
try:
    from taurus_ee.serializers.scheduler_ha import (
    ScheduleExecutionHASerializer as _EEScheduleExecutionHASerializer,
    ScriptTaskExecutionUnifiedSerializer as _EEScriptTaskExecutionUnifiedSerializer,
    SchedulerAlertRuleSerializer as _EESchedulerAlertRuleSerializer,
    UnifiedScheduleListRequestSerializer as _EEUnifiedScheduleListRequestSerializer,
    UnifiedScheduleStatsSerializer as _EEUnifiedScheduleStatsSerializer,
    SchedulerAlertEventSerializer as _EESchedulerAlertEventSerializer,
    )
    _EE_SCHEDULER_HA_OK = True
except ImportError:
    _EE_SCHEDULER_HA_OK = False
    from taurus.ee_fallback import _EEFallbackSerializer as _FBSer
    class _EEScheduleExecutionHASerializer(_FBSer): pass
    class _EESchedulerAlertEventSerializer(_FBSer): pass
    class _EESchedulerAlertRuleSerializer(_FBSer): pass
    class _EEScriptTaskExecutionUnifiedSerializer(_FBSer): pass
    class _EEUnifiedScheduleListRequestSerializer(_FBSer): pass
    class _EEUnifiedScheduleStatsSerializer(_FBSer): pass


if _EE_SCHEDULER_HA_OK:
    class ScheduleExecutionHASerializer(_EEScheduleExecutionHASerializer):
        """Thin Wrapper — EE 实现: taurus_ee.serializers.scheduler_ha"""
        pass


    class ScriptTaskExecutionUnifiedSerializer(_EEScriptTaskExecutionUnifiedSerializer):
        """Thin Wrapper — EE 实现: taurus_ee.serializers.scheduler_ha"""
        pass


    class SchedulerAlertRuleSerializer(_EESchedulerAlertRuleSerializer):
        """Thin Wrapper — EE 实现: taurus_ee.serializers.scheduler_ha"""
        pass


    class UnifiedScheduleListRequestSerializer(_EEUnifiedScheduleListRequestSerializer):
        """Thin Wrapper — EE 实现: taurus_ee.serializers.scheduler_ha"""
        pass


    class UnifiedScheduleStatsSerializer(_EEUnifiedScheduleStatsSerializer):
        """Thin Wrapper — EE 实现: taurus_ee.serializers.scheduler_ha"""
        pass


    class SchedulerAlertEventSerializer(_EESchedulerAlertEventSerializer):
        """Thin Wrapper — EE 实现: taurus_ee.serializers.scheduler_ha"""
        pass


# ContactLead & 6 Placeholder 与 M2.5 log_ext_center 同组导入，共用 _EE_LOG_EXT_OK
if _EE_LOG_EXT_OK:
    class TaskCenterItemSerializer(_EETaskCenterItemSerializer):
        """Thin Wrapper — EE impl: taurus_ee.serializers.log_ext_center._EETaskCenterItemSerializer"""
        pass


    class ContactLeadSerializer(_EEContactLeadSerializer):
        """Thin Wrapper — EE impl: taurus_ee.serializers.log_ext_center._EEContactLeadSerializer"""
        pass


    # -------- 6 extension center placeholder Ser (M2.5 EE 空壳) --------
    class KnowledgeBasePlaceholderSerializer(_EEKnowledgeBasePlaceholderSerializer):
        """Thin Wrapper — 知识库占位（M2.5 EE 空壳）"""
        pass


    class InspectionCenterPlaceholderSerializer(_EEInspectionCenterPlaceholderSerializer):
        """Thin Wrapper — 巡检中心占位（M2.5 EE 空壳）"""
        pass


    class ToolsCenterPlaceholderSerializer(_EEToolsCenterPlaceholderSerializer):
        """Thin Wrapper — 工具中心占位（M2.5 EE 空壳）"""
        pass


    class BackupRestoreCenterPlaceholderSerializer(_EEBackupRestoreCenterPlaceholderSerializer):
        """Thin Wrapper — 备份恢复中心占位（M2.5 EE 空壳）"""
        pass


    class DownloadCenterPlaceholderSerializer(_EEDownloadCenterPlaceholderSerializer):
        """Thin Wrapper — 客户端打包下载中心占位（M2.5 EE 空壳）"""
        pass