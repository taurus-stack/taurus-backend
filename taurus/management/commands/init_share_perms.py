"""
initialize分享PermissionDefinitionDictionary

用法:
  # initializePermission(Idempotency:already exists则UpdateName/Description/Order, 不存在则Create)
  python manage.py init_share_perms

  # ResetPermission(清Empty后re-initialize, 用于修复损badDictionary数据)
  python manage.py init_share_perms --reset

  # 仅initializeScriptPermission
  python manage.py init_share_perms --type script

  # 仅initializeWorkflowPermission
  python manage.py init_share_perms --type workflow
"""

from django.core.management.base import BaseCommand, CommandError
from taurus.models import SharePermissionDef


SCRIPT_PERMS = [
    # ---- 查看class ----
    ('script:view', '查看详情', 'view', '查看脚本基本信息、内容', 10),
    ('script:view_version', '版本历史', 'view', '查看脚本版本历史记录', 20),
    ('script:view_audit', '审计日志', 'view', '查看脚本审计日志', 30),
    ('script:view_exec_history', '执行历史', 'view', '查看脚本执行历史记录', 40),
    # ---- Editclass ----
    ('script:edit', '编辑基本信息', 'edit', '修改名称、描述、标签、分类等基本属性', 10),
    ('script:edit_content', '编辑脚本内容', 'edit', '修改脚本代码内容', 20),
    ('script:edit_config', '编辑执行配置', 'edit', '修改超时、并发、失败策略、参数、环境变量等配置', 30),
    ('script:manage_version', '版本管理', 'edit', '新建版本、回滚到指定历史版本', 40),
    # ---- Executionclass ----
    ('script:trial_run', '试运行', 'execute', '测试执行脚本（非正式运行）', 10),
    ('script:execute', '正式执行', 'execute', '正式执行脚本', 20),
    ('script:create_task', '创建定时任务', 'execute', '基于该脚本创建定时任务', 30),
    # ---- 管理class ----
    ('script:copy', '复制/另存为', 'manage', '复制脚本、另存为新脚本', 10),
    ('script:export', '导出', 'manage', '导出脚本文件到本地', 20),
    ('script:toggle_status', '启停状态', 'manage', '启用或禁用脚本', 30),
    ('script:archive', '归档/取消归档', 'manage', '归档或取消归档脚本', 40),
    ('script:delete', '删除', 'manage', '删除脚本', 50),
    ('script:submit_approve', '提交审批', 'manage', '提交审核流程', 60),
    ('script:manage_share', '分享管理', 'manage', '管理该脚本的分享权限（新增/删除分享配置）', 70),
]

WORKFLOW_PERMS = [
    # ---- 查看class ----
    ('workflow:view', '查看详情', 'view', '查看工作流基本信息、DAG图/步骤详情', 10),
    ('workflow:view_version', '版本历史', 'view', '查看DAG发布版本历史', 20),
    ('workflow:view_approval', '审批记录', 'view', '查看审批流程记录', 30),
    ('workflow:view_exec_history', '执行记录', 'view', '查看工作流执行历史', 40),
    ('workflow:view_risk', '风险评估', 'view', '查看风险评估结果报告', 50),
    # ---- Editclass ----
    ('workflow:edit', '编辑基本信息', 'edit', '修改名称、描述、分类、环境变量等基本属性', 10),
    ('workflow:edit_graph', '编辑DAG图', 'edit', '修改DAG节点、连线等编排（DAG模式）', 20),
    ('workflow:edit_steps', '编辑步骤', 'edit', '修改线性模式步骤定义', 30),
    ('workflow:edit_hosts', '编辑目标主机', 'edit', '修改工作流绑定的目标主机', 40),
    ('workflow:publish', '发布版本', 'edit', '发布DAG版本（将草稿固化为可执行版本）', 50),
    ('workflow:rollback', '回滚版本', 'edit', '回滚到指定历史DAG版本', 60),
    # ---- Executionclass ----
    ('workflow:trial_run', '试运行', 'execute', '测试执行工作流（非正式运行）', 10),
    ('workflow:execute', '正式执行', 'execute', '正式触发执行工作流', 20),
    ('workflow:create_schedule', '创建定时任务', 'execute', '基于工作流创建定时调度任务', 30),
    ('workflow:cancel_exec', '取消执行', 'execute', '取消正在运行中的工作流任务', 40),
    # ---- 管理class ----
    ('workflow:copy', '复制工作流', 'manage', '复制为新工作流', 10),
    ('workflow:export', '导出', 'manage', '导出工作流定义文件', 20),
    ('workflow:toggle_status', '启停状态', 'manage', '启用或禁用工作流', 30),
    ('workflow:delete', '删除', 'manage', '删除工作流', 40),
    ('workflow:submit_approve', '提交审批', 'manage', '提交审核流程', 50),
    ('workflow:manage_share', '分享管理', 'manage', '管理该工作流的分享权限（新增/删除分享配置）', 60),
]


class Command(BaseCommand):
    help = '初始化分享权限定义字典（脚本 & 工作流）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--type', type=str, default='all',
            choices=['all', 'script', 'workflow'],
            help='初始化权限的资源类型 (默认: all)'
        )
        parser.add_argument(
            '--reset', action='store_true', default=False,
            help='先清空现有权限定义再重新初始化（危险！仅用于修复损坏数据）'
        )

    def _sync_perms(self, resource_type, perm_specs):
        created = 0
        updated = 0
        existing_codes = set()

        if self.reset:
            deleted, _ = SharePermissionDef.objects.filter(resource_type=resource_type).delete()
            if deleted > 0:
                self.stdout.write(self.style.WARNING(
                    f'  已清空 [{resource_type}] 旧权限定义: {deleted} 条'
                ))

        for perm_code, perm_name, category, description, sort in perm_specs:
            existing_codes.add(perm_code)
            try:
                obj = SharePermissionDef.objects.get(
                    resource_type=resource_type, perm_code=perm_code
                )
                # already exists:同步Name/Description/Category/Order
                changed = False
                for field, new_val in [
                    ('perm_name', perm_name),
                    ('category', category),
                    ('description', description),
                    ('sort', sort),
                ]:
                    if getattr(obj, field) != new_val:
                        setattr(obj, field, new_val)
                        changed = True
                if not obj.is_active:
                    obj.is_active = True
                    changed = True
                if changed:
                    obj.save()
                    updated += 1
            except SharePermissionDef.DoesNotExist:
                SharePermissionDef.objects.create(
                    resource_type=resource_type,
                    perm_code=perm_code,
                    perm_name=perm_name,
                    category=category,
                    description=description,
                    sort=sort,
                    is_active=True,
                )
                created += 1

        return created, updated

    def handle(self, *args, **options):
        res_type = options['type']
        self.reset = options['reset']

        if self.reset:
            self.stdout.write(self.style.WARNING('⚠️  --reset 模式：将清空现有权限定义后重新创建!'))
            self.stdout.write('')

        total_created = 0
        total_updated = 0

        if res_type in ('all', 'script'):
            self.stdout.write(self.style.MIGRATE_HEADING('🔄 正在初始化 [脚本] 权限定义...'))
            c, u = self._sync_perms('script', SCRIPT_PERMS)
            total_created += c
            total_updated += u
            self.stdout.write(f'  [脚本] 新增: {c} 条, 更新: {u} 条')
            self.stdout.write('')

        if res_type in ('all', 'workflow'):
            self.stdout.write(self.style.MIGRATE_HEADING('🔄 正在初始化 [工作流] 权限定义...'))
            c, u = self._sync_perms('workflow', WORKFLOW_PERMS)
            total_created += c
            total_updated += u
            self.stdout.write(f'  [工作流] 新增: {c} 条, 更新: {u} 条')
            self.stdout.write('')

        self.stdout.write(self.style.SUCCESS('✅ 权限定义初始化完成!'))
        self.stdout.write(f'   合计: 新增 {total_created} 条, 更新 {total_updated} 条')
        self.stdout.write('')

        # 展示汇总统计
        counts = SharePermissionDef.objects.values('resource_type', 'category', 'is_active').annotate(
            cnt=models_count('id')
        )
        self.stdout.write(self.style.MIGRATE_HEADING('📊 当前权限统计:'))
        for row in counts:
            status = '✅启用' if row['is_active'] else '⛔停用'
            self.stdout.write(
                f'   [{row["resource_type"]:<8}] '
                f'{row["category"]:<7} | '
                f'{status} : {row["cnt"]} 条'
            )


def models_count(*args, **kwargs):
    from django.db.models import Count
    return Count(*args, **kwargs)