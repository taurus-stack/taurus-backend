from django.apps import AppConfig


class TaurusEEConfig(AppConfig):
    """Taurus Ops 扩展功能模块容器（全功能开源版本起常驻启用）.

    · ready() 中无条件注册全部 EE Services / DAG Units 到 ee_registry；
    · 使用 taurus.ee_registry 作为对外暴露的 Service 注入点
      （避免 taurus→taurus_ee 反向 import 的循环依赖）。
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "taurus_ee"
    verbose_name = "Taurus Extended Modules"

    # ------------------------------------------------------------------
    # ready() 是 taurus→taurus_ee 合法通信的唯一入口
    # ------------------------------------------------------------------
    def ready(self) -> None:
        import logging

        from taurus.ee_registry import ee_registry  # noqa: F401  — 确保注册表模块先初始化

        logger = logging.getLogger(__name__)

        # 全功能版本：始终注册全部 Services 与 DAG Units（License 仅控配额/服务等级）
        self._register_ee_services(logger)
        self._register_ee_workflow_units(logger)

        logger.info(
            "[taurus_ee] Extended modules loaded: services=%d, workflow_units=%d",
            len(ee_registry.services),
            len(ee_registry.workflow_units),
        )

    # ------------------------------------------------------------------
    def _register_ee_services(self, logger) -> None:
        """把扩展 Service 实现写入全局 ee_registry（供 taurus 侧 Wrapper 调用）.

        注册项：
          script_checker         → taurus.script_checker.ScriptCheckService（纯算法类，留在 taurus）
          script_approval_engine → taurus_ee.services.script_approval_engine.ApprovalFlowEngine
          share_service          → taurus_ee.views.share_permission_view.ShareService
          workflow_*             → DAG / 审批 / 风险评估服务
          scheduler_*            → 统一调度 / 告警服务
          program_policy_engine  → 程序安装策略引擎
          ops_*                  → 通知 / 金丝雀服务
        """
        from taurus.ee_registry import ee_registry

        # 1. 先以 stub 初始化所有 12 项 service（顺序稳定）
        stubs = {
            # M2.1 Script 模块
            "script_checker": None,
            "script_approval_engine": None,
            "script_audit_service": None,
            "share_service": None,
            # M2.2 Workflow 模块
            "workflow_dag_service": None,
            "workflow_approval_engine": None,
            "workflow_risk_service": None,
            # M2.3 Scheduler 模块
            "scheduler_alert_service": None,
            "scheduler_unified_service": None,
            # M2.4 Supervisor Program 模块
            "program_policy_engine": None,
            # M2.5 Ops 模块
            "ops_notification_service": None,
            "ops_canary_service": None,
        }

        # 2. 填入已就绪的 EE 真实实现
        try:
            from taurus.script_checker import ScriptCheckService
            stubs["script_checker"] = ScriptCheckService
        except Exception as exc:  # noqa: BLE001
            logger.warning("[EE] script_checker load failed: %s", exc)

        try:
            from taurus_ee.services.script_approval_engine import ApprovalFlowEngine
            stubs["script_approval_engine"] = ApprovalFlowEngine
        except Exception as exc:  # noqa: BLE001
            logger.warning("[EE] script_approval_engine load failed: %s", exc)

        try:
            from taurus_ee.views.share_permission_view import ShareService
            stubs["share_service"] = ShareService
        except Exception as exc:  # noqa: BLE001
            logger.warning("[EE] share_service load failed: %s", exc)

        # ---------- M2.2 Workflow 3 项真实实现注入 ----------
        try:
            from taurus_ee.services.workflow_approval_engine import (
                WorkflowApprovalFlowEngine as _WFFlow,
            )
            stubs["workflow_approval_engine"] = _WFFlow
        except Exception as exc:  # noqa: BLE001
            logger.warning("[EE] workflow_approval_engine load failed: %s", exc)

        try:
            from taurus_ee.serializers.workflow_dag import (
                WorkflowDAGService as _DAGSvc,
            )
            stubs["workflow_dag_service"] = _DAGSvc  # 单例使用，直接暴露 类 即可
        except Exception as exc:  # noqa: BLE001
            logger.warning("[EE] workflow_dag_service load failed: %s", exc)

        try:
            from taurus_ee.serializers.workflow_dag import (
                WorkflowRiskService as _RiskSvc,
            )
            stubs["workflow_risk_service"] = _RiskSvc
        except Exception as exc:  # noqa: BLE001
            logger.warning("[EE] workflow_risk_service load failed: %s", exc)

        # ---------- M2.3 Scheduler 2 项真实实现注入 ----------
        try:
            from taurus_ee.services.scheduler_ha_services import (
                SchedulerUnifiedService as _Unified,
            )
            stubs["scheduler_unified_service"] = _Unified
        except Exception as exc:  # noqa: BLE001
            logger.warning("[EE] scheduler_unified_service load failed: %s", exc)

        try:
            from taurus_ee.services.scheduler_ha_services import (
                SchedulerAlertService as _Alert,
            )
            stubs["scheduler_alert_service"] = _Alert
        except Exception as exc:  # noqa: BLE001
            logger.warning("[EE] scheduler_alert_service load failed: %s", exc)

        # ---------- M2.4 Supervisor 程序管理 1 项真实实现注入 ----------
        try:
            from taurus_ee.services.program_policy_engine import (
                ProgramPolicyEngine as _PolicyEngine,
            )
            stubs["program_policy_engine"] = _PolicyEngine
        except Exception as exc:  # noqa: BLE001
            logger.warning("[EE] program_policy_engine load failed: %s", exc)

        # ---------- M2.6 Ops 执行中心 2 项真实实现注入 ----------
        try:
            from taurus_ee.services.ops_notification_service import (
                OpsNotificationService as _NotifySvc,
            )
            stubs["ops_notification_service"] = _NotifySvc
        except Exception as exc:  # noqa: BLE001
            logger.warning("[EE] ops_notification_service load failed: %s", exc)

        try:
            from taurus_ee.services.ops_canary_service import (
                OpsCanaryService as _CanarySvc,
            )
            stubs["ops_canary_service"] = _CanarySvc
        except Exception as exc:  # noqa: BLE001
            logger.warning("[EE] ops_canary_service load failed: %s", exc)

        # 3. 全部注册到 ee_registry
        for k, v in stubs.items():
            ee_registry.register_service(k, v)
            logger.debug("[EE] service %s registered -> %s", k,
                         "<Stub>" if v is None else getattr(v, "__name__", v.__class__.__name__))

    def _register_ee_workflow_units(self, logger) -> None:
        """注册扩展 DAG Engine Unit（全功能版本常驻加载）。

        从 taurus.workflow.units 下 import 通过 register_unit_adapter 装饰的
        12 个扩展 unit，注册到 ee_registry.workflow_units 中。
        """
        from taurus.ee_registry import ee_registry

        # 12 个 EE DAG units（和 M2.0 预留一致）→ 实际 import 真实 unit class
        unit_import_paths = [
            ("condition",       "taurus.workflow.units.condition",        "ConditionAdapter"),
            ("loop",            "taurus.workflow.units.loop",             "LoopAdapter"),
            ("sub_workflow",    "taurus.workflow.units.sub_workflow",     "SubWorkflowAdapter"),
            ("http",            "taurus.workflow.units.http",             "HttpAdapter"),
            ("http_callback",   "taurus.workflow.units.http_callback",    "HttpCallbackAdapter"),
            ("email",           "taurus.workflow.units.email_notification", "EmailNotificationAdapter"),
            ("webhook",         "taurus.workflow.units.webhook_notification", "WebhookNotificationAdapter"),
            ("virtual",         "taurus.workflow.units.virtual",          "StartAdapter"),
            ("wait",            "taurus.workflow.units.wait",             "WaitAdapter"),
            ("manifest",        "taurus.workflow.units.transform",        "TransformAdapter"),
            ("approval_bridge", "taurus.workflow.units.approval",         "ApprovalAdapter"),
            ("user_approval_ee","taurus.workflow.units.ops_execution",    "ScriptAdapter"),
        ]
        for key, mod_path, cls_name in unit_import_paths:
            try:
                import importlib
                mod = importlib.import_module(mod_path)
                cls = getattr(mod, cls_name)
                ee_registry.register_workflow_unit(key, cls)
                logger.debug("[EE] workflow_unit %s loaded -> %s.%s", key, mod_path, cls_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[EE] workflow_unit %s load failed: %s", key, exc)
                ee_registry.register_workflow_unit(key, None)

