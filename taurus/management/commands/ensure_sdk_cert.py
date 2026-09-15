"""
确保后端 SDK 客户端证书存在

用法:
  python manage.py ensure_sdk_cert
"""

import os

from django.conf import settings
from django.core.management.base import BaseCommand

from taurus.ca_manager import CAManager


class Command(BaseCommand):
    help = '确保后端 SDK 客户端证书 (client.crt/client.key) 存在，不存在则自动生成'

    def handle(self, *args, **options):
        ca_dir = getattr(settings, 'CA_CERT_DIR', os.path.join(settings.BASE_DIR, 'certs'))
        manager = CAManager(ca_dir)

        if manager.ensure_sdk_client_cert():
            self.stdout.write(self.style.SUCCESS('SDK 客户端证书就绪'))
        else:
            self.stderr.write(self.style.ERROR('SDK 客户端证书生成失败'))
            raise SystemExit(1)