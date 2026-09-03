"""
Official Script Loader

Directory layout:
    CE (always present):
        taurus/official_scripts/
            common/bash_common.sh              (optional: auto-injected for Shell scripts)
            system_ops/
                cpu_usage_check/
                    manifest.py                MANIFEST = {"name": ..., "risk_level": ...}
                    script.sh                  Script body
            monitoring/
                process_alive_check/
                    manifest.py
                    script.sh

    EE (present only when taurus_ee package installed):
        taurus_ee/official_scripts/
            system_ops/
                log_rotation/
                    manifest.py
                    script.sh
            ...

manifest.py MANIFEST fields (backward-compatible with legacy flat structure):
    name, script_type, category_name, tags, desc, supported_systems, risk_level,
    official_version, license_type, source_url, changelog, timeout,
    script_params, script_envs, edition_scope

edition_scope: "community" (visible in both CE & EE) or "enterprise" (EE only).

Output OFFICIAL_SCRIPTS list is 100% compatible with legacy flat structure —
zero changes needed in taurus/views.py or any caller.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

# CE scripts root (always exists)
CE_ROOT = Path(__file__).resolve().parent

# EE scripts root (only present when taurus_ee package is installed)
def _find_ee_root() -> Path | None:
    """Find taurus_ee.official_scripts package root via importlib."""
    spec = importlib.util.find_spec("taurus_ee.official_scripts")
    if spec is None or spec.origin is None:
        return None
    # spec.origin points to taurus_ee/official_scripts/__init__.py
    return Path(spec.origin).resolve().parent

EE_ROOT: Path | None = _find_ee_root()

SKIP_DIRS = {"common", "__pycache__", ".git"}

SCRIPT_TYPE_EXT = {
    "Shell": ".sh",
    "Python3": ".py",
    "PowerShell": ".ps1",
    "Bat": ".bat",
    "SQL": ".sql",
}


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    """Load MANIFEST dict from manifest.py."""
    module_name = f"_manifest_{manifest_path.parent.parent.name}_{manifest_path.parent.name}"
    module_name = module_name.replace(" ", "_").replace("-", "_")

    spec = importlib.util.spec_from_file_location(module_name, manifest_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load manifest: {manifest_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"manifest.py syntax error: {manifest_path}, error: {e}") from e
    manifest = getattr(module, "MANIFEST", None)
    if not isinstance(manifest, dict):
        raise RuntimeError(f"manifest.py missing MANIFEST dict: {manifest_path}")
    return manifest


def _find_script_file(script_dir: Path, script_type: str) -> Path:
    """Find script file by script_type (prefers script.*, falls back to dir-name.*)."""
    ext = SCRIPT_TYPE_EXT.get(script_type)
    if ext is None:
        raise ValueError(f"Unknown script_type: {script_type}")

    candidates = [
        script_dir / f"script{ext}",
        script_dir / f"{script_dir.name}{ext}",
    ]
    for c in candidates:
        if c.exists():
            return c

    # fallback: any file with matching extension in the directory
    files = sorted(p for p in script_dir.iterdir() if p.is_file() and p.suffix == ext)
    if files:
        return files[0]
    raise FileNotFoundError(
        f"No {script_type} script found in {script_dir} (extension: {ext})"
    )


def _normalize_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Fill default fields + normalize tags. Fully compatible with legacy flat structure."""
    if "name" not in manifest:
        raise ValueError(f"manifest missing 'name' field: {manifest}")

    manifest.setdefault("script_type", "Shell")
    manifest.setdefault("category_name", "Other")
    tags = manifest.get("tags")
    if isinstance(tags, (list, tuple)):
        manifest["tags"] = ",".join(str(t) for t in tags)
    manifest.setdefault("tags", "")
    manifest.setdefault("desc", "")
    manifest.setdefault("supported_systems", "")
    manifest.setdefault("risk_level", "low")
    manifest.setdefault("official_version", "v1.0.0")
    manifest.setdefault("license_type", "MIT")
    manifest.setdefault("source_url", "")
    manifest.setdefault("changelog", f"{manifest['official_version']} initial version")
    manifest.setdefault("timeout", 300)
    manifest.setdefault("script_params", [])
    manifest.setdefault("script_envs", [])
    # edition_scope: "community" (CE+EE visible) or "enterprise" (EE only)
    manifest.setdefault("edition_scope", "enterprise")
    return manifest


def _inject_bash_common(content: str, manifest: dict[str, Any]) -> str:
    """Auto-inject bash_common.sh library for Shell scripts (skippable via inject_common=False)."""
    if manifest.get("script_type") != "Shell":
        return content
    if manifest.get("inject_common") is False:
        return content

    # bash_common.sh lives in CE common/ — shared by both CE & EE Shell scripts
    common_path = CE_ROOT / "common" / "bash_common.sh"
    if not common_path.exists():
        return content
    common_code = common_path.read_text(encoding="utf-8").rstrip() + "\n"

    lines = content.splitlines(keepends=True)
    if not lines:
        return common_code
    # If first line is shebang, insert common after it; otherwise prepend
    if lines[0].startswith("#!"):
        return lines[0] + "\n" + common_code + "\n" + "".join(lines[1:])
    return common_code + "\n" + content


def _scan_root(root: Path, allow_ee: bool) -> list[dict[str, Any]]:
    """Scan one scripts root dir and return manifest dicts (with Edition Gate filter)."""
    result: list[dict[str, Any]] = []
    for category_dir in sorted(root.iterdir()):
        if not category_dir.is_dir():
            continue
        if category_dir.name in SKIP_DIRS or category_dir.name.startswith("."):
            continue
        for script_dir in sorted(category_dir.iterdir()):
            if not script_dir.is_dir():
                continue
            if script_dir.name.startswith("."):
                continue
            manifest_path = script_dir / "manifest.py"
            if not manifest_path.exists():
                continue
            manifest = _load_manifest(manifest_path)
            manifest = _normalize_manifest(manifest)
            # Edition Gate: CE only sees edition_scope == "community"
            if not allow_ee and manifest.get("edition_scope") != "community":
                continue
            script_file = _find_script_file(script_dir, manifest["script_type"])
            content = script_file.read_text(encoding="utf-8")
            content = _inject_bash_common(content, manifest)
            manifest["content"] = content
            # inject_common is loader-internal, don't leak to DB
            manifest.pop("inject_common", None)
            result.append(manifest)
    return result


def load_official_scripts() -> list[dict[str, Any]]:
    """Load official scripts from CE root + EE root (if installed). Edition Gate applied."""
    # Lazy-load EditionGate to avoid Django settings circular import during manifest loading
    def _edition_allows_enterprise() -> bool:
        try:
            from taurus.editions import has_feature
            return has_feature("SCRIPT_OFFICIAL_MARKET_FULL")
        except Exception:  # noqa: BLE001 — treat as EE during import self-check
            return True

    allow_ee = _edition_allows_enterprise()

    # Scan CE root (always present, filtered by Edition Gate)
    result = _scan_root(CE_ROOT, allow_ee)

    # Scan EE root if present (EE scripts already have edition_scope="enterprise",
    # but we still need allow_ee to be True to load them)
    if EE_ROOT is not None:
        result.extend(_scan_root(EE_ROOT, allow_ee))

    return result


def validate_unique_names(scripts: list[dict[str, Any]]) -> None:
    names = [s["name"] for s in scripts]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise RuntimeError(f"Duplicate official script names: {sorted(dup)}")


OFFICIAL_SCRIPTS: list[dict[str, Any]] = load_official_scripts()
validate_unique_names(OFFICIAL_SCRIPTS)
