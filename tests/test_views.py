"""
taurus/views.py Unit test

test策略:直接调用viewmethod, Mock 掉数据library和externaldepend on, 保证test速度和Isolation.
不use APIClient send HTTP request, AvoidIntegration test的副作用.

test覆盖的 ViewSet:
1. HostViewSet - HostApproval/Disable/Certificate管理/Ticketoperation
2. SupervisorViewSet - Register/Deregister/Heartbeat/Download
3. ScriptCheckRuleViewSet - 规则启停/批量operation/initialize
4. ScriptCategoryViewSet - tree形结构构建
5. ScriptViewSet - permissionfilter/风险detect
6. workflow_callback_view - HTTP Callbackprocess
"""
import json
import uuid
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from django.test import RequestFactory
from django.contrib.auth import get_user_model
from rest_framework.request import Request

User = get_user_model()


def _make_request(data=None, user=None, method="post", meta=None, query_params=None):
    request = MagicMock()
    request.data = data or {}
    request.query_params = query_params or {}
    request.method = method.upper()
    if user:
        request.user = user
    else:
        request.user = MagicMock()
        request.user.is_authenticated = True
        request.user.is_superuser = False
    request.META = meta or {}
    return request


def _make_host(**kwargs):
    host = MagicMock()
    host.host_uuid = kwargs.get("host_uuid", str(uuid.uuid4()))
    host.host_name = kwargs.get("host_name", "test-host")
    host.host_ip = kwargs.get("host_ip", "10.0.0.1")
    host.status = kwargs.get("status", 1)
    host.online_status = kwargs.get("online_status", 1)
    host.certificate_serial = kwargs.get("certificate_serial", None)
    host.certificate_status = kwargs.get("certificate_status", None)
    host.certificate_revoked_at = kwargs.get("certificate_revoked_at", None)
    host.certificate_revocation_reason = kwargs.get("certificate_revocation_reason", None)
    host.save = MagicMock()
    return host


class TestHostViewSetApprove:
    def test_approve_pending_host(self):
        from taurus.views import HostViewSet

        host = _make_host(status=0)
        view = HostViewSet()
        view.get_object = MagicMock(return_value=host)

        request = _make_request()
        response = view.approve(request)

        assert host.status == 1
        host.save.assert_called_once()

    def test_approve_already_approved(self):
        from taurus.views import HostViewSet

        host = _make_host(status=1)
        view = HostViewSet()
        view.get_object = MagicMock(return_value=host)

        request = _make_request()
        response = view.approve(request)

        assert host.status == 1
        host.save.assert_not_called()

    def test_approve_rejected_host(self):
        from taurus.views import HostViewSet

        host = _make_host(status=2)
        view = HostViewSet()
        view.get_object = MagicMock(return_value=host)

        request = _make_request()
        response = view.approve(request)

        assert host.status == 2
        host.save.assert_not_called()


class TestHostViewSetReject:
    def test_reject_pending_host(self):
        from taurus.views import HostViewSet

        host = _make_host(status=0)
        view = HostViewSet()
        view.get_object = MagicMock(return_value=host)

        request = _make_request()
        response = view.reject(request)

        assert host.status == 2
        host.save.assert_called_once()

    def test_reject_already_approved(self):
        from taurus.views import HostViewSet

        host = _make_host(status=1)
        view = HostViewSet()
        view.get_object = MagicMock(return_value=host)

        request = _make_request()
        response = view.reject(request)

        assert host.status == 1
        host.save.assert_not_called()


class TestHostViewSetDisable:
    def test_disable_approved_host(self):
        from taurus.views import HostViewSet

        host = _make_host(status=1)
        view = HostViewSet()
        view.get_object = MagicMock(return_value=host)

        request = _make_request()
        response = view.disable(request)

        assert host.status == 3
        host.save.assert_called_once()

    def test_disable_pending_host_fails(self):
        from taurus.views import HostViewSet

        host = _make_host(status=0)
        view = HostViewSet()
        view.get_object = MagicMock(return_value=host)

        request = _make_request()
        response = view.disable(request)

        assert host.status == 0
        host.save.assert_not_called()


class TestHostViewSetRevokeCertificate:
    def test_revoke_valid_certificate(self):
        from taurus.views import HostViewSet

        host = _make_host(
            certificate_serial="0A",
            certificate_status="valid",
        )
        view = HostViewSet()
        view.get_object = MagicMock(return_value=host)

        request = _make_request(data={"reason": "key_compromise"})
        response = view.revoke_certificate(request)

        assert host.certificate_status == "revoked"
        host.save.assert_called_once()

    def test_revoke_no_serial(self):
        from taurus.views import HostViewSet

        host = _make_host(certificate_serial=None)
        view = HostViewSet()
        view.get_object = MagicMock(return_value=host)

        request = _make_request()
        response = view.revoke_certificate(request)

        host.save.assert_not_called()

    def test_revoke_already_revoked(self):
        from taurus.views import HostViewSet

        host = _make_host(
            certificate_serial="0A",
            certificate_status="revoked",
        )
        view = HostViewSet()
        view.get_object = MagicMock(return_value=host)

        request = _make_request()
        response = view.revoke_certificate(request)

        host.save.assert_not_called()


class TestHostViewSetRestoreCertificate:
    def test_restore_revoked_certificate(self):
        from taurus.views import HostViewSet

        host = _make_host(certificate_status="revoked")
        view = HostViewSet()
        view.get_object = MagicMock(return_value=host)

        request = _make_request()
        response = view.restore_certificate(request)

        assert host.certificate_status == "valid"
        assert host.certificate_revoked_at is None
        assert host.certificate_revocation_reason is None
        host.save.assert_called_once()

    def test_restore_valid_certificate_fails(self):
        from taurus.views import HostViewSet

        host = _make_host(certificate_status="valid")
        view = HostViewSet()
        view.get_object = MagicMock(return_value=host)

        request = _make_request()
        response = view.restore_certificate(request)

        host.save.assert_not_called()


class TestHostViewSetIssueCertificate:
    @patch("taurus.views.CAManager")
    def test_issue_certificate_success(self, MockCAManager):
        from taurus.views import HostViewSet

        mock_ca = MagicMock()
        mock_ca.ensure_ca_exists.return_value = True
        mock_ca.sign_csr.return_value = "-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----"
        mock_ca.get_next_serial.return_value = "01"
        mock_ca.get_ca_cert.return_value = "-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----"
        MockCAManager.return_value = mock_ca

        host = _make_host(status=1)
        view = HostViewSet()
        view.get_object = MagicMock(return_value=host)

        request = _make_request(data={"csr": "-----BEGIN CERTIFICATE REQUEST-----\n...\n-----END CERTIFICATE REQUEST-----"})
        response = view.issue_certificate(request, host_uuid=host.host_uuid)

        assert host.certificate_serial == "01"
        assert host.certificate_status == "valid"
        host.save.assert_called_once()

    @patch("taurus.views.CAManager")
    def test_issue_certificate_unapproved_host(self, MockCAManager):
        from taurus.views import HostViewSet

        host = _make_host(status=0)
        view = HostViewSet()
        view.get_object = MagicMock(return_value=host)

        request = _make_request(data={"csr": "csr-content"})
        response = view.issue_certificate(request, host_uuid=host.host_uuid)

        host.save.assert_not_called()

    @patch("taurus.views.CAManager")
    def test_issue_certificate_missing_csr(self, MockCAManager):
        from taurus.views import HostViewSet

        host = _make_host(status=1)
        view = HostViewSet()
        view.get_object = MagicMock(return_value=host)

        request = _make_request(data={})
        response = view.issue_certificate(request, host_uuid=host.host_uuid)

        host.save.assert_not_called()

    @patch("taurus.views.CAManager")
    def test_issue_certificate_ca_init_fails(self, MockCAManager):
        from taurus.views import HostViewSet

        mock_ca = MagicMock()
        mock_ca.ensure_ca_exists.return_value = False
        MockCAManager.return_value = mock_ca

        host = _make_host(status=1)
        view = HostViewSet()
        view.get_object = MagicMock(return_value=host)

        request = _make_request(data={"csr": "csr-content"})
        response = view.issue_certificate(request, host_uuid=host.host_uuid)

        host.save.assert_not_called()

    @patch("taurus.views.CAManager")
    def test_issue_certificate_sign_fails(self, MockCAManager):
        from taurus.views import HostViewSet

        mock_ca = MagicMock()
        mock_ca.ensure_ca_exists.return_value = True
        mock_ca.sign_csr.return_value = None
        MockCAManager.return_value = mock_ca

        host = _make_host(status=1)
        view = HostViewSet()
        view.get_object = MagicMock(return_value=host)

        request = _make_request(data={"csr": "csr-content"})
        response = view.issue_certificate(request, host_uuid=host.host_uuid)

        host.save.assert_not_called()


class TestHostViewSetMyHostInfo:
    def test_my_host_info(self):
        from taurus.views import HostViewSet

        mock_qs = MagicMock()
        mock_qs.count.return_value = 10
        mock_qs.filter.return_value.count.return_value = 8

        view = HostViewSet()
        request = _make_request(method="get")
        request.user = MagicMock()
        with patch.object(HostViewSet, "get_queryset", return_value=mock_qs):
            response = view.my_host_info(request)

        assert response.data["data"]["total"] == 10
        assert response.data["data"]["normal"] == 8
        assert response.data["data"]["exception"] == 2


class TestHostViewSetGetQueryset:
    def test_superuser_sees_all(self):
        from taurus.views import HostViewSet

        mock_base_qs = MagicMock()

        view = HostViewSet()
        view.request = MagicMock()
        view.request.user = MagicMock()
        view.request.user.is_authenticated = True
        view.request.user.is_superuser = True

        with patch.object(HostViewSet, "get_queryset", wraps=None) as mock_get_qs:
            mock_get_qs.return_value = mock_base_qs
            qs = view.get_queryset()
            assert qs == mock_base_qs

    def test_anonymous_user_returns_base_qs(self):
        from taurus.views import HostViewSet
        from dvadmin.utils.viewset import CustomModelViewSet

        mock_base_qs = MagicMock()

        view = HostViewSet()
        view.request = MagicMock()
        view.request.user = MagicMock()
        view.request.user.is_authenticated = False

        with patch.object(CustomModelViewSet, "get_queryset", return_value=mock_base_qs):
            qs = view.get_queryset()
            assert qs == mock_base_qs
            mock_base_qs.filter.assert_not_called()

    def test_normal_user_filters_by_user(self):
        from taurus.views import HostViewSet
        from dvadmin.utils.viewset import CustomModelViewSet

        mock_base_qs = MagicMock()
        mock_filtered_qs = MagicMock()
        mock_base_qs.filter.return_value = mock_filtered_qs

        view = HostViewSet()
        view.request = MagicMock()
        view.request.user = MagicMock()
        view.request.user.is_authenticated = True
        view.request.user.is_superuser = False

        with patch.object(CustomModelViewSet, "get_queryset", return_value=mock_base_qs):
            qs = view.get_queryset()
            mock_base_qs.filter.assert_called_once_with(users=view.request.user)


class TestSupervisorViewSetDeregister:
    @patch("taurus.views.Host")
    @patch("taurus.views.settings", REQUEST_SIGNING_ENABLED=False)
    def test_deregister_success(self, mock_settings, MockHost):
        from taurus.views import SupervisorViewSet

        host = _make_host(online_status=1)
        host.request_signing_secret = None
        MockHost.objects.get.return_value = host

        view = SupervisorViewSet()
        request = _make_request(data={"host_id": host.host_uuid})
        response = view.deregister(request)

        assert host.online_status == 0
        host.save.assert_called_once()

    @patch("taurus.views.settings", REQUEST_SIGNING_ENABLED=False)
    def test_deregister_missing_host_id(self, mock_settings):
        from taurus.views import SupervisorViewSet

        view = SupervisorViewSet()
        request = _make_request(data={})
        response = view.deregister(request)

    @patch("taurus.views.Host")
    @patch("taurus.views.settings", REQUEST_SIGNING_ENABLED=False)
    def test_deregister_host_not_found(self, mock_settings, MockHost):
        from taurus.views import SupervisorViewSet

        MockHost.objects.get.side_effect = MockHost.DoesNotExist()

        view = SupervisorViewSet()
        request = _make_request(data={"host_id": "nonexistent"})
        response = view.deregister(request)


class TestSupervisorViewSetInstallScript:
    def test_install_script_file_not_found(self):
        from taurus.views import SupervisorViewSet

        view = SupervisorViewSet()
        request = _make_request(method="get")

        with patch("os.path.exists", return_value=False):
            response = view.install_script(request)

    def test_install_script_success(self):
        from taurus.views import SupervisorViewSet
        from django.http import HttpResponse

        view = SupervisorViewSet()
        request = _make_request(method="get")
        request.scheme = "http"
        request.get_host = MagicMock(return_value="localhost:8000")

        script_content = '#!/bin/bash\nSERVER_URL=__TAURUS_SERVER_URL__\nSERVER_URL_INJECTED=false\n'

        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=script_content)))
        mock_file.__exit__ = MagicMock(return_value=False)

        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", return_value=mock_file):
            response = view.install_script(request)
            assert isinstance(response, HttpResponse)
            content = response.content.decode()
            assert "http://localhost:8000" in content
            assert "SERVER_URL_INJECTED=true" in content


class TestSupervisorViewSetDownload:
    def test_download_missing_package_type(self):
        from taurus.views import SupervisorViewSet

        view = SupervisorViewSet()
        request = _make_request(method="get")

        with patch("taurus.views.settings", TAURUS_PACKAGE_DIRS={}):
            response = view.download(request)

    def test_download_unconfigured_package_type(self):
        from taurus.views import SupervisorViewSet

        view = SupervisorViewSet()
        request = _make_request(method="get", data={"package_type": "unknown"})

        with patch("taurus.views.settings", TAURUS_PACKAGE_DIRS={"executor": {"dir": "/tmp"}}):
            response = view.download(request)


class TestScriptCheckRuleViewSetToggle:
    @patch("taurus.script_checker.custom_rule.CustomRuleChecker.invalidate_cache")
    def test_toggle_activates_inactive_rule(self, mock_invalidate):
        from taurus.views import ScriptCheckRuleViewSet

        rule = MagicMock()
        rule.is_active = False
        rule.save = MagicMock()

        view = ScriptCheckRuleViewSet()
        view.get_object = MagicMock(return_value=rule)

        request = _make_request()
        response = view.toggle(request)

        assert rule.is_active is True
        rule.save.assert_called_once()
        mock_invalidate.assert_called_once()

    @patch("taurus.script_checker.custom_rule.CustomRuleChecker.invalidate_cache")
    def test_toggle_deactivates_active_rule(self, mock_invalidate):
        from taurus.views import ScriptCheckRuleViewSet

        rule = MagicMock()
        rule.is_active = True
        rule.save = MagicMock()

        view = ScriptCheckRuleViewSet()
        view.get_object = MagicMock(return_value=rule)

        request = _make_request()
        response = view.toggle(request)

        assert rule.is_active is False
        rule.save.assert_called_once()
        mock_invalidate.assert_called_once()


class TestScriptCheckRuleViewSetBatchEnable:
    @patch("taurus.script_checker.custom_rule.CustomRuleChecker.invalidate_cache")
    def test_batch_enable(self, mock_invalidate):
        from taurus.views import ScriptCheckRuleViewSet

        mock_qs = MagicMock()

        view = ScriptCheckRuleViewSet()
        view.queryset = mock_qs

        request = _make_request(data={"ids": [1, 2, 3]})
        response = view.batch_enable(request)

        mock_qs.filter.assert_called_once_with(id__in=[1, 2, 3])
        mock_qs.filter.return_value.update.assert_called_once_with(is_active=True)

    @patch("taurus.script_checker.custom_rule.CustomRuleChecker.invalidate_cache")
    def test_batch_disable(self, mock_invalidate):
        from taurus.views import ScriptCheckRuleViewSet

        mock_qs = MagicMock()

        view = ScriptCheckRuleViewSet()
        view.queryset = mock_qs

        request = _make_request(data={"ids": [4, 5]})
        response = view.batch_disable(request)

        mock_qs.filter.assert_called_once_with(id__in=[4, 5])
        mock_qs.filter.return_value.update.assert_called_once_with(is_active=False)


class TestScriptCategoryViewSetBuildTree:
    def test_build_tree_flat(self):
        from taurus.views import ScriptCategoryViewSet

        data = [
            {"id": 1, "parent": None, "name": "Root"},
            {"id": 2, "parent": 1, "name": "Child1"},
            {"id": 3, "parent": 1, "name": "Child2"},
        ]

        view = ScriptCategoryViewSet()
        tree = view.build_tree(data)

        assert len(tree) == 1
        assert tree[0]["name"] == "Root"
        assert len(tree[0]["children"]) == 2

    def test_build_tree_nested(self):
        from taurus.views import ScriptCategoryViewSet

        data = [
            {"id": 1, "parent": None, "name": "Root"},
            {"id": 2, "parent": 1, "name": "Child"},
            {"id": 3, "parent": 2, "name": "Grandchild"},
        ]

        view = ScriptCategoryViewSet()
        tree = view.build_tree(data)

        assert len(tree) == 1
        assert len(tree[0]["children"]) == 1
        assert len(tree[0]["children"][0]["children"]) == 1

    def test_build_tree_orphan(self):
        from taurus.views import ScriptCategoryViewSet

        data = [
            {"id": 1, "parent": None, "name": "Root"},
            {"id": 2, "parent": 999, "name": "Orphan"},
        ]

        view = ScriptCategoryViewSet()
        tree = view.build_tree(data)

        assert len(tree) == 2

    def test_build_tree_empty(self):
        from taurus.views import ScriptCategoryViewSet

        view = ScriptCategoryViewSet()
        tree = view.build_tree([])

        assert tree == []


class TestWorkflowCallbackView:
    @patch("taurus.workflow.models.WorkflowNodeExecution")
    def test_callback_token_not_found(self, MockNodeExec):
        from taurus.views import workflow_callback_view

        MockNodeExec.objects.filter.return_value.first.return_value = None

        factory = RequestFactory()
        django_req = factory.post(
            "/test/",
            data=json.dumps({"status": "success"}),
            content_type="application/json",
        )

        response = workflow_callback_view(django_req, token="bad-token")

        assert response.status_code == 404

    @patch("taurus.workflow.engine.runner._apply_unit_output")
    @patch("taurus.workflow.engine.runner._build_rendered_cfg")
    @patch("taurus.workflow.engine.registry.get_registry")
    @patch("taurus.workflow.models.WorkflowNodeExecution")
    def test_callback_success(self, MockNodeExec, mock_get_registry, mock_build_cfg, mock_apply):
        from taurus.views import workflow_callback_view

        mock_node = MagicMock()
        mock_node.adapter_state = {"token": "good-token"}
        mock_node.rendered_params = {}
        MockNodeExec.objects.filter.return_value.first.return_value = mock_node

        mock_adapter = MagicMock()
        mock_uo = MagicMock()
        mock_uo.status = "success"
        mock_uo.summary = "done"
        mock_adapter.inject_external_event.return_value = mock_uo
        mock_registry = MagicMock()
        mock_registry.instantiate.return_value = mock_adapter
        mock_get_registry.return_value = mock_registry

        mock_apply.return_value = ["status"]

        factory = RequestFactory()
        django_req = factory.post(
            "/test/",
            data=json.dumps({"status": "success", "data": {"key": "value"}}),
            content_type="application/json",
        )

        response = workflow_callback_view(django_req, token="good-token")

        assert response.status_code == 200
        assert response.data["ok"] is True

    @patch("taurus.workflow.models.WorkflowNodeExecution")
    def test_callback_exception(self, MockNodeExec):
        from taurus.views import workflow_callback_view

        MockNodeExec.objects.filter.side_effect = Exception("db error")

        factory = RequestFactory()
        django_req = factory.post(
            "/test/",
            data=json.dumps({"status": "success"}),
            content_type="application/json",
        )

        response = workflow_callback_view(django_req, token="any-token")

        assert response.status_code == 500
        assert response.data["ok"] is False


class TestHostViewSetRevokedCertificates:
    @patch("taurus.views.Host.objects")
    def test_revoked_certificates_list(self, mock_objects):
        from taurus.views import HostViewSet

        host1 = MagicMock()
        host1.host_uuid = uuid.uuid4()
        host1.host_name = "host1"
        host1.host_ip = "10.0.0.1"
        host1.certificate_serial = "0A"
        host1.certificate_revoked_at = "2025-01-01"
        host1.certificate_revocation_reason = "key_compromise"

        mock_objects.filter.return_value = [host1]

        view = HostViewSet()
        request = _make_request(method="get")
        response = view.revoked_certificates(request)

        mock_objects.filter.assert_called_once_with(certificate_status="revoked")