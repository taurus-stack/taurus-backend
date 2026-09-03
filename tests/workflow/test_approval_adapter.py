"""S2-05 人工ApprovalAdapterUnit test."""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.django_db


def _make_host():
    from taurus.models import Host
    return Host.objects.create(host_name="wf-approval-host-1", host_ip="10.9.8.1", status=1)


class TestApprovalAdapterClassAttrs:
    """ApprovalAdapterclassattributecheck."""

    def test_registration_and_flags(self):
        from taurus.workflow.engine.registry import get_registry

        r = get_registry()
        assert r.has("approval")
        cls = r.get_class("approval")
        assert cls.node_type == "approval"
        assert cls.display_name == "人工审批"
        assert cls.requires_host is False
        assert cls.is_asynchronous_human is True

    def test_instantiate(self):
        from taurus.workflow.engine.registry import get_registry
        adapter = get_registry().instantiate("approval")
        assert adapter is not None


class TestApprovalAdapterValidateConfig:
    """staticcheck逻辑."""

    @pytest.fixture()
    def adapter(self):
        from taurus.workflow.engine.registry import get_registry
        return get_registry().instantiate("approval")

    def test_validate_requires_approver_for_submit(self, adapter):
        r = adapter.validate_config({"action": "submit"})
        assert r.ok is False
        assert "/params/approver_user_id" in (r.errors or {})
        assert "E0501" in str(r.errors)

    def test_validate_accepts_inform_without_approver(self, adapter):
        r = adapter.validate_config({"action": "inform"})
        assert r.ok is True

    def test_validate_rejects_unknown_action(self, adapter):
        r = adapter.validate_config({"action": "vote", "approver_user_id": 1})
        assert r.ok is False
        assert "/params/action" in (r.errors or {})
        assert "E0201" in str(r.errors)

    def test_validate_rejects_title_too_long(self, adapter):
        r = adapter.validate_config({
            "action": "inform",
            "title": "x" * 201,
        })
        assert r.ok is False
        assert "/params/title" in (r.errors or {})

    def test_validate_rejects_timeout_out_of_range(self, adapter):
        r = adapter.validate_config({
            "action": "inform",
            "timeout_seconds": 10,
        })
        assert r.ok is False
        assert "/params/timeout_seconds" in (r.errors or {})

    def test_validate_submit_with_approver_ok(self, adapter):
        r = adapter.validate_config({
            "action": "submit",
            "approver_user_id": 1,
            "title": "请审批",
        })
        assert r.ok is True


class TestApprovalAdapterRender:
    """InterpolateRender."""

    @pytest.fixture()
    def adapter(self):
        from taurus.workflow.engine.registry import get_registry
        return get_registry().instantiate("approval")

    def test_render_interpolates_title_and_desc(self, adapter):
        from taurus.workflow.engine.context import WorkflowContext

        ctx = WorkflowContext(
            workflow_env={"APP_NAME": "taurus-executor", "VER": "v2"},
            trigger_params={"user": "operator-1"},
        )
        params = {
            "action": "inform",
            "title": "发布 ${workflow.env.APP_NAME} ${workflow.env.VER}",
            "description": "触发者: ${trigger.user}",
        }
        rp = adapter.validate_and_render(params, ctx)
        assert rp["title"] == "发布 taurus-executor v2"
        assert rp["description"] == "触发者: operator-1"


def _mk_cfg(params, dispatch_id="d-approval-1", user_id=1, host_id=None):
    from taurus.workflow.engine.schemas import RenderedNodeConfig
    return RenderedNodeConfig(
        execution_id="exec-a-1",
        node_key="A1",
        node_name="审批节点-1",
        host_id=host_id,
        dispatch_id=dispatch_id,
        attempt_no=1,
        user_id=user_id,
        global_timeout_sec=86400,
        secrets_mask=[],
        params=params,
        triggered_at="2026-01-01T00:00:00",
    )


class TestApprovalAdapterDispatch:
    """dispatch create OpsExecutionApproval Approval单."""

    @pytest.fixture()
    def adapter(self):
        from taurus.workflow.engine.registry import get_registry
        return get_registry().instantiate("approval")

    @pytest.fixture()
    def host(self):
        return _make_host()

    @pytest.fixture()
    def user(self):
        from django.apps import apps
        from django.conf import settings
        UserCls = apps.get_model(settings.AUTH_USER_MODEL)
        # 用 username 或 name create
        try:
            return UserCls.objects.create(username="approver-x")
        except Exception:
            return UserCls.objects.create(name="approver-x")

    def test_dispatch_submit_creates_approval_returns_running(self, adapter, host, user):
        from taurus.workflow.engine.schemas import STATUS_RUNNING
        from taurus.models import OpsExecutionApproval

        params = {
            "action": "submit",
            "approver_user_id": user.pk,
            "title": "请审批上线",
            "description": "版本 v1.0.0 发布",
        }
        cfg = _mk_cfg(params, dispatch_id="d-approval-10")
        out = adapter.dispatch(cfg)
        assert out.status == STATUS_RUNNING
        approval_id = out.adapter_state["approval_id"]
        approval = OpsExecutionApproval.objects.get(pk=approval_id)
        assert approval.status == "pending"
        assert approval.batch_id == "d-approval-10"
        assert approval.approver_id == user.pk
        assert "请审批上线" in approval.submit_desc or "审批节点-1" in approval.submit_desc

    def test_dispatch_inform_no_approver_ok(self, adapter, host):
        from taurus.workflow.engine.schemas import STATUS_RUNNING
        from taurus.models import OpsExecutionApproval

        params = {"action": "inform", "title": "通知"}
        cfg = _mk_cfg(params, dispatch_id="d-approval-20")
        out = adapter.dispatch(cfg)
        assert out.status == STATUS_RUNNING
        approval_id = out.adapter_state["approval_id"]
        approval = OpsExecutionApproval.objects.get(pk=approval_id)
        assert approval.status == "pending"
        assert approval.approver_id is None

    def test_dispatch_idempotent_same_dispatch_id(self, adapter, host):
        from taurus.models import OpsExecutionApproval

        params = {"action": "inform"}
        cfg = _mk_cfg(params, dispatch_id="d-approval-30")
        out1 = adapter.dispatch(cfg)
        out2 = adapter.dispatch(cfg)
        assert out1.adapter_state["approval_id"] == out2.adapter_state["approval_id"]
        assert OpsExecutionApproval.objects.filter(batch_id="d-approval-30").count() == 1

    def test_dispatch_submit_without_approver_returns_failed(self, adapter, host):
        from taurus.workflow.engine.schemas import STATUS_FAILED

        cfg = _mk_cfg({"action": "submit"}, dispatch_id="d-approval-40")
        out = adapter.dispatch(cfg)
        assert out.status == STATUS_FAILED
        assert out.error_message and "E0501" in out.error_message

    def test_dispatch_submit_approver_not_exists(self, adapter, host):
        from taurus.workflow.engine.schemas import STATUS_FAILED

        cfg = _mk_cfg({"action": "submit", "approver_user_id": 9999999}, dispatch_id="d-approval-50")
        out = adapter.dispatch(cfg)
        assert out.status == STATUS_FAILED
        assert out.error_message and "E0502" in out.error_message


class TestApprovalAdapterPoll:
    """poll 回查Approval status."""

    @pytest.fixture()
    def adapter(self):
        from taurus.workflow.engine.registry import get_registry
        return get_registry().instantiate("approval")

    @pytest.fixture()
    def host(self):
        return _make_host()

    @pytest.fixture()
    def approval_factory(self, host):
        from taurus.models import OpsExecutionApproval

        def _factory(status: str = "pending"):
            return OpsExecutionApproval.objects.create(
                batch_id="poll-test-1",
                status=status,
                host=host,
                script_type="sh",
                script_content="#",
                creator_id=1,
                modifier="1",
            )
        return _factory

    def test_poll_pending_returns_running(self, adapter, host, approval_factory):
        from taurus.workflow.engine.schemas import STATUS_RUNNING
        approval = approval_factory("pending")
        out = adapter.poll(_mk_cfg({}, host_id=host.pk), {"approval_id": approval.pk})
        assert out.status == STATUS_RUNNING
        assert out.output["waiting"] is True

    def test_poll_approved_returns_success(self, adapter, host, approval_factory):
        from taurus.workflow.engine.schemas import STATUS_SUCCESS
        approval = approval_factory("approved")
        approval.approve_reason = "同意"
        approval.save(update_fields=["approve_reason", "modifier"])
        out = adapter.poll(_mk_cfg({}, host_id=host.pk), {"approval_id": approval.pk})
        assert out.status == STATUS_SUCCESS
        assert out.output["approved"] is True
        assert out.exit_code == 0

    def test_poll_rejected_returns_failed_with_error_code(self, adapter, host, approval_factory):
        from taurus.workflow.engine.schemas import STATUS_FAILED
        approval = approval_factory("rejected")
        approval.approve_reason = "代码不规范"
        approval.save(update_fields=["approve_reason", "modifier"])
        out = adapter.poll(_mk_cfg({}, host_id=host.pk), {"approval_id": approval.pk})
        assert out.status == STATUS_FAILED
        assert out.error_message and "E5001" in out.error_message
        assert "代码不规范" in out.error_message
        assert out.output["rejected"] is True

    def test_poll_cancelled_returns_cancelled_status(self, adapter, host, approval_factory):
        from taurus.workflow.engine.schemas import STATUS_CANCELLED
        approval = approval_factory("cancelled")
        out = adapter.poll(_mk_cfg({}, host_id=host.pk), {"approval_id": approval.pk})
        assert out.status == STATUS_CANCELLED

    def test_poll_missing_state_returns_failed(self, adapter, host):
        from taurus.workflow.engine.schemas import STATUS_FAILED
        out = adapter.poll(_mk_cfg({}, host_id=host.pk), None)
        assert out.status == STATUS_FAILED
        assert out.error_message and "E0504" in out.error_message


class TestApprovalAdapterCancel:
    """cancel 撤回Approval单."""

    @pytest.fixture()
    def adapter(self):
        from taurus.workflow.engine.registry import get_registry
        return get_registry().instantiate("approval")

    @pytest.fixture()
    def host(self):
        return _make_host()

    @pytest.fixture()
    def pending_approval(self, host):
        from taurus.models import OpsExecutionApproval
        return OpsExecutionApproval.objects.create(
            batch_id="cancel-test-1",
            status="pending",
            host=host,
            script_type="sh",
            script_content="#",
            creator_id=1,
            modifier="1",
        )

    def test_cancel_pending_marks_cancelled(self, adapter, host, pending_approval):
        from taurus.workflow.engine.schemas import STATUS_CANCELLED
        out = adapter.cancel(
            _mk_cfg({}, host_id=host.pk),
            {"approval_id": pending_approval.pk},
        )
        assert out.status == STATUS_CANCELLED
        pending_approval.refresh_from_db()
        assert pending_approval.status == "cancelled"
        assert "ABORTED" in (pending_approval.approve_reason or "")

    def test_cancel_approved_terminal_noop_success(self, adapter, host):
        from taurus.models import OpsExecutionApproval
        from taurus.workflow.engine.schemas import STATUS_SUCCESS

        approval = OpsExecutionApproval.objects.create(
            batch_id="cancel-test-2",
            status="approved",
            host=host,
            script_type="sh",
            script_content="#",
            creator_id=1,
            modifier="1",
        )
        out = adapter.cancel(
            _mk_cfg({}, host_id=host.pk),
            {"approval_id": approval.pk},
        )
        assert out.status == STATUS_SUCCESS
        approval.refresh_from_db()
        assert approval.status == "approved"

    def test_cancel_without_state_noop_success(self, adapter, host):
        from taurus.workflow.engine.schemas import STATUS_SUCCESS
        out = adapter.cancel(_mk_cfg({}, host_id=host.pk), None)
        assert out.status == STATUS_SUCCESS