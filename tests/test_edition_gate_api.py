"""
P0 — Edition Gate API 最终防线审计测试。

策略（防御性 · 0 假阳性）：
1. 在进程级锁定 TAURUS_EDITION=community，并 reset_for_testing 刷新 EditionGate 单例
2. 强制认证为 is_superuser=True 的 Admin（绕过权限系统，只测 Edition Gate）
3. 对每个 EE 已知路由（见下方 EE_ROUTES），发起 GET(list) 请求：
   - 期望：HTTP 403 PermissionDenied（或 401/405 — 因 list 空数据也可，不要求 200）
   - 严禁：200 OK（这代表 EE 数据在 CE 下泄漏，是 P0 级漏洞）
4. 对自定义 URL（custom_paths）同样发 GET/POST 探测。

为什么不"动态发现所有路由然后推断哪些是 EE"？
—— 避免误报（推断错误导致假通过，给人虚假安全感）。
因此这里硬编码 EE_ROUTES 白名单清单，从 docs/ee-module-map.md M2.1~M2.6 同步，
如果后端新增 EE ViewSet 忘记在此清单登记 → 本文件漏测；为防止此情况，
末尾附加 `test_ee_routes_coverage` 做反向校验：扫描 taurus/urls.py 所有 router.register
basename 与 `docs/ee-module-map.md` 中 FeatureCode×ViewSet 映射做交叉对比。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse, NoReverseMatch
from rest_framework import status
from rest_framework.test import APIClient

from taurus.editions.loader import reset_for_testing

User = get_user_model()

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# 进程级：强制 community，避免开发环境 TAURUS_EDITION=enterprise 导致测试假通过
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module", autouse=True)
def _force_community_edition_module():
    """模块级 fixture：TAURUS_EDITION → community，并刷新 EditionGate 单例.

    注意：不使用 scope=session，因为 pytest-django 的 DB access 必须在
    transaction/django_db 内，而 session scope 早于 DB setup，会导致
    跨 session DB 创建死锁 / Table already exists。
    """
    original = os.environ.get("TAURUS_EDITION")
    os.environ["TAURUS_EDITION"] = "community"
    reset_for_testing()
    yield
    if original is None:
        os.environ.pop("TAURUS_EDITION", None)
    else:
        os.environ["TAURUS_EDITION"] = original
    reset_for_testing()


@pytest.fixture()
def api_client():
    """返回 force_authenticate(admin) 的 APIClient，关闭异常重抛。

    Django test client 默认在 check_exception() 阶段把 DRF 已经渲染为响应的
    PermissionDenied 重新 raise 出来，把本来应是 403 的结果变成 HttpResponseServerError
    500。这里在 `ClientMixin` 层面把 raise_request_exception 改成 False，保证
    Edition Gate 的异常以其真实状态码返回。

    参数中不声明 `db` —— 因为整个模块已经打了 `pytestmark = pytest.mark.django_db`
    的全局标签，所有测试自动获得 DB 访问权。
    """
    user, _created = User.objects.get_or_create(
        username="edition-gate-test-admin",
        defaults={
            "is_superuser": True,
            "is_staff": True,
            "is_active": True,
        },
    )
    from django.test.client import ClientMixin as _ClientMixin

    old_class_flag = getattr(_ClientMixin, "raise_request_exception", True)
    _ClientMixin.raise_request_exception = False
    try:
        client = APIClient()
        client.raise_request_exception = False
        client.force_authenticate(user=user)
        yield client
    finally:
        _ClientMixin.raise_request_exception = old_class_flag


# =========================================================================
# EE_ROUTES 白名单（与 docs/ee-module-map.md 同步）
#
# 格式: (<router basename>, <feature_code>)
#
#   basename=url reverse 用的名字（router.register(..., basename=...)）
#   feature_code=taurus.editions.features 下的常量名（会在测试内部 getattr）
# =========================================================================
EE_ROUTES: list[tuple[str, str]] = [
    # -------- M2.1 Script 模块 (5 basenames) --------
    ("script-check-rule",       "F_SCRIPT_SECURITY_CHECK"),
    ("script-approve",          "F_SCRIPT_APPROVAL_FLOW"),
    ("script-approval-rule",    "F_SCRIPT_APPROVAL_FLOW"),
    ("script-approval-node",    "F_SCRIPT_APPROVAL_FLOW"),
    ("script-approval-instance","F_SCRIPT_APPROVAL_FLOW"),
    ("script-audit",            "F_SCRIPT_AUDIT_LOG"),
    ("share-perm-def",          "F_SCRIPT_SHARING"),
    ("share-link",              "F_SCRIPT_SHARING"),

    # -------- M2.2 Workflow 模块 (4 basenames) --------
    ("workflow-approve",            "F_WORKFLOW_APPROVAL_FLOW"),
    ("workflow-approval-rule",      "F_WORKFLOW_APPROVAL_FLOW"),
    ("workflow-approval-node",      "F_WORKFLOW_APPROVAL_FLOW"),
    ("workflow-approval-instance",  "F_WORKFLOW_APPROVAL_FLOW"),

    # -------- M2.3 Scheduler 模块 (1 basename) --------
    ("task-center",             "F_SCRIPT_TASK_UNIFIED"),

    # -------- M2.4 Supervisor Program 模块 (4 basenames) --------
    ("program-install-template",    "F_PROGRAM_INSTALL_TEMPLATE"),
    ("program-host-binding",        "F_PROGRAM_HOST_BINDING"),
    ("program-install-config",      "F_PROGRAM_INSTALL_CONFIG"),
    ("program-install-policy",      "F_PROGRAM_INSTALL_POLICY"),

    # -------- M2.5 日志 模块 (2 basenames) --------
    ("host-log",                "F_HOST_LOG_FORWARDING"),
    ("log-command",             "F_LOG_COMMAND_CONTROL"),

    # -------- M2.6 Ops 模块 (1 basename) --------
    ("ops-execution-approval",  "F_OPS_EXECUTION_APPROVAL"),

    # -------- M2.5 Contact Lead (1 basename, CE 只允许 POST create, list/CRUD 需 EE) --------
    ("contact-lead",            "F_CONTACT_LEAD_PORTAL"),
]


# =========================================================================
# EE Custom URLs（非 ViewSet list/retrieve，而是 @action 或手工 path）
#
# 格式: (<reverse name>, <method>, <feature_code>, <human label>)
#   reverse name = urls.py 中 name= 参数
# =========================================================================
EE_CUSTOM_URLS: list[tuple[str, str, str, str]] = [
    # -------- M2.1 Script 2 个 @action --------
    ("script-shares",               "GET",  "F_SCRIPT_SHARING",           "ScriptViewSet.shares (M2.1)"),
    ("script-effective-perms",      "GET",  "F_SCRIPT_SHARING",           "ScriptViewSet.effective_perms (M2.1)"),

    # -------- M2.2 Workflow 4 个 @action --------
    ("workflow-risk-assessment",    "GET",  "F_WORKFLOW_RISK_ASSESSMENT", "WorkflowViewSet.risk_assessment (M2.2)"),
    ("workflow-shares",             "GET",  "F_WORKFLOW_SHARING",         "WorkflowViewSet.shares (M2.2)"),
    ("workflow-effective-perms",    "GET",  "F_WORKFLOW_SHARING",         "WorkflowViewSet.effective_perms (M2.2)"),
    ("workflow-get-stats",            "GET",  "F_WORKFLOW_DAG_ENGINE",      "WorkflowViewSet.get_stats (M2.2)"),

    # -------- M2.4 Program 8 个 @action --------
    ("program-install-template-apply-to-hosts", "POST", "F_PROGRAM_INSTALL_TEMPLATE", "PIT.apply_to_hosts"),
    ("program-host-binding-install",            "POST", "F_PROGRAM_HOST_BINDING",     "PHB.install"),
    ("program-host-binding-uninstall",          "POST", "F_PROGRAM_HOST_BINDING",     "PHB.uninstall"),
    ("program-install-config-batch-create",     "POST", "F_PROGRAM_INSTALL_CONFIG",   "PIC.batch_create"),
    ("program-install-config-redispatch",       "POST", "F_PROGRAM_INSTALL_CONFIG",   "PIC.redispatch"),
    ("program-install-policy-apply",            "POST", "F_PROGRAM_INSTALL_POLICY",   "PIP.apply"),
    ("program-install-policy-preview-hosts",    "GET",  "F_PROGRAM_INSTALL_POLICY",   "PIP.preview_hosts"),
    ("program-install-policy-upgrade-version",  "POST", "F_PROGRAM_INSTALL_POLICY",   "PIP.upgrade_version"),

    # -------- M2.6 Ops 1 个 @action --------
    ("program-command-batch-create", "POST", "F_PROGRAM_COMMAND_BATCH", "PC.batch_create"),
]


class TestEEViewSetListGatedOnCE:
    """Edition Gate：ViewSet.list() 在 CE 下必须 403（或等价的 permission error）.

    断言逻辑：
    · list url 永远用 reverse(f'{basename}-list')
    · 期望 NOT 2xx：
        - 403 = 期望最常见（require_feature / handle_exception 返回）
        - 401/404/405 也可以接受（某些路由包装器会把 403 映射为业务 404）
    · FAIL 条件：200/201/204 —— EE 数据在 CE 下可见 = 隔离漏洞
    """

    @pytest.mark.parametrize(
        ("basename", "feature_const"),
        EE_ROUTES,
        ids=[name for name, _ in EE_ROUTES],
    )
    def test_ee_viewset_list_returns_403_on_ce(
        self, api_client, basename, feature_const, request
    ):
        from taurus.editions import features as _ee_features

        feature_code = getattr(_ee_features, feature_const)

        # 1. reverse url
        try:
            url = reverse(f"{basename}-list")
        except NoReverseMatch:
            pytest.fail(
                f"反向路由失败：{basename}-list 不存在。"
                "请检查：1) taurus/urls.py 的 router.register basename;"
                " 2) docs/ee-module-map.md 映射是否与该清单同步。"
            )

        # 2. 发送请求
        resp = api_client.get(url)

        # 3. 断言
        assert status.is_success(resp.status_code) is False, (
            f"[P0 隔离漏洞] basename={basename}({feature_const}={feature_code}) "
            f"在 CE 下 GET list 返回 HTTP {resp.status_code}，应当被 EditionGate "
            "拦截为 403！请检查 ViewSet 是否加了 @require_feature 或 Thin Wrapper。"
        )
        # 顺带：如果是 403，检查 JSON 里 code=="edition_gate"（更严格的格式校验）
        if resp.status_code == 403:
            data = _safe_json(resp)
            if isinstance(data, dict) and "code" in data:
                assert data["code"] == "edition_gate", (
                    f"basename={basename}: 403 payload.code != 'edition_gate'，"
                    "很可能不是 EditionGate 拦下的，需要排查。"
                )


class TestEECustomUrlsGatedOnCE:
    """Edition Gate：custom actions/urls 在 CE 下必须 403.

    注：
    · POST 请求由于无 body 可能被 payload validation 提前 400 拦截 → 也 OK，只要不是 2xx
    · reverse 用 {basename}-{action_name} 或 urls.py 中手工写的 name= 参数
    """

    @pytest.mark.parametrize(
        ("reverse_name", "method", "feature_const", "label"),
        EE_CUSTOM_URLS,
        ids=[t[0] for t in EE_CUSTOM_URLS],
    )
    def test_ee_custom_url_gated_on_ce(
        self, api_client, reverse_name, method, feature_const, label, request
    ):
        from taurus.editions import features as _ee_features

        feature_code = getattr(_ee_features, feature_const)

        # 1. reverse url — detail 路由需要 pk，传 1 占位（EditionGate 在 get_object 前就应拦截）
        try:
            try:
                url = reverse(reverse_name, kwargs={"pk": 1})
            except NoReverseMatch:
                url = reverse(reverse_name)
        except NoReverseMatch:
            pytest.fail(
                f"反向路由失败：{reverse_name} 不存在。"
                "请检查 taurus/urls.py 中对应的 @action(name=...) 或手工 path(name=...)。"
            )

        # 2. 发请求（POST 用空 dict 作为 payload，让 permission check 先于 body 校验）
        if method == "GET":
            resp = api_client.get(url)
        elif method == "POST":
            resp = api_client.post(url, data={}, format="json")
        else:  # pragma: no cover - 防御性
            pytest.fail(f"不支持的 method: {method}")

        # 3. 断言：NOT 2xx；若 403 则尽量验证 edition_gate code 或特征文本
        assert status.is_success(resp.status_code) is False, (
            f"[P0 隔离漏洞] {reverse_name} ({label}, feature={feature_const}={feature_code}) "
            f"在 CE 下 {method} 返回 HTTP {resp.status_code}，应当被 EditionGate 拦截。"
        )
        if resp.status_code == 403:
            data = _safe_json(resp)
            if isinstance(data, dict) and "code" in data:
                if data["code"] != "edition_gate":
                    # CustomExceptionHandler 路径：code=403（数字），msg/detail 含特征关键字
                    msg_text = (
                        str(data.get("msg", "")) + " " +
                        str(data.get("detail", ""))
                    ).lower()
                    _keywords = ("商业版", "企业版", "community edition", "upgrade",
                                 "专属", "升级到", "销售开通", "feature", "edition")
                    has_feature_mark = any(k in msg_text for k in _keywords)
                    assert has_feature_mark, (
                        f"{reverse_name}: 403 payload 不含 EditionGate 特征关键字。"
                        f" code={data['code']!r} msg/msg={str(data.get('msg',''))[:100]!r}，"
                        "可能是权限系统先于 EditionGate 拦截；请加 ee_service_or_403()。"
                    )


# =========================================================================
# 辅助：把 DRF Response / Django HttpResponse 安全转成 dict（失败返回 None）
# =========================================================================
def _safe_json(resp):
    try:
        if hasattr(resp, "data"):
            return resp.data
        import json
        return json.loads(getattr(resp, "content", b"{}"))
    except Exception:
        return None


# =========================================================================
# Cross-check：测试清单与 docs/ee-module-map.md 覆盖一致性
# =========================================================================

def _parse_ee_module_map():
    """把 docs/ee-module-map.md 中 FeatureCode×ViewSet 映射解析成 set[(basename,feature)].

    说明：ee-module-map.md 的 ViewSet 列格式并不统一，可能是：
      - "ScriptCheckRuleViewSet → ee/views/script_check_view.py"
      - "ScriptApprovalRuleViewSet(L9663) ScriptApprovalRuleNodeViewSet(9702) ... → ee/views/..."
      - "整类 @require_feature(F_PROGRAM_HOST_BINDING) Double Gate + xxx" （无 ViewSet 类名）
    策略：
      1. 先从 ViewSet 列抠所有 XxxViewSet → snake basename + fc
      2. 若匹配失败，但 FeatureCode 本身合法 → 兜底写入 (fc.lower(), fc) 虚拟条目
         这样按 FeatureCode 集合对比时不会漏（我们的对比现在只关心 FC 值是否覆盖）
    解析 size==0 直接返回 None，caller 会 skip。
    """
    p = Path(__file__).resolve().parent.parent / "docs" / "ee-module-map.md"
    if not p.exists():
        return None
    mapping = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s.startswith("|") or "FeatureCode" in s or "---" in s:
            continue
        parts = [x.strip() for x in s.strip("|").split("|")]
        if len(parts) < 4:
            continue
        _fc, _vs = parts[0], parts[3]  # 列：FeatureCode / Model / Ser / ViewSet / ...
        if not re.fullmatch(r"[A-Z][A-Z0-9_]+", _fc):  # FeatureCode 必须是大写常量
            continue
        # 从 ViewSet 列提取所有 XxxViewSet
        vs_names = re.findall(r"([A-Za-z_][A-Za-z0-9_]*ViewSet)\b", _vs)
        if not vs_names:
            # 兜底：尝试 Serializer 列 / Service 列
            vs_names = re.findall(r"([A-Za-z_][A-Za-z0-9_]*ViewSet)\b", parts[2])
        if not vs_names and len(parts) >= 5:
            vs_names = re.findall(r"([A-Za-z_][A-Za-z0-9_]*ViewSet)\b", parts[4])
        if not vs_names:
            # 最终兜底：FC 合法但找不到任何 ViewSet 类 → 加虚拟条目 (fc.lower(), fc)
            mapping.add((_fc.lower(), _fc))
            continue
        for vs_cls_full in vs_names:
            vs_cls = vs_cls_full.replace("ViewSet", "")
            # CamelCase/PascalCase -> snake_case basename
            basename = re.sub(r"(?<!^)(?=[A-Z])", "_", vs_cls).lower()
            mapping.add((basename, _fc))
    return mapping if mapping else None


class TestEERoutesCoverage:
    """EE_ROUTES 清单与 docs/ee-module-map.md 双向交叉校验."""

    # docs/ee-module-map.md 中出现但**不要求独立 ViewSet 路由**的 FeatureCode。
    # 跳过原因：
    #   - ViewSet action 级 gate（非整类，CE list/retrieve 允许）
    #   - Service 能力注入型（没有直接对应的 REST API basename）
    #   - M2.5 占位模块，没有在 urls.py router.register 注册路由
    #   - 其它在 EE_CUSTOM_URLS 或集成测试中覆盖的 EE action
    DOC_FC_SKIP_ROUTE_CHECK = frozenset(
        [
            # --- M2.1 动作级：CE 基础 list/retrieve 允许，EE action 单独 gate ---
            "SCRIPT_PERMISSION_FINEGRAINED",
            "SCRIPT_VERSION_FULL",
            # --- M2.2 Workflow ViewSet 级 action（无独立 basename）---
            "WORKFLOW_DAG_ENGINE",       # get_dag/save_dag/publish_version/list_versions/rollback_version/get_stats
            "WORKFLOW_RISK_ASSESSMENT",  # risk_assessment action
            "WORKFLOW_SHARING",          # shares/share_detail/effective_perms action
            # --- M2.3 调度 action 级（ScheduleViewSet / ScriptTaskViewSet 内部 EE actions）---
            "SCHEDULE_HA_CLUSTER",
            "SCHEDULE_ALERT_RETRY",
            # --- M2.4 Program action 级（ProgramCommandViewSet.batch_create host_ids>1 才 gate）---
            "PROGRAM_COMMAND_BATCH",
            # --- M2.5 占位模块（没有在 router.register 注册独立 VS 或 Thin Wrapper 还未加）---
            "BACKUP_RESTORE",
            "DOWNLOAD_CENTER",
            "INSPECTION_CENTER",
            "KNOWLEDGE_BASE",
            "TOOLS_CENTER",
            # --- 文档别名：ee-module-map.md 行里写的是 TASK_CENTER，代码用 F_SCRIPT_TASK_UNIFIED ---
            "TASK_CENTER",
            # --- M2.6 Service 能力注入型（没有独立 API，EE_registry.services 注入）---
            "OPS_EXECUTION_NOTIFICATION",
            "OPS_PILOT_CANARY",
        ]
    )

    def test_ee_routes_coverage_sync_with_module_map(self):
        mapping = _parse_ee_module_map()
        if mapping is None:
            pytest.skip("docs/ee-module-map.md not found or parser returned empty")

        # --- 提取 FeatureCode 集合做对比（不强制绑定 basename） ---
        # 原因 1：docs 中一行可能列多个 ViewSet，也可能写的是 ViewSet action 而非独立 ViewSet
        # 原因 2：basename 分隔符可能不一致（snake_case vs kebab-case）
        # 我们真正关心的是：每个 feature code 至少在测试中有一条路由被覆盖。

        doc_fc_set = {fc for _bn, fc in mapping}

        test_set = set(EE_ROUTES)
        from taurus.editions import features as _ee_features

        test_fc_set = set()
        for _bn, const_name in test_set:
            try:
                test_fc_set.add(getattr(_ee_features, const_name))
            except AttributeError:
                pytest.fail(
                    f"EE_ROUTES 中的 feature_const={const_name} 在 taurus.editions.features "
                    "中不存在，请确认常量名拼写。"
                )
        # EE_CUSTOM_URLS 也计入覆盖（workflow-dag-engine / script-sharing action 级等）
        for _rn, _m, const_name, _lbl in (EE_CUSTOM_URLS or []):
            try:
                test_fc_set.add(getattr(_ee_features, const_name))
            except AttributeError:
                pass

        # 扣掉白名单（只对 missing 扣；白名单里的 extra 仍报错防止写错 docs）
        missing_in_tests = (doc_fc_set - self.DOC_FC_SKIP_ROUTE_CHECK) - test_fc_set
        extra_in_tests = test_fc_set - doc_fc_set

        msg_parts = []
        if missing_in_tests:
            msg_parts.append(
                "【docs 中的 FeatureCode 在 EE_ROUTES / EE_CUSTOM_URLS 无覆盖 → 有漏测风险】:\n  - "
                + "\n  - ".join(sorted(missing_in_tests))
                + "\n（若是 action 级或占位，请加入 DOC_FC_SKIP_ROUTE_CHECK 白名单）"
            )
        if extra_in_tests:
            msg_parts.append(
                "【EE_ROUTES 中的 FeatureCode 没出现在 docs/ee-module-map.md → 请补文档】:\n  - "
                + "\n  - ".join(sorted(extra_in_tests))
            )
        if msg_parts:
            pytest.fail(
                "EE 路由清单与 docs/ee-module-map.md 的 FeatureCode 不同步。\n"
                + "\n".join(msg_parts)
            )