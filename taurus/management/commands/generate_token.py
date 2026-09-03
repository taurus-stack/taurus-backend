"""
Generate Supervisor RegistryToken

用法:
  # Generate默认Token(1小时后过期, 最多use1次, 不自动Approval)
  python manage.py generate_token

  # GeneratetestToken(24小时后过期, 最多use100次, 自动Approval)
  python manage.py generate_token --name "testToken" --hours 24 --max-uses 100 --auto-approve

  # Generate不限制IP的长期Token
  python manage.py generate_token --name "长期Token" --days 365 --max-uses 9999 --auto-approve

  # 限制IPWhitelist
  python manage.py generate_token --allowed-ips 192.168.1.100,192.168.1.101
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from taurus.models import RegistrationToken


class Command(BaseCommand):
    help = '生成 Supervisor 注册令牌'

    def add_arguments(self, parser):
        parser.add_argument('--name', type=str, default='测试令牌', help='令牌名称 (默认: 测试令牌)')
        parser.add_argument('--hours', type=int, default=0, help='有效时长（小时）')
        parser.add_argument('--days', type=int, default=0, help='有效时长（天）')
        parser.add_argument('--max-uses', type=int, default=1, help='最大使用次数 (默认: 1)')
        parser.add_argument('--auto-approve', action='store_true', default=False, help='注册后自动审批')
        parser.add_argument('--allowed-ips', type=str, default='', help='IP白名单，逗号分隔 (默认: 不限制)')

    def handle(self, *args, **options):
        name = options['name']
        hours = options['hours']
        days = options['days']
        max_uses = options['max_uses']
        auto_approve = options['auto_approve']
        allowed_ips_str = options['allowed_ips']

        if not hours and not days:
            hours = 1

        expires_at = timezone.now() + timedelta(hours=hours, days=days)

        allowed_ips = []
        if allowed_ips_str:
            allowed_ips = [ip.strip() for ip in allowed_ips_str.split(',') if ip.strip()]

        plain_token = RegistrationToken.generate_token()
        token_hash = RegistrationToken.hash_token(plain_token)
        token_prefix = plain_token[:12]

        token = RegistrationToken.objects.create(
            token=token_hash,
            token_prefix=token_prefix,
            name=name,
            expires_at=expires_at,
            max_uses=max_uses,
            auto_approve=auto_approve,
            allowed_ips=allowed_ips,
        )

        self.stdout.write(self.style.SUCCESS('令牌创建成功!'))
        self.stdout.write('')
        self.stdout.write(f'  名称:       {token.name}')
        self.stdout.write(f'  前缀:       {token.token_prefix}...')
        self.stdout.write(f'  过期时间:   {token.expires_at.strftime("%Y-%m-%d %H:%M:%S")}')
        self.stdout.write(f'  最大使用:   {token.max_uses} 次')
        self.stdout.write(f'  自动审批:   {"是" if token.auto_approve else "否"}')
        self.stdout.write(f'  IP白名单:   {", ".join(token.allowed_ips) if token.allowed_ips else "不限制"}')
        self.stdout.write('')
        self.stdout.write(self.style.WARNING(f'  明文令牌:   {plain_token}'))
        self.stdout.write('')
        self.stdout.write(self.style.NOTICE('  ⚠️ 明文令牌仅显示一次，请妥善保存!'))
        self.stdout.write('')
        self.stdout.write('  安装命令:')
        self.stdout.write(f'  curl -fsSL <SERVER>/api/taurus/supervisor/install_script/ | bash -s -- --token {plain_token} --auto-install')