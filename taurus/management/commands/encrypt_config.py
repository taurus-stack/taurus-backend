"""
ConfigEncryption/Decryption管理Command

用法:
  # Encryption单 值
  python manage.py encrypt_config "my-secret-password"
  
  # Decryption单 值
  python manage.py encrypt_config --decrypt "fernet:gAAAAAB..."
  
  # Encrypt config file
  python manage.py encrypt_config --encrypt-file conf/env.py --output-file conf/env.encrypted.py
  
  # Decrypt config file
  python manage.py encrypt_config --decrypt-file conf/env.encrypted.py --output-file conf/env.decrypted.py
  
  # Generate master key
  python manage.py encrypt_config --generate-key
"""

from django.core.management.base import BaseCommand, CommandError
from taurus.config_crypto import (
    encrypt_value,
    decrypt_value,
    generate_master_key,
    encrypt_config_file,
    decrypt_config_file,
)


class Command(BaseCommand):
    help = '加密/解密配置值（使用 Fernet 对称加密）'

    def add_arguments(self, parser):
        parser.add_argument(
            'value',
            nargs='?',
            help='要加密/解密的值',
        )
        parser.add_argument(
            '--decrypt',
            action='store_true',
            help='解密模式（默认加密）',
        )
        parser.add_argument(
            '--generate-key',
            action='store_true',
            help='生成新的主密钥',
        )
        parser.add_argument(
            '--encrypt-file',
            help='加密配置文件',
        )
        parser.add_argument(
            '--decrypt-file',
            help='解密配置文件',
        )
        parser.add_argument(
            '--output-file',
            help='输出文件路径',
        )
        parser.add_argument(
            '--master-key',
            help='主密钥（默认从环境变量 CONFIG_ENCRYPTION_KEY 读取）',
        )

    def handle(self, *args, **options):
        master_key = options.get('master_key')

        # Generate master key
        if options['generate_key']:
            key = generate_master_key()
            self.stdout.write(self.style.SUCCESS('=' * 60))
            self.stdout.write(self.style.SUCCESS('生成的主密钥：'))
            self.stdout.write(self.style.WARNING(key))
            self.stdout.write(self.style.SUCCESS('=' * 60))
            self.stdout.write(self.style.SUCCESS('\n请将此密钥保存到安全的地方（如 Vault）'))
            self.stdout.write(self.style.SUCCESS('然后设置环境变量：'))
            self.stdout.write(self.style.WARNING(f'export CONFIG_ENCRYPTION_KEY={key}'))
            return

        # EncryptionFile
        if options['encrypt_file']:
            if not options['output_file']:
                raise CommandError('使用 --encrypt-file 时必须指定 --output-file')
            
            encrypt_config_file(
                options['encrypt_file'],
                options['output_file'],
                master_key
            )
            self.stdout.write(self.style.SUCCESS(f'配置文件已加密: {options["output_file"]}'))
            return

        # DecryptionFile
        if options['decrypt_file']:
            if not options['output_file']:
                raise CommandError('使用 --decrypt-file 时必须指定 --output-file')
            
            decrypt_config_file(
                options['decrypt_file'],
                options['output_file'],
                master_key
            )
            self.stdout.write(self.style.SUCCESS(f'配置文件已解密: {options["output_file"]}'))
            return

        # Encryption/Decryption单 值
        if not options['value']:
            raise CommandError('请提供要加密/解密的值，或使用 --generate-key 生成主密钥')

        value = options['value']

        if options['decrypt']:
            try:
                decrypted = decrypt_value(value, master_key)
                self.stdout.write(self.style.SUCCESS('解密结果：'))
                self.stdout.write(self.style.WARNING(decrypted))
            except Exception as e:
                raise CommandError(f'解密失败: {e}')
        else:
            encrypted = encrypt_value(value, master_key)
            self.stdout.write(self.style.SUCCESS('加密结果：'))
            self.stdout.write(self.style.WARNING(encrypted))
            self.stdout.write(self.style.SUCCESS('\n使用方式：'))
            self.stdout.write(f'export CONFIG_ENCRYPTION_KEY=<your-master-key>')
            self.stdout.write(f'# 或在ConfigFile中use: "{encrypted}"')