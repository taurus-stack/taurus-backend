"""
Certificate吊销list(CRL)管理Modules
用于管理ClientCertificate的吊销Status
"""
import logging
import os
import re
import subprocess
from django.conf import settings

logger = logging.getLogger(__name__)


class CRLManager:
    """Certificate吊销list管理server"""

    def __init__(self):
        self.ca_cert_path = getattr(settings, 'CA_CERT_PATH', None)
        self.ca_key_path = getattr(settings, 'CA_KEY_PATH', None)
        self.crl_path = getattr(settings, 'CRL_PATH', None)
        
        if not self.crl_path:
            base_dir = getattr(settings, 'BASE_DIR', '')
            self.crl_path = os.path.join(base_dir, 'certs', 'crl.pem')

    def _ensure_openssl_config(self):
        """
        确保 OpenSSL ConfigFile存在
        returnConfigFilepath
        """
        config_path = os.path.join(os.path.dirname(self.crl_path), 'openssl_crl.cnf')
        
        if not os.path.exists(config_path):
            ca_dir = os.path.dirname(self.ca_cert_path) if self.ca_cert_path else ''
            config_content = f"""[ca]
default_ca = CA_default

[CA_default]
dir = {ca_dir}
database = {os.path.join(ca_dir, 'index.txt')}
serial = {os.path.join(ca_dir, 'crlnumber')}
default_md = sha256
default_crl_days = 365
crl = {self.crl_path}

[crl_ext]
authorityKeyIdentifier=keyid:always
"""
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, 'w') as f:
                f.write(config_content)
            
            # create必要的File
            index_file = os.path.join(ca_dir, 'index.txt')
            if not os.path.exists(index_file):
                open(index_file, 'a').close()
            
            crlnumber_file = os.path.join(ca_dir, 'crlnumber')
            if not os.path.exists(crlnumber_file):
                with open(crlnumber_file, 'w') as f:
                    f.write('01')
        
        return config_path

    def _find_cert_file_by_serial(self, certificate_serial: str) -> str | None:
        """
        根据证书序列号在 CA 的 newcerts 目录中查找证书 PEM 文件

        Args:
            certificate_serial: 证书序列号（十六进制字符串）

        Returns:
            证书文件路径，未找到返回 None
        """
        ca_dir = os.path.dirname(self.ca_cert_path) if self.ca_cert_path else ''
        if not ca_dir or not certificate_serial:
            return None

        new_certs_dir = os.path.join(ca_dir, 'newcerts')
        if not os.path.isdir(new_certs_dir):
            return None

        try:
            target = int(certificate_serial.strip(), 16)
        except ValueError:
            logger.error("[CRL] Invalid certificate serial number: %s", certificate_serial)
            return None

        for name in os.listdir(new_certs_dir):
            if not name.endswith('.pem'):
                continue
            stem = name[:-4]
            try:
                if int(stem, 16) == target:
                    return os.path.join(new_certs_dir, name)
            except ValueError:
                continue
        return None

    def revoke_certificate(self, certificate_serial: str, reason: str = 'unspecified') -> bool:
        """
        吊销指定证书
        
        Args:
            certificate_serial: 证书序列号（十六进制字符串）
            reason: 吊销原因
            
        Returns:
            是否吊销成功
        """
        if not os.path.exists(self.ca_cert_path) or not os.path.exists(self.ca_key_path):
            logger.error("[CRL] CA CertificateFile does not exist")
            return False

        try:
            config_path = self._ensure_openssl_config()

            # according to序列号在 newcerts directory中find已签发Certificate的 PEM File
            # (openssl ca 签发时会把Certificate副本Save为 newcerts/<serial>.pem)
            cert_pem_path = self._find_cert_file_by_serial(certificate_serial)
            if not cert_pem_path:
                logger.error("[CRL] No certificate file found for serial: serial=%s", certificate_serial)
                return False

            # use openssl ca -revoke 吊销Certificate
            result = subprocess.run(
                [
                    'openssl', 'ca',
                    '-config', config_path,
                    '-cert', self.ca_cert_path,
                    '-keyfile', self.ca_key_path,
                    '-revoke', cert_pem_path,
                    '-crl_reason', reason,
                ],
                capture_output=True,
                text=True,
                timeout=10,
                env={**os.environ, 'OPENSSL_CONF': config_path}
            )
            
            if result.returncode != 0:
                logger.error("[CRL] Certificate revocation failed: %s", result.stderr)
                return False
            
            logger.info("[CRL] Certificate revoked: serial=%s, reason=%s", certificate_serial, reason)
            
            # re-Generate CRL File
            return self.generate_crl()
            
        except subprocess.TimeoutExpired:
            logger.error("[CRL] Certificate revocation timeout")
            return False
        except Exception as e:
            logger.error("[CRL] Exception revoking certificate: %s", str(e))
            return False

    def generate_crl(self) -> bool:
        """
        生成证书吊销列表（CRL）文件
        
        Returns:
            是否生成成功
        """
        if not os.path.exists(self.ca_cert_path) or not os.path.exists(self.ca_key_path):
            logger.error("[CRL] CA CertificateFile does not exist")
            return False

        try:
            config_path = self._ensure_openssl_config()
            
            result = subprocess.run(
                [
                    'openssl', 'ca',
                    '-config', config_path,
                    '-cert', self.ca_cert_path,
                    '-keyfile', self.ca_key_path,
                    '-gencrl',
                    '-out', self.crl_path,
                ],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                logger.error("[CRL] CRL GenerateFailed: %s", result.stderr)
                return False
            
            logger.info("[CRL] CRL file generated: %s", self.crl_path)
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("[CRL] CRL Generatetimeout")
            return False
        except Exception as e:
            logger.error("[CRL] CRL GenerateException: %s", str(e))
            return False

    def get_crl_path(self) -> str:
        """
        获取 CRL 文件路径
        
        Returns:
            CRL 文件路径
        """
        return self.crl_path

    def check_certificate_revoked(self, certificate_serial: str) -> bool:
        """
        检查证书是否已被吊销
        
        Args:
            certificate_serial: 证书序列号
            
        Returns:
            是否已吊销
        """
        if not os.path.exists(self.crl_path):
            return False

        try:
            # Parse CRL File, check序列号YesNo在吊销list中
            result = subprocess.run(
                [
                    'openssl', 'crl',
                    '-in', self.crl_path,
                    '-text',
                    '-noout',
                ],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                logger.error("[CRL] CRL ParseFailed: %s", result.stderr)
                return False
            
            # Parse Revoked Certificates 段中的序列号list, 做精确比较
            # (Avoidchild串match导致的误报, 如 "ABC" 会match "01ABCDEF")
            serial = certificate_serial.strip()
            try:
                target = int(serial, 16)
            except ValueError:
                logger.error("[CRL] Invalid certificate serial number: %s", serial)
                return False

            # match形如 "Serial Number: 1234ABCD" 的行
            for match in re.finditer(r'Serial Number:\s*([0-9A-Fa-f]+)', result.stdout):
                try:
                    if int(match.group(1), 16) == target:
                        return True
                except ValueError:
                    continue

            return False
            
        except subprocess.TimeoutExpired:
            logger.error("[CRL] CRL checktimeout")
            return False
        except Exception as e:
            logger.error("[CRL] CRL checkException: %s", str(e))
            return False

    def get_revoked_certificates(self) -> list:
        """
        获取所有已吊销的证书列表
        
        Returns:
            吊销证书信息列表
        """
        if not os.path.exists(self.crl_path):
            return []

        try:
            result = subprocess.run(
                [
                    'openssl', 'crl',
                    '-in', self.crl_path,
                    '-text',
                    '-noout',
                ],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                logger.error("[CRL] CRL ParseFailed: %s", result.stderr)
                return []
            
            revoked_list = []
            lines = result.stdout.split('\n')
            
            for i, line in enumerate(lines):
                if 'Serial Number:' in line:
                    serial = line.split(':')[1].strip()
                    revocation_date = ''
                    reason = ''
                    
                    # find吊销Date和原因
                    for j in range(i+1, min(i+5, len(lines))):
                        if 'Revocation Date:' in lines[j]:
                            revocation_date = lines[j].split(':', 1)[1].strip()
                        if 'CRL Reason Code:' in lines[j]:
                            reason = lines[j].split(':', 1)[1].strip()
                    
                    revoked_list.append({
                        'serial': serial,
                        'revocation_date': revocation_date,
                        'reason': reason,
                    })
            
            return revoked_list
            
        except Exception as e:
            logger.error("[CRL] Exception fetching revocation list: %s", str(e))
            return []


# global CRL 管理serverInstance
crl_manager = CRLManager()