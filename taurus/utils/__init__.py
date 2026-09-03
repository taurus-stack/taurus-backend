"""taurus common utility functions"""
from typing import Optional


def map_script_type(display_type: Optional[str]) -> str:
    """Map Script model's script_type (Shell/Python3/PowerShell/Bat/SQL) to execution layer type (sh/python/powershell/bat/sql)."""
    if not display_type:
        return "sh"
    t = display_type.lower()
    if "shell" in t or "bash" in t or t == "sh":
        return "sh"
    if "python" in t:
        return "python"
    if "powershell" in t:
        return "powershell"
    if t == "bat":
        return "bat"
    return t or "sh"