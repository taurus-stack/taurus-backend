"""
taurus/ca_manager.py Unit test

test内容:
1. CAManager initialize
2. ensure_ca_exists - 确保 CA Certificate存在
3. generate_ca - Generate CA Certificate
4. sign_csr - 签署Client CSR
5. sign_server_csr - 签署Serviceserver CSR
6. get_ca_cert - Fetch CA Certificate
7. get_next_serial - Fetch下一 序列号
8. _generate_openssl_cnf - Generate OpenSSL config
"""
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from taurus.ca_manager import CAManager


@pytest.fixture
def ca_dir(tmp_path):
    return tmp_path / "ca"


@pytest.fixture
def manager(ca_dir):
    return CAManager(str(ca_dir))


class TestCAManagerInit:
    def test_paths_set(self, manager, ca_dir):
        assert manager.ca_dir == ca_dir
        assert manager.ca_key == ca_dir / "ca.key"
        assert manager.ca_cert == ca_dir / "ca.crt"
        assert manager.index_file == ca_dir / "index.txt"
        assert manager.serial_file == ca_dir / "serial"
        assert manager.crlnumber_file == ca_dir / "crlnumber"
        assert manager.openssl_cnf == ca_dir / "openssl.cnf"
        assert manager.new_certs_dir == ca_dir / "newcerts"


class TestEnsureCAExists:
    @patch.object(CAManager, "generate_ca", return_value=True)
    def test_no_ca_generates(self, mock_gen, manager):
        result = manager.ensure_ca_exists()
        assert result is True
        mock_gen.assert_called_once()

    def test_existing_ca_returns_true(self, manager, ca_dir):
        ca_dir.mkdir(parents=True, exist_ok=True)
        (ca_dir / "ca.key").write_text("key")
        (ca_dir / "ca.crt").write_text("cert")

        result = manager.ensure_ca_exists()
        assert result is True
        assert manager.new_certs_dir.exists()
        assert manager.openssl_cnf.exists()

    def test_existing_ca_creates_auxiliary_files(self, manager, ca_dir):
        ca_dir.mkdir(parents=True, exist_ok=True)
        (ca_dir / "ca.key").write_text("key")
        (ca_dir / "ca.crt").write_text("cert")

        manager.ensure_ca_exists()
        assert manager.new_certs_dir.exists()
        assert manager.index_file.exists()
        assert manager.serial_file.exists()
        assert manager.crlnumber_file.exists()


class TestGenerateCA:
    @patch("taurus.ca_manager.os.chmod")
    @patch("taurus.ca_manager.subprocess.run")
    def test_generate_success(self, mock_run, mock_chmod, manager, ca_dir):
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        def _write_key_file(*args, **kwargs):
            if "genrsa" in args[0]:
                ca_dir.mkdir(parents=True, exist_ok=True)
                (ca_dir / "ca.key").write_text("mock-key")
            return MagicMock(returncode=0, stderr="")

        mock_run.side_effect = _write_key_file

        result = manager.generate_ca()
        assert result is True
        assert ca_dir.exists()
        assert manager.new_certs_dir.exists()
        assert manager.serial_file.exists()
        assert manager.crlnumber_file.exists()
        assert manager.openssl_cnf.exists()

    @patch("taurus.ca_manager.os.chmod")
    @patch("taurus.ca_manager.subprocess.run")
    def test_key_generation_fails(self, mock_run, mock_chmod, manager):
        mock_run.return_value = MagicMock(returncode=1, stderr="key error")

        result = manager.generate_ca()
        assert result is False

    @patch("taurus.ca_manager.os.chmod")
    @patch("taurus.ca_manager.subprocess.run")
    def test_cert_generation_fails(self, mock_run, mock_chmod, manager, ca_dir):
        call_count = [0]

        def _write_key_file(*args, **kwargs):
            call_count[0] += 1
            if "genrsa" in args[0]:
                ca_dir.mkdir(parents=True, exist_ok=True)
                (ca_dir / "ca.key").write_text("mock-key")
                return MagicMock(returncode=0, stderr="")
            return MagicMock(returncode=1, stderr="cert error")

        mock_run.side_effect = _write_key_file

        result = manager.generate_ca()
        assert result is False

    @patch("taurus.ca_manager.os.chmod")
    @patch("taurus.ca_manager.subprocess.run")
    def test_exception_returns_false(self, mock_run, mock_chmod, manager):
        mock_run.side_effect = Exception("unexpected")

        result = manager.generate_ca()
        assert result is False

    @patch("taurus.ca_manager.os.chmod")
    @patch("taurus.ca_manager.subprocess.run")
    def test_generates_openssl_config(self, mock_run, mock_chmod, manager, ca_dir):
        def _write_key_file(*args, **kwargs):
            if "genrsa" in args[0]:
                ca_dir.mkdir(parents=True, exist_ok=True)
                (ca_dir / "ca.key").write_text("mock-key")
            return MagicMock(returncode=0, stderr="")

        mock_run.side_effect = _write_key_file

        manager.generate_ca()
        config_content = manager.openssl_cnf.read_text()
        assert "[ca]" in config_content
        assert "[CA_default]" in config_content
        assert "[v3_client]" in config_content
        assert "[v3_server]" in config_content


class TestSignCSR:
    @patch.object(CAManager, "ensure_ca_exists", return_value=True)
    @patch("taurus.ca_manager.subprocess.run")
    def test_sign_success(self, mock_run, mock_ensure, manager, ca_dir):
        ca_dir.mkdir(parents=True, exist_ok=True)
        cert_content = "-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----"
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")

        (ca_dir / "temp.crt").write_text(cert_content)

        result = manager.sign_csr("csr-content")
        assert result == cert_content

    @patch.object(CAManager, "ensure_ca_exists", return_value=False)
    def test_no_ca_returns_none(self, mock_ensure, manager):
        result = manager.sign_csr("csr-content")
        assert result is None

    @patch.object(CAManager, "ensure_ca_exists", return_value=True)
    @patch("taurus.ca_manager.subprocess.run")
    def test_sign_failure_returns_none(self, mock_run, mock_ensure, manager, ca_dir):
        ca_dir.mkdir(parents=True, exist_ok=True)
        mock_run.return_value = MagicMock(returncode=1, stderr="sign error", stdout="")

        result = manager.sign_csr("csr-content")
        assert result is None

    @patch.object(CAManager, "ensure_ca_exists", return_value=True)
    @patch("taurus.ca_manager.subprocess.run")
    def test_sign_exception_returns_none(self, mock_run, mock_ensure, manager, ca_dir):
        ca_dir.mkdir(parents=True, exist_ok=True)
        mock_run.side_effect = Exception("sign error")

        result = manager.sign_csr("csr-content")
        assert result is None


class TestSignServerCSR:
    @patch.object(CAManager, "ensure_ca_exists", return_value=True)
    @patch("taurus.ca_manager.subprocess.run")
    def test_sign_server_success(self, mock_run, mock_ensure, manager, ca_dir):
        ca_dir.mkdir(parents=True, exist_ok=True)
        cert_content = "-----BEGIN CERTIFICATE-----\nserver\n-----END CERTIFICATE-----"
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")

        (ca_dir / "temp_server.crt").write_text(cert_content)

        result = manager.sign_server_csr(
            "csr-content",
            san_list=["DNS:localhost", "IP:127.0.0.1"],
        )
        assert result == cert_content

    @patch.object(CAManager, "ensure_ca_exists", return_value=True)
    def test_empty_san_returns_none(self, mock_ensure, manager):
        result = manager.sign_server_csr("csr-content", san_list=[])
        assert result is None

    @patch.object(CAManager, "ensure_ca_exists", return_value=False)
    def test_no_ca_returns_none(self, mock_ensure, manager):
        result = manager.sign_server_csr(
            "csr-content",
            san_list=["DNS:localhost"],
        )
        assert result is None


class TestGetCACert:
    def test_no_cert_returns_none(self, manager):
        result = manager.get_ca_cert()
        assert result is None

    def test_returns_cert_content(self, manager, ca_dir):
        ca_dir.mkdir(parents=True, exist_ok=True)
        cert_text = "-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----"
        (ca_dir / "ca.crt").write_text(cert_text)

        result = manager.get_ca_cert()
        assert result == cert_text


class TestGetNextSerial:
    def test_no_serial_file_returns_none(self, manager):
        result = manager.get_next_serial()
        assert result is None

    def test_returns_serial(self, manager, ca_dir):
        ca_dir.mkdir(parents=True, exist_ok=True)
        (ca_dir / "serial").write_text("0A")

        result = manager.get_next_serial()
        assert result == "0A"


class TestGenerateOpensslCnf:
    def test_config_contains_required_sections(self, manager, ca_dir):
        ca_dir.mkdir(parents=True, exist_ok=True)
        manager._generate_openssl_cnf()

        content = manager.openssl_cnf.read_text()
        assert "[ca]" in content
        assert "[CA_default]" in content
        assert "[policy_anything]" in content
        assert "[v3_client]" in content
        assert "[v3_server]" in content
        assert "basicConstraints = CA:FALSE" in content
        assert "clientAuth" in content
        assert "serverAuth" in content