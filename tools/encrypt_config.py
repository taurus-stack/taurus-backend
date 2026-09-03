#!/usr/bin/env python
"""
ConfigEncryption/Decryption工具(independentScript, 不Dependency Django)

用法:
  # Generate master key
  python tools/encrypt_config.py --generate-key
  
  # Encryption单 值
  python tools/encrypt_config.py --encrypt "my-secret-password" --key <master-key>
  
  # Decryption单 值
  python tools/encrypt_config.py --decrypt "fernet:gAAAAAB..." --key <master-key>
  
  # Encrypt config file
  python tools/encrypt_config.py --encrypt-file conf/env.py --output-file conf/env.encrypted.py --key <master-key>
  
  # Decrypt config file
  python tools/encrypt_config.py --decrypt-file conf/env.encrypted.py --output-file conf/env.decrypted.py --key <master-key>
"""

import argparse
import base64
import hashlib
import os
import re
import sys
from cryptography.fernet import Fernet, InvalidToken

FERNET_PREFIX = "fernet:"


def derive_fernet_key(master_key: str) -> bytes:
    """从主Secret key派生 Fernet Secret key"""
    key_bytes = hashlib.sha256(master_key.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(key_bytes)


def get_fernet(master_key: str) -> Fernet:
    """Fetch Fernet Instance"""
    try:
        return Fernet(master_key.encode() if isinstance(master_key, str) else master_key)
    except Exception:
        return Fernet(derive_fernet_key(master_key))


def encrypt_value(plain_text: str, master_key: str) -> str:
    """EncryptionConfig value"""
    if not plain_text:
        return plain_text
    fernet = get_fernet(master_key)
    encrypted = fernet.encrypt(plain_text.encode('utf-8'))
    return f"{FERNET_PREFIX}{encrypted.decode('utf-8')}"


def decrypt_value(encrypted_text: str, master_key: str) -> str:
    """DecryptionConfig value"""
    if not encrypted_text:
        return encrypted_text
    
    if encrypted_text.startswith(FERNET_PREFIX):
        encrypted_text = encrypted_text[len(FERNET_PREFIX):]
    else:
        return encrypted_text
    
    fernet = get_fernet(master_key)
    decrypted = fernet.decrypt(encrypted_text.encode('utf-8'))
    return decrypted.decode('utf-8')


def encrypt_config_file(input_file: str, output_file: str, master_key: str):
    """Encrypt config file"""
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
        def replace_match(match, p=pattern):
            prefix = match.group(1)
            plain_value = match.group(2)
            encrypted = encrypt_value(plain_value, master_key)
            return f'{prefix}"{encrypted}"'
        
        content = re.sub(pattern, replace_match, content)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"✓ 配置文件已加密: {input_file} -> {output_file}")


def decrypt_config_file(input_file: str, output_file: str, master_key: str):
    """Decrypt config file"""
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
            print(f"✗ 解密失败: {e}")
            return match.group(0)
    
    content = re.sub(encrypted_pattern, replace_match, content)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"✓ 配置文件已解密: {input_file} -> {output_file}")


def main():
    parser = argparse.ArgumentParser(description='配置加密/解密工具')
    parser.add_argument('value', nargs='?', help='要加密/解密的值')
    parser.add_argument('--decrypt', action='store_true', help='解密模式')
    parser.add_argument('--generate-key', action='store_true', help='生成主密钥')
    parser.add_argument('--encrypt-file', help='加密配置文件')
    parser.add_argument('--decrypt-file', help='解密配置文件')
    parser.add_argument('--output-file', help='输出文件路径')
    parser.add_argument('--key', help='主密钥')
    
    args = parser.parse_args()
    
    # Generate master key
    if args.generate_key:
        key = Fernet.generate_key().decode('utf-8')
        print('=' * 60)
        print('生成的主密钥：')
        print(key)
        print('=' * 60)
        print('\n请将此密钥保存到安全的地方（如 Vault）')
        print('然后设置环境变量：')
        print(f'export CONFIG_ENCRYPTION_KEY={key}')
        return
    
    # check主Secret key
    if not args.key:
        args.key = os.environ.get('CONFIG_ENCRYPTION_KEY')
        if not args.key:
            print('✗ 错误：请提供主密钥（--key）或设置环境变量 CONFIG_ENCRYPTION_KEY')
            sys.exit(1)
    
    # Encryptionfile
    if args.encrypt_file:
        if not args.output_file:
            print('✗ 错误：使用 --encrypt-file 时必须指定 --output-file')
            sys.exit(1)
        encrypt_config_file(args.encrypt_file, args.output_file, args.key)
        return
    
    # Decryptionfile
    if args.decrypt_file:
        if not args.output_file:
            print('✗ 错误：使用 --decrypt-file 时必须指定 --output-file')
            sys.exit(1)
        decrypt_config_file(args.decrypt_file, args.output_file, args.key)
        return
    
    # Encryption/Decryption单 值
    if not args.value:
        print('✗ 错误：请提供要加密/解密的值，或使用 --generate-key 生成主密钥')
        sys.exit(1)
    
    if args.decrypt:
        try:
            decrypted = decrypt_value(args.value, args.key)
            print('解密结果：')
            print(decrypted)
        except InvalidToken:
            print('✗ 解密失败：主密钥不匹配或数据已损坏')
            sys.exit(1)
        except Exception as e:
            print(f'✗ 解密失败: {e}')
            sys.exit(1)
    else:
        encrypted = encrypt_value(args.value, args.key)
        print('加密结果：')
        print(encrypted)
        print('\n使用方式：')
        print(f'export CONFIG_ENCRYPTION_KEY=<your-master-key>')
        print(f'# 或在Configfile中use: "{encrypted}"')


if __name__ == '__main__':
    main()