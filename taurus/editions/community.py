"""
M1.2 — Community Edition（社区版）实现。

按用户确认的划分：
  · 主机上限 50 台 / 用户上限 10 人 / 调度任务上限 20 / 脚本版本限 3 个
  · EE 功能：审批流 / DAG 引擎 / 安全扫描 / 策略匹配 / 集中日志 / 4 大扩展中心 全部移除
  · 保留：SCHEDULE_STANDALONE_CE（单实例非 HA 版 taurus-scheduler 降级调度）
  · 保留：6 个基础官方巡检脚本（SCRIPT_OFFICIAL_MARKET_CE）
"""

from __future__ import annotations

from typing import Dict, FrozenSet

from .base import BaseEdition
from .features import (
    # --- 系统基础（仅基础） ---
    F_USER_MANAGE, F_ROLE_MANAGE, F_DEPT_MANAGE, F_MENU_MANAGE,
    F_DICT_MANAGE, F_CONFIG_MANAGE, F_LOGIN_LOG, F_OPERATION_LOG,
    F_AREAS_MANAGE,
    # --- 主机（基础） ---
    F_HOST_MANAGE, F_HOST_REGISTRATION_TOKEN, F_HOST_ONLINE_STATUS,
    # --- 运维执行（基础） ---
    F_OPS_COMMAND, F_OPS_SCRIPT_TEMPLATE, F_OPS_FILE_TRANSFER,
    F_OPS_EXECUTION_HISTORY, F_OPS_SERIAL_PARALLEL,
    # --- 脚本库（基础 + 6 个 CE 演示 + 3 个历史版本限制） ---
    F_SCRIPT_CRUD, F_SCRIPT_CATEGORY,
    F_SCRIPT_VERSION_LIMITED,
    F_SCRIPT_TASK, F_SCRIPT_PERMISSION_BASIC,
    F_SCRIPT_OFFICIAL_MARKET_CE,
    # --- 工作流（DAG 编辑放开 + 基础记录，保存数受 max_workflows=10 限制） ---
    F_WORKFLOW_LINEAR, F_WORKFLOW_EXECUTION_RECORD,
    F_WORKFLOW_DAG_ENGINE,
    # --- 调度：Celery Beat + CE 单实例非 HA 降级独立调度 ---
    F_SCHEDULE_CELERY_BEAT, F_SCHEDULE_STANDALONE_CE,
    F_SCRIPT_TASK_BASIC,
    # --- Supervisor 程序管理（基础 + 逐台，无模板/策略/绑定） ---
    F_PROGRAM_BASIC_MANAGE, F_PROGRAM_COMMAND_SINGLE,
    # --- 日志（仅 Record 执行日志） ---
    F_EXECUTION_LOG,
    # --- 安全：4 项全部保留 ---
    F_AUTH_TICKET, F_MTLS, F_SIGNING, F_CONFIG_CRYPTO,
    F_LICENSE_SYSTEM,  # 保留定义（CE 场景下不限制）
    QuotaDefaults,
)


class CommunityEdition(BaseEdition):
    @property
    def edition_name(self):
        return "community"  # type: ignore[return-value]

    @property
    def tier(self):
        return "community"  # type: ignore[return-value]

    @property
    def features(self) -> FrozenSet[str]:
        return frozenset(
            [
                # 系统基础
                F_USER_MANAGE, F_ROLE_MANAGE, F_DEPT_MANAGE, F_MENU_MANAGE,
                F_DICT_MANAGE, F_CONFIG_MANAGE, F_LOGIN_LOG, F_OPERATION_LOG,
                F_AREAS_MANAGE,
                # 主机
                F_HOST_MANAGE, F_HOST_REGISTRATION_TOKEN, F_HOST_ONLINE_STATUS,
                # 运维执行
                F_OPS_COMMAND, F_OPS_SCRIPT_TEMPLATE, F_OPS_FILE_TRANSFER,
                F_OPS_EXECUTION_HISTORY, F_OPS_SERIAL_PARALLEL,
                # 脚本库
                F_SCRIPT_CRUD, F_SCRIPT_CATEGORY,
                F_SCRIPT_VERSION_LIMITED,
                F_SCRIPT_TASK, F_SCRIPT_PERMISSION_BASIC,
                F_SCRIPT_OFFICIAL_MARKET_CE,
                # 工作流
                F_WORKFLOW_LINEAR, F_WORKFLOW_EXECUTION_RECORD,
                F_WORKFLOW_DAG_ENGINE,
                # 调度
                F_SCHEDULE_CELERY_BEAT, F_SCHEDULE_STANDALONE_CE,
                F_SCRIPT_TASK_BASIC,
                # 程序管理
                F_PROGRAM_BASIC_MANAGE, F_PROGRAM_COMMAND_SINGLE,
                # 日志
                F_EXECUTION_LOG,
                # 安全
                F_AUTH_TICKET, F_MTLS, F_SIGNING, F_CONFIG_CRYPTO,
                F_LICENSE_SYSTEM,
            ]
        )

    @property
    def quota(self) -> Dict[str, int | None]:
        return dict(QuotaDefaults.COMMUNITY)


def get_edition() -> CommunityEdition:
    """editions.loader 内部会调用该函数创建 CE 实例。"""
    return CommunityEdition()