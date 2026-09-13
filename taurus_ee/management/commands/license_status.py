"""
python manage.py license_status — 查看当前 License 状态（四态）.

状态机：
    free      无 License               全功能 + max_hosts=50 + 社区服务等级
    licensed  验签有效且未过期          按 License tier 配额与权益
    grace     过期 ≤ 30 天宽限期        保留原 tier 权益，仅告警
    blocked   超宽限/指纹不符/坏签      回退 50 台主机配额

用法：
    python manage.py license_status
    python manage.py license_status --json
"""

import json
import os

from django.core.management.base import BaseCommand, CommandError


_STATE_STYLE = {
    "free": "SUCCESS",
    "licensed": "SUCCESS",
    "grace": "WARNING",
    "blocked": "ERROR",
}


class Command(BaseCommand):
    help = "查看 Taurus License 状态（free/licensed/grace/blocked）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--json", action="store_true",
            help="以 JSON 格式输出（方便脚本解析）",
        )

    def handle(self, *args, **options):
        try:
            from taurus.editions.loader import get_edition, reset_for_testing
            from taurus.editions.features import ALL_FEATURE_CODES
        except ImportError as e:
            raise CommandError(f"无法导入 Edition 模块: {e}")

        # CLI 每次调用都丢弃缓存，确保展示磁盘上 License 文件的最新状态
        reset_for_testing()
        g = get_edition()
        lic = g.license_status
        state = lic.get("state", "free")
        total = len(ALL_FEATURE_CODES)
        avail = sum(1 for c in ALL_FEATURE_CODES if g.has_feature(c))

        if options["json"]:
            print(json.dumps({
                "edition": g.name,
                "state": state,
                "tier": lic.get("tier"),
                "valid": lic.get("valid"),
                "license": lic,
                "features_total": total,
                "features_available": avail,
            }, indent=2, ensure_ascii=False))
            return

        # ------------------------------------------------------------ 人类可读格式
        style_name = _STATE_STYLE.get(state, "WARNING")
        state_style = getattr(self.style, style_name)

        self.stdout.write(f"Edition            : {g.name}（全功能版本，edition 字段仅兼容保留）")
        self.stdout.write(state_style(f"License state      : {state}"))
        self.stdout.write(f"Valid              : {lic.get('valid')}")
        self.stdout.write(f"Tier               : {lic.get('tier', '?')}")
        self.stdout.write(f"Customer ID        : {lic.get('customer_id') or '-'}")
        self.stdout.write(f"Customer Name      : {lic.get('customer_name') or '-'}")
        self.stdout.write(f"Expires at         : {lic.get('expires_at') or '-'}")

        quota = lic.get("quota", {})
        max_hosts = quota.get("max_hosts")
        hosts_used = lic.get("hosts_used")
        hosts_used_text = "未知" if hosts_used is None else hosts_used
        max_hosts_text = "不限" if max_hosts is None else max_hosts
        self.stdout.write(f"Hosts used / quota : {hosts_used_text} / {max_hosts_text}")
        self.stdout.write(f"Quota              : {json.dumps(quota, ensure_ascii=False)}")

        self.stdout.write(f"Branding allowed   : {lic.get('branding_allowed', False)}")
        self.stdout.write(f"Update channels    : {', '.join(lic.get('update_channels') or [])}")
        svc = lic.get("service_level") or {}
        self.stdout.write(
            f"Service level      : {svc.get('level', '-')} — {svc.get('sla', '-')}"
        )
        channels = svc.get("channels") or []
        if channels:
            self.stdout.write(f"                     渠道：{', '.join(channels)}")

        self.stdout.write(self.style.SUCCESS(f"Features           : {avail}/{total} 全部可用（功能不做门禁）"))

        if state == "grace":
            self.stdout.write(self.style.WARNING(
                f"⚠️  宽限期剩余 {lic.get('grace_days_left')} 天，到期未续期将回退免费版配额（50 台主机）"
            ))
        elif state == "blocked":
            self.stdout.write(self.style.ERROR(
                "⛔ 已回退免费版配额（限 50 台主机，仅阻止新主机注册，其余功能正常可用）"
            ))

        for w in lic.get("warnings", []):
            self.stdout.write(self.style.WARNING(f"  [{w.get('code')}] {w.get('message')}"))

        if os.environ.get("TAURUS_DEV_BYPASS_LICENSE"):
            self.stdout.write(self.style.WARNING(
                "\n⚠️  TAURUS_DEV_BYPASS_LICENSE=1 — 开发旁路，按 professional 权益放行"
            ))
