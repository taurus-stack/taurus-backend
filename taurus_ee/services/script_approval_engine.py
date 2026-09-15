"""Script approval services (EE only):
  - ApprovalRuleMatcher: 按条件组匹配最合适的审批规则
  - ApprovalFlowEngine: 创建审批实例 + 节点推进（approve/reject/delegate/add-sign/cancel）

原位置：taurus/views.py L9177-L9660（两个 class 直接迁移，保持算法稳定）。
"""
from __future__ import annotations


class ApprovalRuleMatcher:
    """审核规则 match 服务。"""

    @classmethod
    def match(cls, script, submitter):
        from taurus.models import ScriptApprovalRule

        rules = ScriptApprovalRule.objects.filter(is_active=True).order_by('priority')
        for rule in rules:
            if cls._check_rule(rule, script, submitter):
                return rule
        return None

    @classmethod
    def _check_rule(cls, rule, script, submitter):
        condition_groups = rule.condition_groups or []
        if not condition_groups:
            return True

        for group in condition_groups:
            if cls._check_condition_group(group, script, submitter):
                return True
        return False

    @classmethod
    def _check_condition_group(cls, group, script, submitter):
        if 'category_ids' in group and group['category_ids']:
            all_category_ids = cls._get_all_child_category_ids(group['category_ids'])
            if script.category_id not in all_category_ids:
                return False

        if 'script_types' in group and group['script_types']:
            if script.script_type not in group['script_types']:
                return False

        if 'risk_levels' in group and group['risk_levels']:
            risk_level, _ = cls._evaluate_risk(script)
            if risk_level not in group['risk_levels']:
                return False

        if 'min_risk_points' in group and group['min_risk_points']:
            _, risk_points = cls._evaluate_risk(script)
            if len(risk_points) < group['min_risk_points']:
                return False

        if 'auth_types' in group and group['auth_types']:
            if script.auth_type not in group['auth_types']:
                return False

        if 'submitter_roles' in group and group['submitter_roles']:
            if not hasattr(submitter, 'roles'):
                return False
            user_roles = set(submitter.roles.values_list('code', flat=True))
            if not user_roles & set(group['submitter_roles']):
                return False

        return True

    @classmethod
    def _get_all_child_category_ids(cls, parent_ids):
        from taurus.models import ScriptCategory
        result = set(parent_ids)
        categories = list(ScriptCategory.objects.all().values('id', 'parent_id'))
        changed = True
        while changed:
            changed = False
            for cat in categories:
                if cat['parent_id'] in result and cat['id'] not in result:
                    result.add(cat['id'])
                    changed = True
        return list(result)

    @classmethod
    def _evaluate_risk(cls, script):
        risk_points = []
        try:
            from taurus.script_checker import ScriptCheckService
            result = ScriptCheckService.check(
                script.content or '',
                script.script_type or ''
            )
            result.calculate_risk_level()
            risk_level = result.risk_level
            for issue in result.issues:
                severity_prefix = {
                    'error': '[high]', 'warning': '[warning]',
                    'info': '[tip]', 'style': '[Style]'
                }.get(issue.severity, '')
                line_info = f'Line {issue.line}' if issue.line else ''
                msg_parts = [p for p in [severity_prefix, line_info, issue.message] if p]
                point_desc = ' '.join(msg_parts)
                if issue.fix_suggestion and issue.severity == 'error':
                    point_desc += f'(suggestion: {issue.fix_suggestion})'
                risk_points.append(point_desc)
        except Exception:  # noqa: BLE001
            content = script.content or ''
            content_lower = content.lower()
            fallback_patterns = [
                ('rm -rf', 'Contains rm -rf forced delete'),
                ('sudo', 'Contains sudo privilege escalation'),
                ('mkfs', 'Contains disk format command'),
                ('dd if=', 'Contains dd disk operation'),
                ('> /dev/sd', 'Contains direct disk write'),
                ('shutdown', 'Contains shutdown command'),
                ('reboot', 'Contains reboot command'),
                ('drop table', 'Contains DROP TABLE'),
                ('drop database', 'Contains DROP DATABASE'),
                ('delete from', 'Contains DELETE data'),
            ]
            for pattern, desc in fallback_patterns:
                if pattern in content_lower:
                    risk_points.append(desc)
            if len(risk_points) >= 3:
                risk_level = 'high'
            elif len(risk_points) >= 1:
                risk_level = 'medium'
            else:
                risk_level = 'low'

        return risk_level, risk_points


# ---------------------------------------------------------------------------
# ApprovalFlowEngine
# ---------------------------------------------------------------------------
class ApprovalFlowEngine:
    """脚本审批流程执行引擎。"""

    @classmethod
    def start_approval(cls, script, submitter, submit_desc=''):
        from taurus.models import (
            ScriptApprovalInstance, ScriptApprovalNodeExecution,
        )

        rule = ApprovalRuleMatcher.match(script, submitter)
        risk_level, risk_points = ApprovalRuleMatcher._evaluate_risk(script)

        if rule is None:
            # Script 标记了"modify需审核"时即使没匹配到规则也要创建默认审批实例
            if getattr(script, 'need_audit', False):
                instance = ScriptApprovalInstance.objects.create(
                    script=script,
                    script_version=script.current_version,
                    rule=None,
                    rule_name='Default approval (need_audit)',
                    status='pending',
                    current_node_index=0,
                    submitter=submitter,
                    submitter_name=submitter.username,
                    submit_desc=submit_desc,
                    risk_level=risk_level,
                    risk_points=risk_points,
                )
                candidate_approvers = cls._get_default_admin_approvers()
                ScriptApprovalNodeExecution.objects.create(
                    instance=instance,
                    node_name='Admin approval',
                    approver_type='specific_users',
                    approver_config={},
                    approval_mode='any',
                    step_order=1,
                    candidate_approvers=candidate_approvers,
                    status='pending',
                )
                script.status = 2
                script.save()
                return instance
            script.status = 0
            script.save()
            return None

        instance = ScriptApprovalInstance.objects.create(
            script=script,
            script_version=script.current_version,
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

        nodes = rule.nodes.order_by('step_order')
        for node in nodes:
            candidate_approvers = cls._get_candidate_approvers(node, script, submitter)
            ScriptApprovalNodeExecution.objects.create(
                instance=instance,
                node_name=node.node_name,
                approver_type=node.approver_type,
                approver_config=node.approver_config or {},
                approval_mode=node.approval_mode,
                step_order=node.step_order,
                candidate_approvers=candidate_approvers,
                status='pending',
            )

        script.status = 2
        script.save()
        return instance

    @classmethod
    def _get_default_admin_approvers(cls):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        approvers = []
        for user in User.objects.filter(is_superuser=True):
            approvers.append({
                'user_id': user.id,
                'username': user.username,
                'name': getattr(user, 'name', user.username),
            })
        return approvers

    @classmethod
    def _get_candidate_approvers(cls, node, script, submitter):
        approvers = []

        if node.approver_type == 'category_reviewer':
            if script.category_id:
                from taurus.models import ScriptCategory
                try:
                    category = ScriptCategory.objects.get(id=script.category_id)
                    for user in category.reviewers.all():
                        approvers.append({
                            'user_id': user.id,
                            'username': user.username,
                            'name': getattr(user, 'name', user.username),
                        })
                except ScriptCategory.DoesNotExist:
                    pass

        elif node.approver_type == 'specific_users':
            user_ids = (node.approver_config or {}).get('user_ids', [])
            from django.contrib.auth import get_user_model
            User = get_user_model()
            for user in User.objects.filter(id__in=user_ids):
                approvers.append({
                    'user_id': user.id,
                    'username': user.username,
                    'name': getattr(user, 'name', user.username),
                })

        elif node.approver_type == 'role':
            role_codes = (node.approver_config or {}).get('role_codes', [])
            from django.contrib.auth import get_user_model
            User = get_user_model()
            users = User.objects.filter(roles__code__in=role_codes).distinct()
            for user in users:
                approvers.append({
                    'user_id': user.id,
                    'username': user.username,
                    'name': getattr(user, 'name', user.username),
                })

        elif node.approver_type == 'submitter_manager':
            levels = (node.approver_config or {}).get('levels', 1)
            manager = cls._get_manager_by_levels(submitter, levels)
            if manager:
                approvers.append({
                    'user_id': manager.id,
                    'username': manager.username,
                    'name': getattr(manager, 'name', manager.username),
                })

        return approvers

    @classmethod
    def _get_manager_by_levels(cls, user, levels):
        current = user
        for _ in range(levels):
            manager = getattr(current, 'manager', None)
            if manager is None:
                return None
            current = manager
        return current if current != user else None

    @classmethod
    def _is_user_approver(cls, node_exec, user):
        candidate_ids = [a['user_id'] for a in (node_exec.candidate_approvers or [])]
        return user.id in candidate_ids

    @classmethod
    def approve(cls, instance_id, approver, reason=''):
        from taurus.models import ScriptApprovalInstance
        from django.utils import timezone

        instance = ScriptApprovalInstance.objects.get(id=instance_id)
        if instance.status != 'pending':
            raise ValueError('This approval already processed')

        nodes = list(instance.node_executions.order_by('step_order'))
        if instance.current_node_index >= len(nodes):
            raise ValueError('Approval workflow completed')

        node_exec = nodes[instance.current_node_index]
        if not cls._is_user_approver(node_exec, approver):
            raise ValueError('Not approver for this node')

        for record in node_exec.approval_records:
            if record.get('user_id') == approver.id and record.get('action') in ('approve', 'reject'):
                raise ValueError('Already processed this node')

        node_exec.approval_records.append({
            'user_id': approver.id,
            'username': approver.username,
            'action': 'approve',
            'reason': reason,
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
                instance.script.status = 0
                instance.script.save()
            else:
                instance.current_node_index = next_index
                instance.save()
        else:
            node_exec.save()

        return instance

    @classmethod
    def reject(cls, instance_id, approver, reason=''):
        from taurus.models import ScriptApprovalInstance
        from django.utils import timezone

        instance = ScriptApprovalInstance.objects.get(id=instance_id)
        if instance.status != 'pending':
            raise ValueError('This approval already processed')

        nodes = list(instance.node_executions.order_by('step_order'))
        if instance.current_node_index >= len(nodes):
            raise ValueError('Approval workflow completed')

        node_exec = nodes[instance.current_node_index]
        if not cls._is_user_approver(node_exec, approver):
            raise ValueError('Not approver for this node')

        for record in node_exec.approval_records:
            if record.get('user_id') == approver.id and record.get('action') in ('approve', 'reject'):
                raise ValueError('Already processed this node')

        node_exec.approval_records.append({
            'user_id': approver.id,
            'username': approver.username,
            'action': 'reject',
            'reason': reason,
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
    def _is_node_approved(cls, node_exec):
        records = node_exec.approval_records or []
        approve_count = sum(1 for r in records if r.get('action') == 'approve')
        candidate_count = len(node_exec.candidate_approvers or [])

        if node_exec.approval_mode == 'first':
            return approve_count >= 1
        elif node_exec.approval_mode == 'any':
            return approve_count >= 1
        elif node_exec.approval_mode == 'all':
            return approve_count >= candidate_count and candidate_count > 0
        return False

    @classmethod
    def delegate(cls, instance_id, from_user, to_user_id, reason=''):
        from taurus.models import ScriptApprovalInstance
        from django.contrib.auth import get_user_model
        from django.utils import timezone

        User = get_user_model()
        instance = ScriptApprovalInstance.objects.get(id=instance_id)
        if instance.status != 'pending':
            raise ValueError('This approval already processed')

        nodes = list(instance.node_executions.order_by('step_order'))
        node_exec = nodes[instance.current_node_index]
        if not cls._is_user_approver(node_exec, from_user):
            raise ValueError('Not approver for this node')

        try:
            to_user = User.objects.get(id=to_user_id)
        except User.DoesNotExist:
            raise ValueError('Target user not found')

        node_exec.delegate_records.append({
            'from_user_id': from_user.id,
            'from_username': from_user.username,
            'to_user_id': to_user.id,
            'to_username': to_user.username,
            'reason': reason,
            'operate_time': timezone.now().isoformat(),
        })

        new_approver = {
            'user_id': to_user.id,
            'username': to_user.username,
            'name': getattr(to_user, 'name', to_user.username),
        }
        candidate_ids = [a['user_id'] for a in node_exec.candidate_approvers]
        if to_user.id not in candidate_ids:
            node_exec.candidate_approvers.append(new_approver)

        node_exec.save()
        return instance

    @classmethod
    def add_sign(cls, instance_id, operator, added_user_ids, reason=''):
        from taurus.models import ScriptApprovalInstance
        from django.contrib.auth import get_user_model
        from django.utils import timezone

        User = get_user_model()
        instance = ScriptApprovalInstance.objects.get(id=instance_id)
        if instance.status != 'pending':
            raise ValueError('This approval already processed')

        nodes = list(instance.node_executions.order_by('step_order'))
        node_exec = nodes[instance.current_node_index]
        if not cls._is_user_approver(node_exec, operator):
            raise ValueError('Not approver for this node')

        added_users = []
        for user in User.objects.filter(id__in=added_user_ids):
            added_users.append({
                'user_id': user.id,
                'username': user.username,
                'name': getattr(user, 'name', user.username),
            })

        node_exec.add_sign_records.append({
            'operator_id': operator.id,
            'operator_username': operator.username,
            'added_user_ids': added_user_ids,
            'reason': reason,
            'operate_time': timezone.now().isoformat(),
        })

        candidate_ids = [a['user_id'] for a in node_exec.candidate_approvers]
        for user_info in added_users:
            if user_info['user_id'] not in candidate_ids:
                node_exec.candidate_approvers.append(user_info)

        node_exec.save()
        return instance

    @classmethod
    def cancel(cls, instance_id, operator):
        from taurus.models import ScriptApprovalInstance
        from django.utils import timezone

        instance = ScriptApprovalInstance.objects.get(id=instance_id)
        if instance.status != 'pending':
            raise ValueError('This approval already processed')

        if instance.submitter_id != operator.id:
            raise ValueError('Only submitter can withdraw')

        instance.status = 'cancelled'
        instance.finish_time = timezone.now()
        instance.save()

        instance.script.status = 1
        instance.script.save()

        return instance
