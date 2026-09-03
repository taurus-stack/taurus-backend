"""
CA Certificate管理server
负责 CA Certificate的Generate, 管理和ClientCertificate签发
"""
import os
import subprocess
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class CAManager:
    """CA Certificate管理server"""

    def __init__(self, ca_dir: str):
        self.ca_dir = Path(ca_dir)
        self.new_certs_dir = self.ca_dir / "newcerts"
        self.ca_key = self.ca_dir / "ca.key"
        self.ca_cert = self.ca_dir / "ca.crt"
        self.index_file = self.ca_dir / "index.txt"
        self.serial_file = self.ca_dir / "serial"
        self.crlnumber_file = self.ca_dir / "crlnumber"
        self.openssl_cnf = self.ca_dir / "openssl.cnf"

    def ensure_ca_exists(self) -> bool:
        """确保 CA Certificate存在, if不存在则自动Generate"""
        if self.ca_cert.exists() and self.ca_key.exists():
            logger.info("CA certificate already exists: %s", self.ca_dir)
            # 确保辅助File和directory存在(CA Certificatemayalready exists但辅助Filemissing)
            self.new_certs_dir.mkdir(parents=True, exist_ok=True)
            if not self.index_file.exists() or self.index_file.stat().st_size == 0:
                self.index_file.write_text("")
            if not self.serial_file.exists():
                self.serial_file.write_text("01")
            if not self.crlnumber_file.exists():
                self.crlnumber_file.write_text("01")
            # 始终re-GenerateConfigFile, 确保Contains最新Config(如 new_certs_dir)
            self._generate_openssl_cnf()
            return True
        
        logger.info("CA certificate not found, generating...")
        return self.generate_ca()

    def generate_ca(self, days: int = 3650) -> bool:
        """
        Generate CA Certificate
        
        Args:
            days: CA Certificate有效期(天)
            
        Returns:
            YesNoGenerateSuccess
        """
        try:
            self.ca_dir.mkdir(parents=True, exist_ok=True)
            self.new_certs_dir.mkdir(parents=True, exist_ok=True)
            
            # Generate CA Private key
            logger.info("Generating CA private key...")
            result = subprocess.run(
                ["openssl", "genrsa", "-out", str(self.ca_key), "4096"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                logger.error("Failed to generate CA private key: %s", result.stderr)
                return False
            
            os.chmod(self.ca_key, 0o600)
            
            # Generate CA Certificate
            logger.info("Generating CA certificate...")
            result = subprocess.run(
                [
                    "openssl", "req", "-new", "-x509",
                    "-key", str(self.ca_key),
                    "-out", str(self.ca_cert),
                    "-days", str(days),
                    "-subj", "/C=CN/ST=Beijing/L=Beijing/O=TaurusOps/CN=TaurusOps-CA",
                    "-addext", "basicConstraints=critical,CA:TRUE",
                    "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                    "-addext", "subjectKeyIdentifier=hash"
                ],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                logger.error("Failed to generate CA certificate: %s", result.stderr)
                return False
            
            # initialize CA 数据libraryFile
            if not self.index_file.exists() or self.index_file.stat().st_size == 0:
                self.index_file.write_text("")
            
            if not self.serial_file.exists():
                self.serial_file.write_text("01")
            
            if not self.crlnumber_file.exists():
                self.crlnumber_file.write_text("01")
            
            # Generate OpenSSL ConfigFile
            self._generate_openssl_cnf()
            
            logger.info("CA certificate generated successfully: %s", self.ca_dir)
            return True
            
        except Exception as e:
            logger.error("Exception generating CA certificate: %s", str(e))
            return False

    def _generate_openssl_cnf(self):
        """Generate OpenSSL ConfigFile"""
        config_content = f"""[ca]
default_ca = CA_default

[CA_default]
dir = {self.ca_dir}
database = {self.index_file}
serial = {self.serial_file}
crlnumber = {self.crlnumber_file}
new_certs_dir = {self.new_certs_dir}
default_md = sha256
default_days = 365
default_crl_days = 365
policy = policy_anything
unique_subject = no

[policy_anything]
countryName = optional
stateOrProvinceName = optional
localityName = optional
organizationName = optional
organizationalUnitName = optional
commonName = supplied
emailAddress = optional

[v3_client]
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer

[v3_server]
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
"""
        self.openssl_cnf.write_text(config_content)

    def sign_csr(self, csr_content: str, days: int = 365) -> Optional[str]:
        """
        签署 CSR 请求，生成客户端证书
        
        Args:
            csr_content: CSR 内容（PEM 格式）
            days: 证书有效期（天）
            
        Returns:
            客户端证书内容（PEM 格式），失败返回 None
        """
        if not self.ensure_ca_exists():
            logger.error("CA certificate not found, cannot sign certificate")
            return None
        
        try:
            # 将 CSR writetemporaryFile
            csr_file = self.ca_dir / "temp.csr"
            csr_file.write_text(csr_content)
            
            # 签署Certificate
            cert_file = self.ca_dir / "temp.crt"
            result = subprocess.run(
                [
                    "openssl", "ca",
                    "-config", str(self.openssl_cnf),
                    "-cert", str(self.ca_cert),
                    "-keyfile", str(self.ca_key),
                    "-in", str(csr_file),
                    "-out", str(cert_file),
                    "-days", str(days),
                    "-batch",
                    "-extensions", "v3_client"
                ],
                capture_output=True, text=True, timeout=30
            )
            
            # cleanuptemporary CSR
            csr_file.unlink(missing_ok=True)
            
            if result.returncode != 0:
                logger.error("Certificate signing failed: stdout=%s, stderr=%s", result.stdout, result.stderr)
                return None
            
            # readGenerate的Certificate
            client_cert = cert_file.read_text()
            cert_file.unlink(missing_ok=True)
            
            logger.info("Certificate signed successfully")
            return client_cert
            
        except Exception as e:
            logger.error("Exception signing certificate: %s", str(e))
            return None

    def sign_server_csr(self, csr_content: str, san_list: list[str], days: int = 365) -> Optional[str]:
        """
        签署 CSR 请求，生成服务器证书（包含 SAN）
        
        Args:
            csr_content: CSR 内容（PEM 格式）
            san_list: Subject Alternative Name 列表，如 ['DNS:localhost', 'IP:127.0.0.1']
            days: 证书有效期（天）
            
        Returns:
            服务器证书内容（PEM 格式），失败返回 None
        """
        if not self.ensure_ca_exists():
            logger.error("CA certificate not found, cannot sign certificate")
            return None
        
        if not san_list:
            logger.error("ServiceserverCertificatemustContains SAN list")
            return None
        
        try:
            # 将 CSR writetemporaryFile
            csr_file = self.ca_dir / "temp_server.csr"
            csr_file.write_text(csr_content)
            
            # createtemporaryConfigFile, Contains SAN scale out
            temp_cnf = self.ca_dir / "temp_server.cnf"
            san_string = ", ".join(san_list)
            temp_cnf_content = f"""[v3_server_with_san]
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = {san_string}
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
"""
            temp_cnf.write_text(temp_cnf_content)
            
            # 签署Certificate
            cert_file = self.ca_dir / "temp_server.crt"
            result = subprocess.run(
                [
                    "openssl", "ca",
                    "-config", str(self.openssl_cnf),
                    "-cert", str(self.ca_cert),
                    "-keyfile", str(self.ca_key),
                    "-in", str(csr_file),
                    "-out", str(cert_file),
                    "-days", str(days),
                    "-batch",
                    "-extfile", str(temp_cnf),
                    "-extensions", "v3_server_with_san"
                ],
                capture_output=True, text=True, timeout=30
            )
            
            # cleanuptemporaryFile
            csr_file.unlink(missing_ok=True)
            temp_cnf.unlink(missing_ok=True)
            
            if result.returncode != 0:
                logger.error("Server certificate signing failed: stdout=%s, stderr=%s", result.stdout, result.stderr)
                return None
            
            # readGenerate的Certificate
            server_cert = cert_file.read_text()
            cert_file.unlink(missing_ok=True)
            
            logger.info("Server certificate signed successfully, SAN: %s", san_string)
            return server_cert
            
        except Exception as e:
            logger.error("Exception signing server certificate: %s", str(e))
            return None

    def get_ca_cert(self) -> Optional[str]:
        """Fetch CA Certificate内容"""
        if not self.ca_cert.exists():
            return None
        return self.ca_cert.read_text()

    def get_next_serial(self) -> Optional[str]:
        """Fetch下一 Certificate序列号"""
        if not self.serial_file.exists():
            return None
        return self.serial_file.read_text().strip()