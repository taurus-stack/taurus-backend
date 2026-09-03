"""
M1.1 — FeatureCode 完整枚举清单（单一事实来源）。

新增功能时必须在此注册 FeatureCode，否则 EditionGate 不认。
命名约定：<DOMAIN>_<CAPABILITY>，全大写 UPPER_SNAKE_CASE。

同时在此文件维护阶梯定价配额（Quota）字段，M4 License 校验时会用到：
    · QUOTA_MAX_HOSTS  · QUOTA_MAX_USERS  · QUOTA_MAX_SCHEDULED_TASKS
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Literal

# =========================================================================
# 1. Feature Code 全集（每一条都必须与 CE/EE 划分矩阵严格对齐）
# =========================================================================

# --- 系统基础（dvadmin 层） ---
F_USER_MANAGE = "USER_MANAGE"
F_ROLE_MANAGE = "ROLE_MANAGE"
F_DEPT_MANAGE = "DEPT_MANAGE"
F_MENU_MANAGE = "MENU_MANAGE"
F_DICT_MANAGE = "DICT_MANAGE"
F_CONFIG_MANAGE = "CONFIG_MANAGE"
F_LOGIN_LOG = "LOGIN_LOG"
F_OPERATION_LOG = "OPERATION_LOG"
F_AREAS_MANAGE = "AREAS_MANAGE"  # 行政区域
F_COLUMN_PERMISSION = "COLUMN_PERMISSION"  # 列/字段权限（EE）
F_DATA_PERMISSION_MATRIX = "DATA_PERMISSION_MATRIX"  # 完整行级矩阵（EE）
F_WHITELIST_ADVANCED = "WHITELIST_ADVANCED"  # 多维白名单（EE）
F_BACKUP_RESTORE = "BACKUP_RESTORE"  # 备份恢复中心（EE）
F_DOWNLOAD_CENTER = "DOWNLOAD_CENTER"  # 客户端打包下载中心（EE）
F_SSO_LDAP = "SSO_LDAP"  # LDAP/SSO 集成（EE）

# --- 主机管理 ---
F_HOST_MANAGE = "HOST_MANAGE"
F_HOST_REGISTRATION_TOKEN = "HOST_REGISTRATION_TOKEN"
F_HOST_ONLINE_STATUS = "HOST_ONLINE_STATUS"  # 基本上下线显示（CE/EE 均有）
F_HOST_MONITOR_METRICS = "HOST_MONITOR_METRICS"  # CPU/内存/磁盘/负载 指标(EE)
F_HEARTBEAT_SERVER_CLUSTER = "HEARTBEAT_SERVER_CLUSTER"  # 多心跳节点负载均衡(EE)
F_CA_CRL_MANAGEMENT = "CA_CRL_MANAGEMENT"  # CA / CRL / 证书吊销管理(EE)

# --- 运维执行中心 ---
F_OPS_COMMAND = "OPS_COMMAND"
F_OPS_SCRIPT_TEMPLATE = "OPS_SCRIPT_TEMPLATE"
F_OPS_FILE_TRANSFER = "OPS_FILE_TRANSFER"  # 上传/下载
F_OPS_EXECUTION_HISTORY = "OPS_EXECUTION_HISTORY"
F_OPS_SERIAL_PARALLEL = "OPS_SERIAL_PARALLEL"  # 串行/并发执行
F_OPS_PILOT_CANARY = "OPS_PILOT_CANARY"  # 灰度/金丝雀发布模式(EE)
F_OPS_EXECUTION_APPROVAL = "OPS_EXECUTION_APPROVAL"  # 执行任务审批(EE)
F_OPS_EXECUTION_NOTIFICATION = "OPS_EXECUTION_NOTIFICATION"  # 邮件/Webhook 通知(EE)

# --- 脚本库 ---
F_SCRIPT_CRUD = "SCRIPT_CRUD"
F_SCRIPT_CATEGORY = "SCRIPT_CATEGORY"
F_SCRIPT_VERSION_LIMITED = "SCRIPT_VERSION_LIMITED"  # CE 仅 3 个历史版本
F_SCRIPT_VERSION_FULL = "SCRIPT_VERSION_FULL"  # EE 无限版本(EE)
F_SCRIPT_TASK = "SCRIPT_TASK"  # 脚本定时调度
F_SCRIPT_PERMISSION_BASIC = "SCRIPT_PERMISSION_BASIC"  # View/Exec 两级(CE)
F_SCRIPT_PERMISSION_FINEGRAINED = "SCRIPT_PERMISSION_FINEGRAINED"  # 细粒度(EE)
F_SCRIPT_OFFICIAL_MARKET_FULL = "SCRIPT_OFFICIAL_MARKET_FULL"  # 20+ 完整官方脚本库(EE)
F_SCRIPT_OFFICIAL_MARKET_CE = "SCRIPT_OFFICIAL_MARKET_CE"  # 6 个 CE 演示脚本
F_SCRIPT_SECURITY_CHECK = "SCRIPT_SECURITY_CHECK"  # bandit/shellcheck/semgrep + 自定义规则(EE)
F_SCRIPT_APPROVAL_FLOW = "SCRIPT_APPROVAL_FLOW"  # 多级规则/或签/会签/加签/转签(EE)
F_SCRIPT_AUDIT_LOG = "SCRIPT_AUDIT_LOG"  # 脚本审计日志(EE)
F_SCRIPT_SHARING = "SCRIPT_SHARING"  # 直接分享 + ShareLink 链接分享(EE)

# --- 工作流 ---
F_WORKFLOW_LINEAR = "WORKFLOW_LINEAR"  # 线性 step-by-step (CE/EE 均有)
F_WORKFLOW_EXECUTION_RECORD = "WORKFLOW_EXECUTION_RECORD"
F_WORKFLOW_DAG_ENGINE = "WORKFLOW_DAG_ENGINE"  # DAG v2 可视化编排 + 12+ 节点类型(EE)
F_WORKFLOW_DAG_VERSIONING = "WORKFLOW_DAG_VERSIONING"  # DAG 版本/发布快照(EE)
F_WORKFLOW_APPROVAL_FLOW = "WORKFLOW_APPROVAL_FLOW"  # 工作流审批(EE)
F_WORKFLOW_SHARING = "WORKFLOW_SHARING"  # 工作流分享权限(EE)
F_WORKFLOW_RISK_ASSESSMENT = "WORKFLOW_RISK_ASSESSMENT"  # 风险评估对话框(EE)

# --- 调度中心 ---
F_SCHEDULE_CELERY_BEAT = "SCHEDULE_CELERY_BEAT"  # 内置 Celery Beat 单实例(CE/EE)
F_SCHEDULE_STANDALONE_CE = "SCHEDULE_STANDALONE_CE"  # CE 单实例非 HA 降级版 taurus-scheduler (CE ONLY，占位标识)
F_SCHEDULE_HA_CLUSTER = "SCHEDULE_HA_CLUSTER"  # EE 独立调度：Redis 主备选举 + 队列 + HTTP 兜底回调(EE)
F_SCRIPT_TASK_BASIC = "SCRIPT_TASK_BASIC"
F_SCRIPT_TASK_UNIFIED = "SCRIPT_TASK_UNIFIED"  # ScriptTask + Schedule 双轨统一调度(EE)
F_SCHEDULE_ALERT_RETRY = "SCHEDULE_ALERT_RETRY"  # 失败告警 + 重试(EE)

# --- Supervisor 程序管理 ---
F_PROGRAM_BASIC_MANAGE = "PROGRAM_BASIC_MANAGE"  # 启动/停止/重启/PID/端口
F_PROGRAM_COMMAND_SINGLE = "PROGRAM_COMMAND_SINGLE"  # 逐台安装/升级/卸载
F_PROGRAM_COMMAND_BATCH = "PROGRAM_COMMAND_BATCH"  # 批量分发(EE)
F_PROGRAM_INSTALL_TEMPLATE = "PROGRAM_INSTALL_TEMPLATE"  # 安装模板(EE)
F_PROGRAM_INSTALL_POLICY = "PROGRAM_INSTALL_POLICY"  # 规则驱动批量自动匹配策略(EE)
F_PROGRAM_HOST_BINDING = "PROGRAM_HOST_BINDING"  # 安装绑定(EE)
F_PROGRAM_INSTALL_CONFIG = "PROGRAM_INSTALL_CONFIG"  # 安装配置(EE)

# --- 日志中心 ---
F_EXECUTION_LOG = "EXECUTION_LOG"  # 基础 Record/RecordDetail 执行日志(CE/EE)
F_HOST_LOG_FORWARDING = "HOST_LOG_FORWARDING"  # HostLog 远程主机日志转发集中采集(EE)
F_LOG_COMMAND_CONTROL = "LOG_COMMAND_CONTROL"  # LogCommand 日志采集控制命令(EE)

# --- 扩展中心（EE 专属页面）---
F_KNOWLEDGE_BASE = "KNOWLEDGE_BASE"  # 知识库(EE)
F_INSPECTION_CENTER = "INSPECTION_CENTER"  # 巡检中心(EE)
F_TOOLS_CENTER = "TOOLS_CENTER"  # 工具中心(EE)
F_TICKET_CENTER = "TICKET_CENTER"  # 票据中心(EE)
F_TASK_CENTER = "TASK_CENTER"  # 任务中心（待办聚合）(EE)
F_SHARE_ACTIVATE_PAGE = "SHARE_ACTIVATE_PAGE"  # 分享激活页(EE)
F_CONTACT_LEAD_PORTAL = "CONTACT_LEAD_PORTAL"  # 联系销售/商务表单(EE)

# --- 安全基础（CE/EE 均保留）---
F_AUTH_TICKET = "AUTH_TICKET"  # taurus-auth 一次性票据
F_MTLS = "MTLS"  # mTLS 双向认证
F_SIGNING = "SIGNING"  # 签名验证防重放
F_CONFIG_CRYPTO = "CONFIG_CRYPTO"  # Fernet 配置加密
F_LICENSE_SYSTEM = "LICENSE_SYSTEM"  # License 系统（仅 EE 使用，CE 不报错）

# =========================================================================
# 2. 功能分组（便于文档生成 / UI 升级卡片展示）
# =========================================================================

FEATURE_GROUPS: Dict[str, Dict[str, str]] = {
    "系统基础": {
        F_USER_MANAGE: "用户管理",
        F_ROLE_MANAGE: "角色管理",
        F_DEPT_MANAGE: "部门管理",
        F_MENU_MANAGE: "菜单管理",
        F_DICT_MANAGE: "字典管理",
        F_CONFIG_MANAGE: "系统配置",
        F_LOGIN_LOG: "登录日志",
        F_OPERATION_LOG: "操作日志",
        F_AREAS_MANAGE: "行政区域",
        F_COLUMN_PERMISSION: "列/字段权限（企业版）",
        F_DATA_PERMISSION_MATRIX: "完整行级数据权限矩阵（企业版）",
        F_WHITELIST_ADVANCED: "多维白名单访问控制（企业版）",
        F_BACKUP_RESTORE: "备份恢复中心（企业版）",
        F_DOWNLOAD_CENTER: "客户端打包下载中心（企业版）",
        F_SSO_LDAP: "LDAP / SSO 单点登录（企业版）",
    },
    "主机管理": {
        F_HOST_MANAGE: "主机注册/审批/禁用",
        F_HOST_REGISTRATION_TOKEN: "注册令牌（RegistrationToken）",
        F_HOST_ONLINE_STATUS: "主机在线状态",
        F_HOST_MONITOR_METRICS: "主机监控指标（CPU/内存/磁盘/负载）（企业版）",
        F_HEARTBEAT_SERVER_CLUSTER: "多心跳节点负载均衡（企业版）",
        F_CA_CRL_MANAGEMENT: "CA 证书/吊销列表管理（企业版）",
    },
    "运维执行": {
        F_OPS_COMMAND: "命令执行（Xterm 实时输出）",
        F_OPS_SCRIPT_TEMPLATE: "脚本模板执行",
        F_OPS_FILE_TRANSFER: "文件上传/下载",
        F_OPS_EXECUTION_HISTORY: "执行历史与重跑",
        F_OPS_SERIAL_PARALLEL: "串行 / 并发执行模式",
        F_OPS_PILOT_CANARY: "金丝雀灰度发布模式（企业版）",
        F_OPS_EXECUTION_APPROVAL: "执行任务审批流（企业版）",
        F_OPS_EXECUTION_NOTIFICATION: "执行结果邮件 / Webhook 通知（企业版）",
    },
    "脚本库": {
        F_SCRIPT_CRUD: "脚本 CRUD",
        F_SCRIPT_CATEGORY: "脚本分类管理",
        F_SCRIPT_VERSION_LIMITED: "脚本版本（限 3 个历史版）",
        F_SCRIPT_VERSION_FULL: "脚本版本（无限历史版）（企业版）",
        F_SCRIPT_TASK: "脚本定时调度",
        F_SCRIPT_PERMISSION_BASIC: "脚本权限（基础 View/Exec 两级）",
        F_SCRIPT_PERMISSION_FINEGRAINED: "脚本权限（细粒度矩阵）（企业版）",
        F_SCRIPT_OFFICIAL_MARKET_CE: "内置演示脚本包（6 个基础巡检脚本）",
        F_SCRIPT_OFFICIAL_MARKET_FULL: "完整官方脚本市场（20+ 专业脚本）（企业版）",
        F_SCRIPT_SECURITY_CHECK: "脚本安全扫描引擎（企业版）",
        F_SCRIPT_APPROVAL_FLOW: "脚本多级审批流（企业版）",
        F_SCRIPT_AUDIT_LOG: "脚本操作审计日志（企业版）",
        F_SCRIPT_SHARING: "脚本分享（用户/角色/链接）（企业版）",
    },
    "工作流": {
        F_WORKFLOW_LINEAR: "线性流程编排",
        F_WORKFLOW_EXECUTION_RECORD: "工作流执行记录",
        F_WORKFLOW_DAG_ENGINE: "DAG 可视化编排引擎 v2（企业版）",
        F_WORKFLOW_DAG_VERSIONING: "DAG 版本管理与发布快照（企业版）",
        F_WORKFLOW_APPROVAL_FLOW: "工作流审批流（企业版）",
        F_WORKFLOW_SHARING: "工作流分享权限（企业版）",
        F_WORKFLOW_RISK_ASSESSMENT: "工作流风险评估（企业版）",
    },
    "调度中心": {
        F_SCHEDULE_CELERY_BEAT: "内置 Celery Beat 调度（单实例）",
        F_SCHEDULE_STANDALONE_CE: "独立调度服务（单实例非 HA 版）",
        F_SCHEDULE_HA_CLUSTER: "独立调度服务（HA 高可用集群 + Redis 选举）（企业版）",
        F_SCRIPT_TASK_BASIC: "脚本定时任务（基础版）",
        F_SCRIPT_TASK_UNIFIED: "脚本任务双轨统一调度（企业版）",
        F_SCHEDULE_ALERT_RETRY: "调度失败告警与自动重试（企业版）",
    },
    "程序管理": {
        F_PROGRAM_BASIC_MANAGE: "基础程序管理（启动/停止/重启）",
        F_PROGRAM_COMMAND_SINGLE: "逐台程序安装/升级/卸载",
        F_PROGRAM_COMMAND_BATCH: "批量程序分发（企业版）",
        F_PROGRAM_INSTALL_TEMPLATE: "程序安装模板（企业版）",
        F_PROGRAM_INSTALL_POLICY: "规则驱动程序安装策略（企业版）",
        F_PROGRAM_HOST_BINDING: "主机-模板绑定管理（企业版）",
        F_PROGRAM_INSTALL_CONFIG: "逐主机安装配置管理（企业版）",
    },
    "日志与扩展中心": {
        F_EXECUTION_LOG: "基础执行日志",
        F_HOST_LOG_FORWARDING: "远程主机日志集中采集（企业版）",
        F_LOG_COMMAND_CONTROL: "日志采集控制命令（企业版）",
        F_KNOWLEDGE_BASE: "知识库（企业版）",
        F_INSPECTION_CENTER: "巡检中心（企业版）",
        F_TOOLS_CENTER: "工具中心（企业版）",
        F_TICKET_CENTER: "票据中心（企业版）",
        F_TASK_CENTER: "待办任务中心（企业版）",
        F_SHARE_ACTIVATE_PAGE: "分享激活页（企业版）",
        F_CONTACT_LEAD_PORTAL: "联系销售表单（企业版）",
    },
    "安全": {
        F_AUTH_TICKET: "一次性票据鉴权（taurus-auth）",
        F_MTLS: "mTLS 双向身份认证",
        F_SIGNING: "签名验证（防重放攻击）",
        F_CONFIG_CRYPTO: "Fernet 敏感配置加密",
        F_LICENSE_SYSTEM: "商业授权系统（企业版使用）",
    },
}

# =========================================================================
# 3. 阶梯定价配额（Quota）定义
# =========================================================================

# Edition 名
EditionName = Literal["community", "enterprise"]

# 商业版阶梯 Tier（仅 EE 有，CE 固定为 "community" tier）
TierName = Literal["community", "starter", "professional", "enterprise", "ultimate"]


class QuotaDefaults:
    """
    各 Tier 默认配额（作为 License 字段未指定时的 fallback；
    CE 固定取 COMMUNITY；EE 按 License.tier 字段匹配下表）。
    """

    # 社区版：完全免费，限制规模促进付费转化
    COMMUNITY: Dict[str, int | None] = {
        "max_hosts": 50,          # 最多 50 台托管主机
        "max_users": 10,          # 最多 10 个用户账号
        "max_scheduled_tasks": 20,  # Schedule + ScriptTask 总计 ≤ 20 条
        "max_script_versions_per_script": 3,  # 每个脚本最多 3 个历史版本
        "max_concurrent_executions": 10,       # 最大并发执行数
        "max_workflows": 10,      # 最多 10 条工作流（DAG + 线性）
    }

    # 商业版 Starter（入门）
    STARTER: Dict[str, int | None] = {
        "max_hosts": 200,
        "max_users": 50,
        "max_scheduled_tasks": 200,
        "max_script_versions_per_script": None,  # None = 不限制
        "max_concurrent_executions": 50,
        "max_workflows": None,
    }

    # 商业版 Professional（专业）
    PROFESSIONAL: Dict[str, int | None] = {
        "max_hosts": 1000,
        "max_users": 200,
        "max_scheduled_tasks": 2000,
        "max_script_versions_per_script": None,
        "max_concurrent_executions": 200,
        "max_workflows": None,
    }

    # 商业版 Enterprise（企业）
    ENTERPRISE: Dict[str, int | None] = {
        "max_hosts": 5000,
        "max_users": 1000,
        "max_scheduled_tasks": None,
        "max_script_versions_per_script": None,
        "max_concurrent_executions": None,
        "max_workflows": None,
    }

    # 商业版 Ultimate（旗舰 / 定制）
    ULTIMATE: Dict[str, int | None] = {
        "max_hosts": None,
        "max_users": None,
        "max_scheduled_tasks": None,
        "max_script_versions_per_script": None,
        "max_concurrent_executions": None,
        "max_workflows": None,
    }

    @classmethod
    def for_tier(cls, tier: TierName) -> Dict[str, int | None]:
        return {
            "community": cls.COMMUNITY,
            "starter": cls.STARTER,
            "professional": cls.PROFESSIONAL,
            "enterprise": cls.ENTERPRISE,
            "ultimate": cls.ULTIMATE,
        }[tier]


# =========================================================================
# 4. 辅助：取 Feature Code 描述
# =========================================================================

def describe_feature(code: str) -> str:
    """返回 Feature Code 的中文描述；找不到时返回 code 本身。"""
    for _group, mapping in FEATURE_GROUPS.items():
        if code in mapping:
            return mapping[code]
    return code


# =========================================================================
# 5. Feature Code 完整集合（运行时做合法性校验）
# =========================================================================

ALL_FEATURE_CODES: FrozenSet[str] = frozenset(
    code for mapping in FEATURE_GROUPS.values() for code in mapping
)