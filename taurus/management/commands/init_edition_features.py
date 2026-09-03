"""M3.1 — 给 EE 专属菜单节点写入 requires_feature 字段。

匹配策略（按优先级）：
  1. 菜单 component 字段（前端 Vue 组件路径，唯一稳定）
  2. 菜单 web_path 字段（路由路径）
  3. 菜单 name 字段（兜底，可能有多语言，但当前只有中文）

本命令是幂等的：重复运行会覆盖已有的 requires_feature 值，
不会产生重复或冲突。

注意：dvadmin Menu model 没有 requires_feature 属性（我们是通过 migration
在 DB 层加的列），所以读写都走 raw SQL，不走 ORM。
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection


# Mapping: 匹配键 → FeatureCode(s)
# 一个菜单可绑定多个 FC（逗号分隔），前端 _hasRequiredFeature 用 OR 语义
EE_MENU_FEATURE_MAP: list[dict] = [
    # ==== M2.1 脚本审批 + 安全 + 分享 ====
    {"component": "taurus/ops/script-check-rule/index",   "fc": "SCRIPT_SECURITY_CHECK"},
    {"component": "taurus/ops/script-approval/index",     "fc": "SCRIPT_APPROVAL_FLOW"},
    # Share 权限独立菜单可能不存在（是弹窗），留空；后端 ScriptSharePermissionDefViewSet 已 Gate

    # ==== M2.2 工作流审批 + DAG + 分享 ====
    {"component": "taurus/workflow/WorkflowEditor",         "fc": "WORKFLOW_DAG_ENGINE"},
    {"component": "taurus/workflow/WorkflowApproveList",    "fc": "WORKFLOW_APPROVAL_FLOW"},

    # ==== M2.3 调度 HA / 告警 / 统一 ====
    {"component": "taurus/task-center/index", "fc": "TASK_CENTER"},

    # ==== M2.4 Supervisor 程序管理高级 ====
    {"component": "taurus/supervisor/program-install-config/index",   "fc": "PROGRAM_INSTALL_CONFIG"},
    {"component": "taurus/supervisor/program-install-policy/index",    "fc": "PROGRAM_INSTALL_POLICY"},
    {"component": "taurus/supervisor/program-install-template/index", "fc": "PROGRAM_INSTALL_TEMPLATE"},

    # ==== M2.6 Ops 审批 ====
    {"component": "taurus/ops/execution-approval/index", "fc": "OPS_EXECUTION_APPROVAL"},

    # ==== M5 Edition Gate — 后端菜单补漏（页面尚未 seed 到 DB，init 时会 skip）====
    {"component": "taurus/knowledge/KnowledgeBase",   "fc": "KNOWLEDGE_BASE"},
    {"component": "taurus/inspection/InspectionCenter", "fc": "INSPECTION_CENTER"},
    {"component": "taurus/tools/ToolsCenter",        "fc": "TOOLS_CENTER"},
    {"component": "taurus/ticket/TicketCenter",      "fc": "TICKET_CENTER"},
]


def _table_name():
    return "taurus_system_menu"  # dvadmin table_prefix='taurus_'


def _lookup(filters: dict) -> list[dict]:
    """Raw SQL lookup — returns [{id, name, component, requires_feature}, ...]."""
    where_parts = []
    params = []
    for k, v in filters.items():
        where_parts.append(f"{k} = %s")
        params.append(v)
    sql = (
        f"SELECT id, name, component, web_path, "
        f"COALESCE(requires_feature, '') AS requires_feature "
        f"FROM {_table_name()} WHERE {' AND '.join(where_parts)}"
    )
    with connection.cursor() as cur:
        cur.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _update(menu_id: int, fc: str):
    with connection.cursor() as cur:
        cur.execute(
            f"UPDATE {_table_name()} SET requires_feature = %s WHERE id = %s",
            [fc, menu_id],
        )


class Command(BaseCommand):
    help = "M3.1 — Seed requires_feature on EE-only menu nodes"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually run UPDATE; without this flag we only dry-run (preview).",
        )

    def handle(self, *args, **options):
        apply = options.get("apply", False)
        verb = "APPLY" if apply else "DRY-RUN"
        self.stdout.write(f"\n[M3.1] init_edition_features — mode={verb}\n")

        updated = 0
        skipped_no_match = 0
        no_change = 0

        for entry in EE_MENU_FEATURE_MAP:
            fc = entry["fc"]
            filters = {}
            if entry.get("component"):
                filters["component"] = entry["component"]
            if entry.get("web_path"):
                filters["web_path"] = entry["web_path"]

            rows = _lookup(filters)

            if not rows:
                skipped_no_match += 1
                self.stdout.write(f"  ⚠  SKIP (not in DB): fc={fc}  filters={filters}")
                continue

            for row in rows:
                old = row["requires_feature"] or ""
                if old == fc:
                    no_change += 1
                    self.stdout.write(
                        f"  =  NO-CHANGE  menu[{row['id']}] {row['name']}  fc={fc}"
                    )
                    continue
                if apply:
                    _update(row["id"], fc)
                self.stdout.write(
                    f"  {'↑' if apply else '?'}  UPDATE   menu[{row['id']}] {row['name']}  "
                    f"{old or '(empty)'} → {fc}"
                )
                updated += 1

        if apply and updated > 0:
            # 让 WebRouterSerializer 的模块级缓存失效，下次 bootstrap 自动刷新
            try:
                from dvadmin.system.views.menu import invalidate_menu_requires_feature_cache
                invalidate_menu_requires_feature_cache()
                self.stdout.write("  ✓  invalidated WebRouterSerializer cache")
            except Exception as exc:  # noqa: BLE001
                self.stdout.write(f"  ⚠  cache invalidation failed: {exc}")

        self.stdout.write(
            f"\n[M3.1] DONE  updated={updated}  no_change={no_change}  "
            f"skipped_no_match={skipped_no_match}  (apply={apply})\n"
        )
        if not apply:
            self.stdout.write(
                "Re-run with --apply to persist changes.\n"
            )
