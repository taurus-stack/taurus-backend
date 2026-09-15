"""[DEPRECATED] 历史命令：给菜单节点写入 requires_feature 字段。

.. deprecated:: 社区版/企业版合并后
    单一全功能版本不再做功能门禁：FeatureCode 全集恒真，前端不再消费
    菜单的 requires_feature 字段（DB 残留数据保留但忽略，无需清理）。
    本命令仅为兼容历史部署保留，执行时不做任何操作，后续版本将移除。
"""
from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "[DEPRECATED] Seed requires_feature on menu nodes (no longer consumed by the single full-featured edition)"

    def add_arguments(self, parser):
        # 兼容历史调用方式（init_edition_features --apply），参数不再有任何效果
        parser.add_argument(
            "--apply",
            action="store_true",
            help="(deprecated) accepted for compatibility, no longer has any effect.",
        )

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.WARNING(
                "[DEPRECATED] init_edition_features 已废弃：单一全功能版本不再使用 "
                "requires_feature 做功能门禁，前端会忽略该字段。本次不执行任何操作。"
            )
        )
