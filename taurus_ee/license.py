"""
taurus_ee.license — 商业 License 验签 + 状态解析 + 服务等级权益模块。

全功能开源版本起，License **不做功能门禁**，只决定：
    1. 主机配额（quota.max_hosts 等）
    2. 服务等级权益（白标 / 升级通道 / 支持服务，见 editions.features.TIER_ENTITLEMENTS）

License 文件格式（纯文本 .lic，可 base64 编码传输）：

    {
      "payload": {
        "customer_id": "C-2026-0042",
        "customer_name": "XX 有限公司",
        "tier": "professional",           # starter | professional | enterprise | ultimate
        "expires_at": "2027-12-31T23:59:59+08:00",
        "machine_fingerprint": "sha256:abcdef...",
        "quota": {
          "max_hosts": 500,
          "max_users": 100,
          ...
        },
        "features": ["..."],              # 保留解析，全功能版本不再用于门禁
        "issued_at": "2026-09-01T00:00:00+08:00",
        "nonce": "uuid"
      },
      "signature_b64": "base64(rsa_pss_sha256(json(payload), PRIV_KEY))"
    }

状态机（LicenseStatus.state）：
    free      无 License 文件                → 免费版：max_hosts=50 + 社区服务等级
    licensed  验签有效且未过期                → 按 tier/quota 享有权益
    grace     已过期 ≤ LICENSE_GRACE_DAYS 天 → 权益不变，仅告警（宽限期）
    blocked   过期超宽限期 / 指纹不匹配 / 签名无效
                                            → 回退免费版配额（50 台），阻止新主机注册

注意：
    RSA 私钥只留在 License 签发服务器，绝不进代码仓库。
    本模块内置的是 **公钥**，用于验签（无法伪造）。
    dev 模式（TAURUS_DEV_BYPASS_LICENSE=1）返回 professional 旁路状态，仅用于本地联调。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from taurus.editions.features import (
    LICENSE_GRACE_DAYS,
    QuotaDefaults,
    TIER_ENTITLEMENTS,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RSA 公钥（生产版）— 对应签发服务器的私钥
# 格式：PEM-encoded SubjectPublicKeyInfo（多行 PEM，保留换行）
# 真实公钥由 `scripts/gen_rsa_keys.py` 生成，签发服务器保管对应私钥。
# ---------------------------------------------------------------------------
_PUBLIC_KEY_PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAzVcrv2GwxZs1DNq136iK\n"
    "GZWV7ata5Y6R4aHN2fzFZA4TNvlIvNg9AlOhCBOmO7B42/tfKFLJOgmqmva31KM6\n"
    "q0dNqV8IG+57srQpYvsgolwed1o4tk6WUqiCr0TZD8qO5kQi+id7eczZWoCBpoA/\n"
    "ZAyAgj2NvsB0sq4dGhApZVTeJijGVY0m5zgZCCK05OSxAlCqV2NzPEEg1h3iqPBA\n"
    "BWzubeS3Vejd3PaZdAmNx7/plVvn5fB6Z/xSzNarqgMHTh7/RrOeo973xC/vfMOu\n"
    "/Bs4mXuZhDEEHN9988SIFeWbdQVec1YghWX6OsHsHmtrMfbKZLPAC6qfmiOfu7bW\n"
    "rwIDAQAB\n"
    "-----END PUBLIC KEY-----\n"
)

# 状态机常量
STATE_FREE = "free"
STATE_LICENSED = "licensed"
STATE_GRACE = "grace"
STATE_BLOCKED = "blocked"
VALID_STATES = (STATE_FREE, STATE_LICENSED, STATE_GRACE, STATE_BLOCKED)


def _load_public_key() -> rsa.RSAPublicKey:
    """从 PEM 加载内置 RSA 公钥。"""
    pem = _PUBLIC_KEY_PEM.encode("ascii")
    key = serialization.load_pem_public_key(pem)
    assert isinstance(key, rsa.RSAPublicKey), "Public key must be RSA"
    return key


# ---------------------------------------------------------------------------
# 机器指纹
# ---------------------------------------------------------------------------
def compute_machine_fingerprint() -> str:
    """计算当前机器指纹：sha256(hostname | primary mac | root disk serial).

    尽量选难以伪造的字段组合；任何一项取不到就跳过而不是失败。
    """
    parts: List[str] = []

    # hostname
    try:
        parts.append(socket.gethostname())
    except OSError:
        pass

    # 主 MAC 地址（按 ifindex 最小取第一个可用网卡）
    try:
        import fcntl  # type: ignore[import-not-found]
        import struct as _struct
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for ifname in ("eth0", "ens33", "en0", "lo"):
            try:
                SIOCGIFHWADDR = 0x8927
                result = fcntl.ioctl(sock.fileno(), SIOCGIFHWADDR,
                                     _struct.pack("256s", ifname.encode()))
                mac = result[18:24]
                parts.append(":".join(f"{b:02x}" for b in mac))
                break
            except OSError:
                continue
    except ImportError:
        # macOS / Windows 可能没有 fcntl，跳过
        pass

    # 磁盘 serial (只读 /etc/machine-id 或 root disk uuid)
    for path_str in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            parts.append(Path(path_str).read_text().strip())
            break
        except OSError:
            continue

    raw = "|".join(parts)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return f"sha256:{digest}"


# ---------------------------------------------------------------------------
# License 文件定位
# ---------------------------------------------------------------------------
def _find_license_file() -> Optional[Path]:
    """按优先级找 License 文件路径."""
    env = os.environ.get("TAURUS_LICENSE_FILE")
    if env:
        p = Path(env)
        if p.is_file():
            return p

    # 默认搜索路径
    candidates = [
        Path("/etc/taurus/license.lic"),
        Path("/opt/taurus/license.lic"),
        Path.home() / ".taurus" / "license.lic",
        Path.cwd() / "license.lic",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


# ---------------------------------------------------------------------------
# 权益解析
# ---------------------------------------------------------------------------
def _safe_tier(tier: Optional[str]) -> str:
    """未知 tier 兜底为 community。"""
    if tier and tier in TIER_ENTITLEMENTS:
        return tier
    return "community"


def _resolve_quota(tier: str, override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Tier 默认配额 + License 内 quota 覆盖。"""
    quota: Dict[str, Any] = dict(QuotaDefaults.for_tier(_safe_tier(tier)))  # type: ignore[arg-type]
    if isinstance(override, dict):
        quota.update({k: v for k, v in override.items() if v is not None})
    return quota


def _entitlement(tier: str) -> Dict[str, Any]:
    return TIER_ENTITLEMENTS[_safe_tier(tier)]


def compute_hosts_used() -> Optional[int]:
    """当前纳管主机数（与 views.py 配额拦截点口径一致：Host.objects.count()）。

    Django 未就绪 / 查询失败时返回 None，不阻塞 License 解析。
    """
    try:
        from taurus.models import Host
        return Host.objects.count()
    except Exception:  # noqa: BLE001 — apps 未就绪、迁移未跑等场景
        return None


# ---------------------------------------------------------------------------
# License 解析 + 验签
# ---------------------------------------------------------------------------
class LicenseError(Exception):
    """License 校验失败的统一异常；msg 会透传给 user-facing warnings."""


class LicenseStatus:
    """License 解析 + 验签后的完整状态。

    state 语义见模块 docstring。valid 仅在 licensed/grace/free 为 True；
    blocked 为 False（调用方也可直接判断 state）。
    """

    def __init__(
        self,
        *,
        state: str = STATE_FREE,
        valid: bool = True,
        tier: str = "community",
        customer_id: Optional[str] = None,
        customer_name: Optional[str] = None,
        expires_at: Optional[str] = None,
        fingerprint_ok: bool = True,
        quota: Optional[Dict[str, Any]] = None,
        features: Optional[List[str]] = None,
        warnings: Optional[List[Dict[str, Any]]] = None,
        grace_days_left: Optional[int] = None,
    ):
        self.state = state if state in VALID_STATES else STATE_BLOCKED
        self.valid = valid
        self.tier = _safe_tier(tier)
        self.customer_id = customer_id
        self.customer_name = customer_name
        self.expires_at = expires_at
        self.fingerprint_ok = fingerprint_ok
        self.quota = quota or dict(QuotaDefaults.COMMUNITY)
        self.features = features or []
        self.warnings = warnings or []
        self.grace_days_left = grace_days_left

    # ------------------------------------------------------------------ 权益
    @property
    def branding_allowed(self) -> bool:
        return bool(_entitlement(self.tier)["branding_allowed"])

    @property
    def update_channels(self) -> List[str]:
        return list(_entitlement(self.tier)["update_channels"])

    @property
    def service_level(self) -> Dict[str, Any]:
        return dict(_entitlement(self.tier)["support"])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "state": self.state,
            "tier": self.tier,
            "customer_id": self.customer_id,
            "customer_name": self.customer_name,
            "expires_at": self.expires_at,
            "fingerprint_ok": self.fingerprint_ok,
            "quota": self.quota,
            "features": self.features,
            "warnings": self.warnings,
            "grace_days_left": self.grace_days_left,
            "branding_allowed": self.branding_allowed,
            "update_channels": self.update_channels,
            "service_level": self.service_level,
            "hosts_used": compute_hosts_used(),
        }


# ---------------------------------------------------------------------------
# 各状态工厂
# ---------------------------------------------------------------------------
def free_status() -> LicenseStatus:
    """无 License 的免费版状态。"""
    return LicenseStatus(
        state=STATE_FREE,
        valid=True,
        tier="community",
        customer_name="社区版（免费）",
        quota=dict(QuotaDefaults.COMMUNITY),
    )


def blocked_status(
    reason_code: str,
    message: str,
    *,
    tier: str = "community",
    customer_id: Optional[str] = None,
    customer_name: Optional[str] = None,
    expires_at: Optional[str] = None,
    fingerprint_ok: bool = True,
    features: Optional[List[str]] = None,
) -> LicenseStatus:
    """blocked 状态：回退免费版配额（限 50 台），权益回退社区等级。"""
    return LicenseStatus(
        state=STATE_BLOCKED,
        valid=False,
        tier="community",
        customer_id=customer_id,
        customer_name=customer_name,
        expires_at=expires_at,
        fingerprint_ok=fingerprint_ok,
        quota=dict(QuotaDefaults.COMMUNITY),
        features=features or [],
        warnings=[{"code": reason_code, "message": message}],
    )


def verify_license_file(path: Path) -> LicenseStatus:
    """读取 + 验签 + 完整 License 状态检查。

    签名错误 / 文件非法时抛 LicenseError（调用方据此进入 blocked）；
    过期不再抛异常，而是返回 grace / blocked 状态。
    """
    raw = path.read_text(encoding="utf-8").strip()
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LicenseError(f"License 文件不是合法 JSON: {e}") from e

    payload = doc.get("payload")
    sig_b64 = doc.get("signature_b64")
    if not isinstance(payload, dict) or not isinstance(sig_b64, str):
        raise LicenseError("License 文件缺少 payload 或 signature_b64 字段")

    # 1. RSA 验签（失败直接抛错 → blocked）
    _verify_signature(payload, sig_b64)

    tier = _safe_tier(str(payload.get("tier", "professional")))
    customer_id = payload.get("customer_id")
    customer_name = payload.get("customer_name")
    features = list(payload.get("features") or [])

    # 2. 过期检查（宽限状态机）
    expires_raw = payload.get("expires_at")
    expires_dt: Optional[datetime] = None
    expiry_warning: Optional[Dict[str, Any]] = None
    state = STATE_LICENSED
    grace_days_left: Optional[int] = None
    if isinstance(expires_raw, str):
        try:
            expires_dt = datetime.fromisoformat(expires_raw)
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
        except ValueError as e:
            raise LicenseError(f"expires_at 格式错误: {expires_raw}") from e

        now = datetime.now(timezone.utc)
        if expires_dt < now:
            days_expired = (now - expires_dt).days
            if days_expired <= LICENSE_GRACE_DAYS:
                # 宽限期：保留原 tier 权益与配额
                state = STATE_GRACE
                grace_days_left = max(0, LICENSE_GRACE_DAYS - days_expired)
                expiry_warning = {
                    "code": "EXPIRY_GRACE",
                    "message": (
                        f"License 已过期 {days_expired} 天，处于 {LICENSE_GRACE_DAYS} 天宽限期"
                        f"（剩余 {grace_days_left} 天），宽限期结束后将回退免费版配额，请尽快续期"
                    ),
                    "days_expired": days_expired,
                    "grace_days_left": grace_days_left,
                }
            else:
                return blocked_status(
                    "LICENSE_EXPIRED_BLOCKED",
                    (
                        f"License 已过期 {days_expired} 天，超过 {LICENSE_GRACE_DAYS} 天宽限期，"
                        "已回退免费版配额（限 50 台主机），请续期后恢复完整权益"
                    ),
                    tier=tier,
                    customer_id=customer_id,
                    customer_name=customer_name,
                    expires_at=expires_raw,
                    features=features,
                )
        else:
            delta = expires_dt - now
            if 0 < delta.days <= 30:
                expiry_warning = {
                    "code": "EXPIRING_SOON",
                    "message": f"License 将在 {delta.days} 天后过期（expires_at={expires_raw}）",
                    "days_left": delta.days,
                }

    # 3. 机器指纹（不匹配直接 blocked，不再"valid 但告警"）
    expected_fp = payload.get("machine_fingerprint")
    current_fp = compute_machine_fingerprint()
    fp_ok = (expected_fp is None) or (expected_fp == current_fp)
    if not fp_ok:
        return blocked_status(
            "MACHINE_MISMATCH",
            (
                f"License 机器指纹不匹配（期望 {expected_fp}，当前 {current_fp}）。"
                "已回退免费版配额，请在原始机器上使用，或联系销售重新绑定。"
            ),
            tier=tier,
            customer_id=customer_id,
            customer_name=customer_name,
            expires_at=expires_raw,
            fingerprint_ok=False,
            features=features,
        )

    # 4. licensed / grace：tier 默认配额 + License quota 覆盖
    warnings: List[Dict[str, Any]] = []
    if expiry_warning:
        warnings.append(expiry_warning)

    return LicenseStatus(
        state=state,
        valid=True,
        tier=tier,
        customer_id=customer_id,
        customer_name=customer_name,
        expires_at=expires_raw,
        fingerprint_ok=True,
        quota=_resolve_quota(tier, payload.get("quota")),
        features=features,
        warnings=warnings,
        grace_days_left=grace_days_left,
    )


def _verify_signature(payload: Dict[str, Any], sig_b64: str) -> None:
    """RSA-PSS-SHA256 验签.

    payload 的 JSON 规范化顺序必须一致——签发和验签都用 sort_keys=True.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        signature = base64.b64decode(sig_b64, validate=True)
    except Exception as e:
        raise LicenseError(f"signature_b64 base64 解码失败: {e}") from e

    pubkey = _load_public_key()
    try:
        pubkey.verify(
            signature,
            canonical,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
    except InvalidSignature as e:
        raise LicenseError("签名验证失败（payload 被篡改或公钥不匹配）") from e


# ---------------------------------------------------------------------------
# 缓存 + 顶层入口
# ---------------------------------------------------------------------------
_cache: Optional[LicenseStatus] = None
_cache_path: Optional[str] = None


def load_license(force_reload: bool = False) -> LicenseStatus:
    """加载 + 验签 License，带内存缓存（文件路径变化或 force_reload 会重验）."""
    global _cache, _cache_path

    # 开发旁路
    if os.environ.get("TAURUS_DEV_BYPASS_LICENSE"):
        return LicenseStatus(
            state=STATE_LICENSED,
            valid=True,
            tier="professional",
            customer_name="Development (TAURUS_DEV_BYPASS_LICENSE=1)",
            quota=dict(QuotaDefaults.PROFESSIONAL),
            warnings=[{"code": "DEV_MODE", "message": "开发旁路模式，按 professional 权益放行"}],
        )

    path = _find_license_file()
    if path is None:
        # 无 License = 免费版（全功能 + 50 台主机 + 社区服务等级）
        return free_status()

    # 缓存命中：同路径且 force_reload=False
    if not force_reload and _cache is not None and _cache_path == str(path):
        return _cache

    try:
        status = verify_license_file(path)
    except LicenseError as e:
        logger.warning("License 校验失败: %s", e)
        status = blocked_status("INVALID_LICENSE", str(e))

    _cache = status
    _cache_path = str(path)
    return status


def invalidate_cache() -> None:
    """License 文件导入 / 热更后调用."""
    global _cache, _cache_path
    _cache = None
    _cache_path = None


# ---------------------------------------------------------------------------

__all__ = [
    "LicenseError",
    "LicenseStatus",
    "load_license",
    "invalidate_cache",
    "compute_machine_fingerprint",
    "compute_hosts_used",
    "verify_license_file",
    "free_status",
    "blocked_status",
    "STATE_FREE",
    "STATE_LICENSED",
    "STATE_GRACE",
    "STATE_BLOCKED",
]
