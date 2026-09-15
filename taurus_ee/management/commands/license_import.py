"""
python manage.py license_import <file> — 导入 License 文件到默认路径.

用法：
    python manage.py license_import ./license.lic
    python manage.py license_import ./license.lic --force
    python manage.py license_import ./license.lic --target /etc/taurus/license.lic
"""

import shutil
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


DEFAULT_TARGET = Path("/etc/taurus/license.lic")


class Command(BaseCommand):
    help = "导入 Taurus EE License 文件"

    def add_arguments(self, parser):
        parser.add_argument("file", help="源 .lic 文件路径")
        parser.add_argument(
            "--target", default=str(DEFAULT_TARGET),
            help=f"目标路径（默认 {DEFAULT_TARGET}）",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="覆盖已存在的目标文件",
        )

    def handle(self, *args, **options):
        src = Path(options["file"])
        dst = Path(options["target"])

        if not src.is_file():
            raise CommandError(f"源文件不存在: {src}")

        if dst.is_file() and not options["force"]:
            raise CommandError(
                f"目标文件已存在: {dst}\n"
                f"使用 --force 覆盖，或指定 --target 另存."
            )

        # 确保目标目录存在
        dst.parent.mkdir(parents=True, exist_ok=True)

        # 先验签再拷贝（避免拷贝无效文件）
        try:
            from taurus_ee.license import verify_license_file, invalidate_cache
            status = verify_license_file(src)
            self.stdout.write(f"验签通过: valid={status.valid}, tier={status.tier}")
        except ImportError:
            self.stdout.write(self.style.WARNING(
                "⚠️  taurus_ee/license 模块不可用，跳过验签"
            ))

        # 拷贝 + 权限保护
        shutil.copy2(src, dst)
        dst.chmod(0o644)
        self.stdout.write(self.style.SUCCESS(f"✅ License 已导入 → {dst}"))

        # 清缓存（如果模块可用）
        try:
            from taurus_ee.license import invalidate_cache
            invalidate_cache()
            self.stdout.write(self.style.SUCCESS("✅ License 缓存已失效，下次加载重新验签"))
        except ImportError:
            pass
