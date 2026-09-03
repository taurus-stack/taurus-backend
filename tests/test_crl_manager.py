"""
taurus/crl_manager.py Unit test

test内容:
1. CRLManager initialize
2. revoke_certificate - 吊销Certificate
3. generate_crl - Generate CRL
4. check_certificate_revoked - checkCertificateYesNo被吊销
5. get_revoked_certificates - Fetch吊销list
6. get_crl_path - Fetch CRL path
7. _ensure_openssl_config - 确保 OpenSSL configfile存在
"""
import os
import pytest
from unittest.mock import patch, MagicMock

from taurus.crl_manager import CRLManager


@pytest.fixture
def manager(tmp_path):
    ca_dir = tmp_path / "ca"
    ca_dir.mkdir()
    crl_dir = tmp_path / "crl"
    crl_dir.mkdir()

    ca_cert = ca_dir / "ca.crt"
    ca_key = ca_dir / "ca.key"
    ca_cert.write_text("cert")
    ca_key.write_text("key")

    crl_path = str(crl_dir / "crl.pem")

    with patch.object(CRLManager, "__init__", lambda self: None):
        mgr = CRLManager()
        mgr.ca_cert_path = str(ca_cert)
        mgr.ca_key_path = str(ca_key)
        mgr.crl_path = crl_path
        return mgr


class TestCRLManagerInit:
    @patch("taurus.crl_manager.settings")
    def test_init_with_settings(self, mock_settings):
        mock_settings.CA_CERT_PATH = "/path/to/ca.crt"
        mock_settings.CA_KEY_PATH = "/path/to/ca.key"
        mock_settings.CRL_PATH = "/path/to/crl.pem"

        mgr = CRLManager()
        assert mgr.ca_cert_path == "/path/to/ca.crt"
        assert mgr.ca_key_path == "/path/to/ca.key"
        assert mgr.crl_path == "/path/to/crl.pem"


class TestEnsureOpensslConfig:
    def test_creates_config_file(self, manager, tmp_path):
        config_path = manager._ensure_openssl_config()
        assert os.path.exists(config_path)

        with open(config_path) as f:
            content = f.read()
        assert "[ca]" in content
        assert "[CA_default]" in content

    def test_config_file_reused(self, manager, tmp_path):
        path1 = manager._ensure_openssl_config()
        path2 = manager._ensure_openssl_config()
        assert path1 == path2


class TestRevokeCertificate:
    @patch("taurus.crl_manager.subprocess.run")
    def test_revoke_success(self, mock_run, manager):
        mock_run.side_effect = [
            MagicMock(returncode=0, stderr=""),
            MagicMock(returncode=0, stderr=""),
        ]

        result = manager.revoke_certificate("0A", reason="key_compromise")
        assert result is True

    def test_no_ca_cert_returns_false(self, manager):
        manager.ca_cert_path = "/nonexistent/ca.crt"

        result = manager.revoke_certificate("0A")
        assert result is False

    def test_no_ca_key_returns_false(self, manager):
        manager.ca_key_path = "/nonexistent/ca.key"

        result = manager.revoke_certificate("0A")
        assert result is False

    @patch("taurus.crl_manager.subprocess.run")
    def test_revoke_openssl_fails(self, mock_run, manager):
        mock_run.return_value = MagicMock(returncode=1, stderr="revoke error")

        result = manager.revoke_certificate("0A")
        assert result is False

    @patch("taurus.crl_manager.subprocess.run")
    def test_revoke_timeout(self, mock_run, manager):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="openssl", timeout=10)

        result = manager.revoke_certificate("0A")
        assert result is False

    @patch("taurus.crl_manager.subprocess.run")
    def test_revoke_exception(self, mock_run, manager):
        mock_run.side_effect = Exception("unexpected")

        result = manager.revoke_certificate("0A")
        assert result is False


class TestGenerateCRL:
    @patch("taurus.crl_manager.subprocess.run")
    def test_generate_success(self, mock_run, manager):
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        result = manager.generate_crl()
        assert result is True

    def test_no_ca_cert_returns_false(self, manager):
        manager.ca_cert_path = "/nonexistent/ca.crt"

        result = manager.generate_crl()
        assert result is False

    @patch("taurus.crl_manager.subprocess.run")
    def test_generate_fails(self, mock_run, manager):
        mock_run.return_value = MagicMock(returncode=1, stderr="crl error")

        result = manager.generate_crl()
        assert result is False

    @patch("taurus.crl_manager.subprocess.run")
    def test_generate_timeout(self, mock_run, manager):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="openssl", timeout=10)

        result = manager.generate_crl()
        assert result is False


class TestCheckCertificateRevoked:
    def test_no_crl_file_returns_false(self, manager):
        result = manager.check_certificate_revoked("0A")
        assert result is False

    @patch("taurus.crl_manager.subprocess.run")
    def test_serial_in_crl(self, mock_run, manager, tmp_path):
        crl_dir = tmp_path / "crl"
        crl_dir.mkdir(exist_ok=True)
        crl_file = crl_dir / "crl.pem"
        crl_file.write_text("CRL data")

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Serial Number: 0A\nRevocation Date: ...",
        )

        result = manager.check_certificate_revoked("0A")
        assert result is True

    @patch("taurus.crl_manager.subprocess.run")
    def test_serial_not_in_crl(self, mock_run, manager, tmp_path):
        crl_dir = tmp_path / "crl"
        crl_dir.mkdir(exist_ok=True)
        crl_file = crl_dir / "crl.pem"
        crl_file.write_text("CRL data")

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Serial Number: 0B\nRevocation Date: ...",
        )

        result = manager.check_certificate_revoked("0A")
        assert result is False

    @patch("taurus.crl_manager.subprocess.run")
    def test_crl_parse_fails(self, mock_run, manager, tmp_path):
        crl_dir = tmp_path / "crl"
        crl_dir.mkdir(exist_ok=True)
        crl_file = crl_dir / "crl.pem"
        crl_file.write_text("CRL data")

        mock_run.return_value = MagicMock(returncode=1, stderr="parse error")

        result = manager.check_certificate_revoked("0A")
        assert result is False

    @patch("taurus.crl_manager.subprocess.run")
    def test_case_insensitive_match(self, mock_run, manager, tmp_path):
        crl_dir = tmp_path / "crl"
        crl_dir.mkdir(exist_ok=True)
        crl_file = crl_dir / "crl.pem"
        crl_file.write_text("CRL data")

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Serial Number: 0A\n",
        )

        assert manager.check_certificate_revoked("0a") is True
        assert manager.check_certificate_revoked("0A") is True


class TestGetRevokedCertificates:
    def test_no_crl_file_returns_empty(self, manager):
        result = manager.get_revoked_certificates()
        assert result == []

    @patch("taurus.crl_manager.subprocess.run")
    def test_parse_revoked_list(self, mock_run, manager, tmp_path):
        crl_dir = tmp_path / "crl"
        crl_dir.mkdir(exist_ok=True)
        crl_file = crl_dir / "crl.pem"
        crl_file.write_text("CRL data")

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "Serial Number: 0A\n"
                "        Revocation Date: Jan 1 00:00:00 2025 GMT\n"
                "        CRL Reason Code: Key Compromise\n"
                "Serial Number: 0B\n"
                "        Revocation Date: Feb 1 00:00:00 2025 GMT\n"
            ),
        )

        result = manager.get_revoked_certificates()
        assert len(result) == 2
        assert result[0]["serial"] == "0A"
        assert result[1]["serial"] == "0B"

    @patch("taurus.crl_manager.subprocess.run")
    def test_parse_failure_returns_empty(self, mock_run, manager, tmp_path):
        crl_dir = tmp_path / "crl"
        crl_dir.mkdir(exist_ok=True)
        crl_file = crl_dir / "crl.pem"
        crl_file.write_text("CRL data")

        mock_run.return_value = MagicMock(returncode=1, stderr="error")

        result = manager.get_revoked_certificates()
        assert result == []


class TestGetCRLPath:
    def test_returns_path(self, manager):
        result = manager.get_crl_path()
        assert result == manager.crl_path