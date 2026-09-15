"""Workflow approval engines (EE-only)。

原位置：taurus/views.py L9215-L9833。
  1. WorkflowApprovalRuleMatcher   — 9 条件组（含 category_ids / workflow_modes / risk_levels /
                                    min_risk_points / auth_types / submitter_roles /
                                    workflow_ids / script_ids / tags）
  2. WorkflowApprovalFlowEngine    — start_approval 含 override_approvers（approver_ids + countersign_ids）
                                    支持 4 级兜底 need_audit / public / category Approvers
"""
from __future__ import annotations


class WorkflowApprovalRuleMatcher:
    """Workflow 审核规则 match 服务。"""

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
            if workflow.category_id not in cls._get_all_child_category_ids(group['category_ids']):
                return False
        if 'workflow_modes' in group and group['workflow_modes']:
            if workflow.workflow_mode not in group['workflow_modes']:
                return False
        if 'risk_levels' in group and group['risk_levels']:
            from taurus.views import WorkflowViewSet
            risk_level, _ = WorkflowViewSet._evaluate_workflow_risk(workflow)
            if risk_level not in group['risk_levels']:
                return False
        if 'min_risk_points' in group and group['min_risk_points']:
            from taurus.views import WorkflowViewSet
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
            required = set(int(x) for x in group['script_ids'])
            template_ids = set(
                workflow.steps.values_list('template_id', flat=True)
            ) if hasattr(workflow, 'steps') else set()
            if not (required & template_ids):
                return False
        if 'tags' in group and group['tags']:
            tag_list = [str(t).strip().lower() for t in group['tags'] if str(t).strip()]
            if tag_list:
                wf_tags: set = set()
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


class WorkflowApprovalFlowEngine:
    """Workflow 审核流程执行引擎。"""

    @classmethod
    def start_approval(cls, workflow, submitter, submit_desc='', override_approvers=None):
        from django.contrib.auth import get_user_model as _gum
        from taurus.models import (
            WorkflowApprovalInstance, WorkflowApprovalNodeExecution,
        )

        def _build_user_candidates(uid_list):
            if not uid_list:
                return []
            U = _gum()
            clean = []
            for x in uid_list:
                try:
                    clean.append(int(x))
                except (ValueError, TypeError):
                    pass
            if not clean:
                return []
            users = list(U.objects.filter(id__in=clean))
            by_id = {u.id: u for u in users}
            ordered = []
            for uid in clean:
                u = by_id.get(uid)
                if u:
                    ordered.append({
                        'user_id': u.id,
                        'username': u.username,
                        'name': getattr(u, 'name', '') or '',
                    })
            return ordered

        def _extract_graph_approvers(wf):
            graph = getattr(wf, 'graph_definition', None) or {}
            nodes = graph.get('nodes') or []
            approver_ids, approval_mode = [], 'any'
            for node in nodes:
                if str(node.get('node_type', '')) == 'approval':
                    params = node.get('params') or {}
                    try:
                        approver_ids.append(int(params.get('approver_user_id')))
                    except (ValueError, TypeError):
                        pass
                    mode = params.get('mode')
                    if mode in ('any', 'all', 'first'):
                        approval_mode = mode
            return approver_ids, approval_mode

        from taurus.views import WorkflowViewSet
        rule = WorkflowApprovalRuleMatcher.match(workflow, submitter)
        risk_level, risk_points = WorkflowViewSet._evaluate_workflow_risk(workflow)

        def _is_public_category(cat):
            if not cat:
                return False
            cur, visited = cat, set()
            while cur and getattr(cur, 'id', None) not in visited:
                visited.add(cur.id)
                if getattr(cur, 'name', None) == 'Public workflow':
                    return True
                cur = getattr(cur, 'parent', None)
            return False

        def _create_custom_node(instance, override):
            any_ids = list((override or {}).get('approver_ids') or [])
            all_ids = list((override or {}).get('countersign_ids') or [])
            raw_mode = (override or {}).get('approval_mode') or 'any'
            effective_mode = 'all' if all_ids else ('any' if raw_mode == 'any' else 'all')
            any_cand = _build_user_candidates(any_ids)
            all_cand = _build_user_candidates(all_ids)
            step = 0
            if not any_cand and not all_cand:
                custom_ids = list(getattr(workflow, 'custom_approver_ids', None) or [])
                if custom_ids:
                    fallback = _build_user_candidates(custom_ids)
                    if fallback:
                        any_cand = fallback
                if not any_cand:
                    gids, gmode = _extract_graph_approvers(workflow)
                    if gids:
                        cands = _build_user_candidates(gids)
                        if cands:
                            any_cand = cands
                            effective_mode = gmode if gmode in ('any', 'all') else 'any'
            if any_cand:
                step += 1
                WorkflowApprovalNodeExecution.objects.create(
                    instance=instance,
                    node_name='Any one approver' if override else 'Specific approver approval',
                    approver_type='specific_users',
                    approver_config={'user_ids': [u['user_id'] for u in any_cand]},
                    approval_mode='any',
                    step_order=step,
                    candidate_approvers=any_cand,
                    status='pending',
                )
            if all_cand:
                step += 1
                WorkflowApprovalNodeExecution.objects.create(
                    instance=instance,
                    node_name='All approvers' if override else 'Joint approval',
                    approver_type='specific_users',
                    approver_config={'user_ids': [u['user_id'] for u in all_cand]},
                    approval_mode='all',
                    step_order=step,
                    candidate_approvers=all_cand,
                    status='pending',
                )
            final_mode = 'all' if all_cand else effective_mode
            if step == 0:
                cls._create_fallback_admin_node(instance)
            try:
                instance.approval_mode = final_mode
                instance.save(update_fields=['approval_mode'])
            except Exception:
                pass

        if rule is None:
            need = (
                getattr(workflow, 'need_audit', False)
                or getattr(workflow, 'auth_type', None) == 'public'
                or _is_public_category(getattr(workflow, 'category', None))
            )
            if need:
                instance = WorkflowApprovalInstance.objects.create(
                    workflow=workflow,
                    workflow_version=str(getattr(workflow, 'dag_published_version_id', '') or ''),
                    rule=None,
                    rule_name='Custom approval (need_audit / public flow)',
                    status='pending',
                    current_node_index=0,
                    submitter=submitter,
                    submitter_name=submitter.username,
                    submit_desc=submit_desc,
                    risk_level=risk_level,
                    risk_points=risk_points,
                )
                _create_custom_node(instance, override_approvers)
                workflow.status = 2
                workflow.save()
                return instance
            workflow.status = 0
            workflow.save()
            return None

        instance = WorkflowApprovalInstance.objects.create(
            workflow=workflow,
            workflow_version=str(getattr(workflow, 'dag_published_version_id', '') or ''),
            rule=rule,
            rule_name=rule.name,
            status='pending',
            current_node_index=0,
            submitter=submitter,
            submitter_name=submitter.username,
            submit_desc=submit_desc,
            risk_level=risk_level,
            risk_points=risk_points,
        )
        if override_approvers and (
            (override_approvers.get('approver_ids') and len(override_approvers['approver_ids']) > 0)
            or (override_approvers.get('countersign_ids') and len(override_approvers['countersign_ids']) > 0)
        ):
            _create_custom_node(instance, override_approvers)
        else:
            for node in rule.nodes.order_by('step_order'):
                candidates = cls._get_candidate_approvers(node, workflow, submitter)
                WorkflowApprovalNodeExecution.objects.create(
                    instance=instance,
                    node_name=node.node_name,
                    approver_type=node.approver_type,
                    approver_config=node.approver_config or {},
                    approval_mode=node.approval_mode,
                    step_order=node.step_order,
                    candidate_approvers=candidates,
                    status='pending',
                )
        workflow.status = 2
        workflow.save()
        return instance

    @classmethod
    def _create_fallback_admin_node(cls, instance):
        from taurus.models import WorkflowApprovalNodeExecution
        WorkflowApprovalNodeExecution.objects.create(
            instance=instance,
            node_name='Admin approval',
            approver_type='specific_users',
            approver_config={},
            approval_mode='any',
            step_order=1,
            candidate_approvers=cls._get_default_admin_approvers(),
            status='pending',
        )

    @classmethod
    def _get_default_admin_approvers(cls):
        from django.contrib.auth import get_user_model
        U = get_user_model()
        return [
            {'user_id': u.id, 'username': u.username,
             'name': getattr(u, 'name', u.username)}
            for u in U.objects.filter(is_superuser=True)
        ]

    @classmethod
    def _get_candidate_approvers(cls, node, workflow, submitter):
        approvers = []
        if node.approver_type == 'category_reviewer' and workflow.category_id:
            from taurus.models import WorkflowCategory
            try:
                for user in WorkflowCategory.objects.get(id=workflow.category_id).reviewers.all():
                    approvers.append({
                        'user_id': user.id, 'username': user.username,
                        'name': getattr(user, 'name', user.username),
                    })
            except WorkflowCategory.DoesNotExist:
                pass
        elif node.approver_type == 'specific_users':
            user_ids = (node.approver_config or {}).get('user_ids', [])
            from django.contrib.auth import get_user_model
            for user in get_user_model().objects.filter(id__in=user_ids):
                approvers.append({
                    'user_id': user.id, 'username': user.username,
                    'name': getattr(user, 'name', user.username),
                })
        elif node.approver_type == 'role':
            role_codes = (node.approver_config or {}).get('role_codes', [])
            from django.contrib.auth import get_user_model
            U = get_user_model()
            for user in U.objects.filter(roles__code__in=role_codes).distinct():
                approvers.append({
                    'user_id': user.id, 'username': user.username,
                    'name': getattr(user, 'name', user.username),
                })
        elif node.approver_type == 'submitter_manager':
            levels = (node.approver_config or {}).get('levels', 1)
            mgr = cls._get_manager_by_levels(submitter, levels)
            if mgr:
                approvers.append({
                    'user_id': mgr.id, 'username': mgr.username,
                    'name': getattr(mgr, 'name', mgr.username),
                })
        return approvers

    @classmethod
    def _get_manager_by_levels(cls, user, levels):
        cur = user
        for _ in range(levels):
            mgr = getattr(cur, 'manager', None)
            if mgr is None:
                return None
            cur = mgr
        return cur if cur != user else None

    @classmethod
    def _is_user_approver(cls, node_exec, user):
        candidate_ids = [a['user_id'] for a in (node_exec.candidate_approvers or [])]
        return user.id in candidate_ids

    @classmethod
    def _is_node_approved(cls, node_exec):
        records = node_exec.approval_records or []
        approve_cnt = sum(1 for r in records if r.get('action') == 'approve')
        candidate_cnt = len(node_exec.candidate_approvers or [])
        if node_exec.approval_mode in ('first', 'any'):
            return approve_cnt >= 1
        if node_exec.approval_mode == 'all':
            return approve_cnt >= candidate_cnt and candidate_cnt > 0
        return False

    @classmethod
    def approve(cls, instance_id, approver, reason=''):
        from django.utils import timezone
        from taurus.models import WorkflowApprovalInstance
        instance = WorkflowApprovalInstance.objects.get(id=instance_id)
        if instance.status != 'pending':
            raise ValueError('This approval already processed')
        nodes = list(instance.node_executions.order_by('step_order'))
        if instance.current_node_index >= len(nodes):
            raise ValueError('Approval workflow completed')
        node_exec = nodes[instance.current_node_index]
        if not cls._is_user_approver(node_exec, approver):
            raise ValueError('Not approver for this node')
        for rec in node_exec.approval_records:
            if rec.get('user_id') == approver.id and rec.get('action') in ('approve', 'reject'):
                raise ValueError('Already processed this node')
        node_exec.approval_records.append({
            'user_id': approver.id, 'username': approver.username,
            'action': 'approve', 'reason': reason,
            'operate_time': timezone.now().isoformat(),
        })
        if cls._is_node_approved(node_exec):
            node_exec.status = 'approved'
            node_exec.finish_time = timezone.now()
            node_exec.save()
            next_index = instance.current_node_index + 1
            if next_index >= len(nodes):
                instance.status = 'approved'
                instance.finish_time = timezone.now()
                instance.save()
                instance.workflow.status = 0
                instance.workflow.save()
            else:
                instance.current_node_index = next_index
                instance.save()
        else:
            node_exec.save()
        return instance

    @classmethod
    def reject(cls, instance_id, approver, reason=''):
        from django.utils import timezone
        from taurus.models import WorkflowApprovalInstance
        instance = WorkflowApprovalInstance.objects.get(id=instance_id)
        if instance.status != 'pending':
            raise ValueError('This approval already processed')
        nodes = list(instance.node_executions.order_by('step_order'))
        if instance.current_node_index >= len(nodes):
            raise ValueError('Approval workflow completed')
        node_exec = nodes[instance.current_node_index]
        if not cls._is_user_approver(node_exec, approver):
            raise ValueError('Not approver for this node')
        for rec in node_exec.approval_records:
            if rec.get('user_id') == approver.id and rec.get('action') in ('approve', 'reject'):
                raise ValueError('Already processed this node')
        node_exec.approval_records.append({
            'user_id': approver.id, 'username': approver.username,
            'action': 'reject', 'reason': reason,
            'operate_time': timezone.now().isoformat(),
        })
        node_exec.status = 'rejected'
        node_exec.finish_time = timezone.now()
        node_exec.save()
        instance.status = 'rejected'
        instance.finish_time = timezone.now()
        instance.save()
        return instance

    @classmethod
    def cancel(cls, instance_id, operator):
        from django.utils import timezone
        from taurus.models import WorkflowApprovalInstance
        instance = WorkflowApprovalInstance.objects.get(id=instance_id)
        if instance.status != 'pending':
            raise ValueError('This approval already processed')
        if (instance.submitter_id != operator.id
                and not getattr(operator, 'is_superuser', False)):
            raise ValueError('Only submitter can withdraw')
        instance.status = 'cancelled'
        instance.finish_time = timezone.now()
        instance.save()
        instance.workflow.status = 1
        instance.workflow.save()
        return instance

    @classmethod
    def delegate(cls, instance_id, approver, to_user_id, reason=''):
        from django.utils import timezone
        from django.contrib.auth import get_user_model
        from taurus.models import WorkflowApprovalInstance
        U = get_user_model()
        instance = WorkflowApprovalInstance.objects.get(id=instance_id)
        if instance.status != 'pending':
            raise ValueError('This approval already processed')
        nodes = list(instance.node_executions.order_by('step_order'))
        node_exec = nodes[instance.current_node_index]
        if not cls._is_user_approver(node_exec, approver):
            raise ValueError('Not approver for this node')
        try:
            to_user = U.objects.get(id=to_user_id)
        except U.DoesNotExist:
            raise ValueError('Target user not found')
        node_exec.approval_records.append({
            'user_id': approver.id, 'username': approver.username,
            'action': 'delegate', 'to_user_id': to_user.id,
            'to_username': to_user.username, 'reason': reason,
            'operate_time': timezone.now().isoformat(),
        })
        candidate_ids = [a['user_id'] for a in (node_exec.candidate_approvers or [])]
        if to_user.id not in candidate_ids:
            node_exec.candidate_approvers.append({
                'user_id': to_user.id, 'username': to_user.username,
                'name': getattr(to_user, 'name', to_user.username),
            })
        node_exec.save()
        return instance

    @classmethod
    def add_sign(cls, instance_id, approver, user_ids, reason=''):
        from django.utils import timezone
        from django.contrib.auth import get_user_model
        from taurus.models import WorkflowApprovalInstance
        U = get_user_model()
        instance = WorkflowApprovalInstance.objects.get(id=instance_id)
        if instance.status != 'pending':
            raise ValueError('This approval already processed')
        nodes = list(instance.node_executions.order_by('step_order'))
        node_exec = nodes[instance.current_node_index]
        if not cls._is_user_approver(node_exec, approver):
            raise ValueError('Not approver for this node')
        users = list(U.objects.filter(id__in=user_ids))
        node_exec.approval_records.append({
            'user_id': approver.id, 'username': approver.username,
            'action': 'add_sign', 'user_ids': [u.id for u in users],
            'reason': reason, 'operate_time': timezone.now().isoformat(),
        })
        candidate_ids = set(a['user_id'] for a in (node_exec.candidate_approvers or []))
        for u in users:
            if u.id not in candidate_ids:
                node_exec.candidate_approvers.append({
                    'user_id': u.id, 'username': u.username,
                    'name': getattr(u, 'name', u.username),
                })
        node_exec.save()
        return instance
