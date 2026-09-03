"""
通用ConfigEncryption工具

use Fernet 对称Encryption算法protected敏感Config:
- 数据library密码
- Redis 密码
- JWT Secret key
- Signing key
- 其他敏感Config

support:
1. Environment variables中存储Encryption后的密文
2. start时自动Decryption
3. provideCommand行工具进行Encryption/Decryption
"""

from cryptography.fernet import Fernet, InvalidToken
import base64
import hashlib
import logging
import os

logger = logging.getLogger(__name__)

# Fernet Secret keyPrefix标识
FERNET_PREFIX = "fernet:"


def derive_fernet_key(master_key: str) -> bytes:
    """
    从主Secret key派生 Fernet Secret key
    
    Fernet require 32 字节的 URL-safe base64 encodeSecret key.
    use SHA-256 从任意长度的主Secret key派生.
    
    Args:
        master_key: 任意长度的主Secret key字符串
        
    Returns:
        Fernet Secret key(URL-safe base64 encode)
    """
    # use SHA-256 Generate 32 字节Secret key
    key_bytes = hashlib.sha256(master_key.encode('utf-8')).digest()
    # convert为 URL-safe base64 encode
    key_b64 = base64.urlsafe_b64encode(key_bytes)
    return key_b64


def get_fernet(master_key: str = None) -> Fernet:
    """
    Fetch Fernet Instance
    
    Args:
        master_key: 主Secret key, if为 None 则从Environment variablesread
        
    Returns:
        Fernet Instance
    """
    if master_key is None:
        master_key = os.environ.get('CONFIG_ENCRYPTION_KEY')
        if not master_key:
            raise ValueError(
                "CONFIG_ENCRYPTION_KEY not set, please set MASTER_KEY env var\n"
                "Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
    
    # if已经YesValid的 Fernet Secret key, 直接use
    try:
        return Fernet(master_key.encode() if isinstance(master_key, str) else master_key)
    except Exception:
        # No则派生Secret key
        return Fernet(derive_fernet_key(master_key))


def encrypt_value(plain_text: str, master_key: str = None) -> str:
    """
    EncryptionConfig value
    
    Args:
        plain_text: 明文Config value
        master_key: 主Secret key(Optional)
        
    Returns:
        Encryption后的字符串(带 fernet: Prefix)
    """
    if not plain_text:
        return plain_text
    
    fernet = get_fernet(master_key)
    encrypted = fernet.encrypt(plain_text.encode('utf-8'))
    return f"{FERNET_PREFIX}{encrypted.decode('utf-8')}"


def decrypt_value(encrypted_text: str, master_key: str = None) -> str:
    """
    DecryptionConfig value
    
    Args:
        encrypted_text: Encryption后的字符串(带或不带 fernet: Prefix)
        master_key: 主Secret key(Optional)
        
    Returns:
        明文Config value
        
    Raises:
        ValueError: DecryptionFailed
    """
    if not encrypted_text:
        return encrypted_text
    
    # removePrefix(if存在)
    if encrypted_text.startswith(FERNET_PREFIX):
        encrypted_text = encrypted_text[len(FERNET_PREFIX):]
    else:
        # if不YesEncryptionFormat, 直接return
        return encrypted_text
    
    try:
        fernet = get_fernet(master_key)
        decrypted = fernet.decrypt(encrypted_text.encode('utf-8'))
        return decrypted.decode('utf-8')
    except InvalidToken:
        logger.error("Decryption failed: key mismatch or corrupted data")
        raise ValueError("Decryption failed: key mismatch or corrupted data")
    except Exception as e:
        logger.error(f"Decryption error: {e}")
        raise


def is_encrypted(value: str) -> bool:
    """
    check值YesNo已Encryption
    
    Args:
        value: Config value
        
    Returns:
        True if已Encryption, False No则
    """
    if not value:
        return False
    return value.startswith(FERNET_PREFIX)


def auto_decrypt(value: str, master_key: str = None) -> str:
    """
    自动DecryptionConfig value(if已Encryption)
    
    这Yes一 安全的package装server, if值未Encryption或DecryptionFailed, return原值.
    
    if CONFIG_ENCRYPTION_ENABLED=False, 直接return原值(SkipDecryption).
    
    Args:
        value: Config value
        master_key: 主Secret key(Optional)
        
    Returns:
        Decryption后的值或原值
    """
    if not value:
        return value
    
    # checkEncryptionYesNoEnable(Development environment可设为 False)
    encryption_enabled = os.environ.get('CONFIG_ENCRYPTION_ENABLED', 'true').lower() == 'true'
    if not encryption_enabled:
        # Development environment:SkipEncryption, 直接return原值
        if value.startswith(FERNET_PREFIX):
            logger.debug("Encryption disabled, skipping decryption")
        return value
    
    if not is_encrypted(value):
        return value
    
    try:
        return decrypt_value(value, master_key)
    except Exception as e:
        logger.warning(f"Auto-decryption failed, returning original value: {e}")
        return value


def generate_master_key() -> str:
    """
    Generatenew主Secret key
    
    Returns:
        Fernet Format的Secret key字符串
    """
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode('utf-8')


def encrypt_config_file(input_file: str, output_file: str, master_key: str = None):
    """
    Encryption整 ConfigFile
    
    read明文ConfigFile, Encryption敏感Field, OutputEncryption后的ConfigFile.
    
    Args:
        input_file: 明文ConfigFilepath
        output_file: Encryption后ConfigFilepath
        master_key: 主Secret key(Optional)
    """
    import re
    
    # 敏感ConfigField模式
    sensitive_patterns = [
        r'(DATABASE_PASSWORD\s*=\s*)[\'"]([^\'"]+)[\'"]',
        r'(REDIS_PASSWORD\s*=\s*)[\'"]([^\'"]+)[\'"]',
        r'(JWT_SECRET_KEY\s*=\s*)[\'"]([^\'"]+)[\'"]',
        r'(SIGNING_MASTER_KEY\s*=\s*)[\'"]([^\'"]+)[\'"]',
        r'(SECRET_KEY\s*=\s*)[\'"]([^\'"]+)[\'"]',
    ]
    
    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    for pattern in sensitive_patterns:
        def replace_match(match):
            prefix = match.group(1)
            plain_value = match.group(2)
            encrypted = encrypt_value(plain_value, master_key)
            return f'{prefix}"{encrypted}"'
        
        content = re.sub(pattern, replace_match, content)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(content)
    
    logger.info(f"Config file encrypted: {input_file} -> {output_file}")


def decrypt_config_file(input_file: str, output_file: str, master_key: str = None):
    """
    Decrypt config file
    
    Args:
        input_file: Encrypt config filepath
        output_file: Decryption后ConfigFilepath
        master_key: 主Secret key(Optional)
    """
    import re
    
    # matchEncryption的值
    encrypted_pattern = r'([\'"])fernet:([A-Za-z0-9_\-]+=*)[\'"]'
    
    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    def replace_match(match):
        quote = match.group(1)
        encrypted_value = f"fernet:{match.group(2)}"
        try:
            decrypted = decrypt_value(encrypted_value, master_key)
            return f'{quote}{decrypted}{quote}'
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            return match.group(0)  # 保持原值
    
    content = re.sub(encrypted_pattern, replace_match, content)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(content)
    
    logger.info(f"Config file decrypted: {input_file} -> {output_file}")