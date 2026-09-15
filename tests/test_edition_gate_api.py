"""
全功能版本 Edition / License 模型测试。

双版本取消后，旧用例（CE 下 EE 路由必须 403）已全部过时并删除——
全部功能现在恒可用，配额与服务等级差异由 License 状态机驱动。

本文件覆盖：
  1. /edition/info（匿名可访问）：全集 features、free 态配额（max_hosts=50，其余 None）、
     license.state=free、branding_allowed=False、update_channels=["stable"]、hosts_used
  2. /edition/features：返回 FeatureCode 全集
  3. Gate 行为：has_feature 对全集恒 True、未注册 code False；
     check_quota/is_over_quota 仅 max_hosts 生效，None 配额恒放行
  4. TAURUS_DEV_BYPASS_LICENSE 旁路：state=licensed、professional 配额与白标权益
"""
from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from taurus.editions import get_edition
from taurus.editions.loader import reset_for_testing
from taurus.editions.features import ALL_FEATURE_CODES
from taurus.models import Host

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# 每个用例独立锁定 License 环境：无 License 文件 + 无开发旁路 → free 态
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _free_license(monkeypatch):
    from taurus_ee import license as ee_license

    # 直接钉死文件探测，保证开发机上残留的 license.lic 不会污染测试
    monkeypatch.setattr(ee_license, "_find_license_file", lambda: None)
    monkeypatch.delenv("TAURUS_DEV_BYPASS_LICENSE", raising=False)
    reset_for_testing()
    yield
    reset_for_testing()


# =========================================================================
# /api/taurus/edition/info
# =========================================================================
class TestEditionInfoApi:
    def test_info_匿名访问_返回200与免费版权益(self):
        client = APIClient()
        resp = client.get("/api/taurus/edition/info/")
        assert resp.status_code == 200
        data = resp.data["data"]

        # 兼容字段恒为 community；前端以 tier/state/quota 为准
        assert data["edition"] == "community"
        assert data["tier"] == "community"

        # features 为全集
        assert set(data["features"]) == set(ALL_FEATURE_CODES)
        assert data["feature_count"] == len(ALL_FEATURE_CODES)

        # 免费版仅限制主机 50 台，其余配额全部不限制
        quota = data["quota"]
        assert quota["max_hosts"] == 50
        assert quota["max_users"] is None
        assert quota["max_scheduled_tasks"] is None
        assert quota["max_script_versions_per_script"] is None
        assert quota["max_concurrent_executions"] is None
        assert quota["max_workflows"] is None

        # License 四态 + 服务等级权益
        lic = data["license"]
        assert lic["valid"] is True
        assert lic["state"] == "free"
        assert lic["tier"] == "community"
        assert data["branding_allowed"] is False
        assert data["update_channels"] == ["stable"]
        assert data["service_level"]["level"] == "社区支持"
        assert isinstance(data["hosts_used"], int)
        assert data["hosts_used"] == Host.objects.count()

        # 免费版展示升级引导
        assert data["upgrade"]["show_banner"] is True

    def test_info_feature_groups_全部标记in_edition为真(self):
        client = APIClient()
        resp = client.get("/api/taurus/edition/info/")
        groups = resp.data["data"]["feature_groups"]
        flagged = [item["code"] for grp in groups for item in grp["items"]
                   if not item["in_edition"]]
        assert flagged == []


class TestEditionFeaturesApi:
    def test_features_返回全集(self):
        client = APIClient()
        resp = client.get("/api/taurus/edition/features/")
        assert resp.status_code == 200
        data = resp.data["data"]
        assert data["edition"] == "community"
        assert set(data["features"]) == set(ALL_FEATURE_CODES)


# =========================================================================
# Gate 行为
# =========================================================================
class TestGateFullFeatures:
    def test_has_feature_全集恒真(self):
        gate = get_edition()
        for code in ALL_FEATURE_CODES:
            assert gate.has_feature(code) is True

    def test_has_feature_未注册code为假(self):
        assert get_edition().has_feature("__NOT_REGISTERED_FEATURE__") is False


class TestGateQuotaFreeTier:
    def test_配额_免费版仅主机50其余不限制(self):
        quota = get_edition().quota
        assert quota["max_hosts"] == 50
        assert quota["max_users"] is None
        assert quota["max_workflows"] is None

    def test_is_over_quota_主机超50为真(self):
        gate = get_edition()
        assert gate.is_over_quota("max_hosts", 50) is False
        assert gate.is_over_quota("max_hosts", 51) is True
        # None = 无上限，任何当前值都不超额
        assert gate.is_over_quota("max_users", 10**9) is False

    def test_check_quota_主机达上限抛PermissionDenied(self):
        from rest_framework.exceptions import PermissionDenied
        from taurus.editions.loader import check_quota

        check_quota("max_hosts", 49, "托管主机")  # 未达上限，放行
        with pytest.raises(PermissionDenied):
            check_quota("max_hosts", 50, "托管主机")  # 再注册一台即 51 > 50

    def test_check_quota_不限制字段恒放行(self):
        from taurus.editions.loader import check_quota

        check_quota("max_users", 10**9, "用户")
        check_quota("max_workflows", 10**9, "工作流")


class TestGateLicenseEntitlements:
    def test_免费版_状态与权益(self):
        gate = get_edition()
        assert gate.license_state == "free"
        assert gate.tier == "community"
        assert gate.branding_allowed is False
        assert gate.update_channels == ["stable"]
        assert "level" in gate.service_level

    def test_开发旁路_按professional权益放行(self, monkeypatch):
        monkeypatch.setenv("TAURUS_DEV_BYPASS_LICENSE", "1")
        reset_for_testing()

        gate = get_edition()
        assert gate.license_state == "licensed"
        assert gate.tier == "professional"
        assert gate.get_quota("max_hosts") == 1000
        assert gate.branding_allowed is True
        assert gate.update_channels == ["stable", "lts"]
        assert gate.is_over_quota("max_hosts", 1000) is False
        assert gate.is_over_quota("max_hosts", 1001) is True


class TestBrandingEnforcement:
    def test_免费版_修改品牌键抛PermissionDenied(self):
        from rest_framework.exceptions import PermissionDenied
        from taurus.editions import enforce_branding_config

        with pytest.raises(PermissionDenied):
            enforce_branding_config({"web_title"})
        # 非品牌键 / 空集合放行
        enforce_branding_config({"some_other_config"})
        enforce_branding_config(set())

    def test_专业版_品牌键放行(self, monkeypatch):
        from taurus.editions import BRANDING_CONFIG_KEYS, enforce_branding_config

        monkeypatch.setenv("TAURUS_DEV_BYPASS_LICENSE", "1")
        reset_for_testing()
        enforce_branding_config(set(BRANDING_CONFIG_KEYS))
