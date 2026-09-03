"""
官方内置Scriptlibrary(directory化Version)

用法保持与旧版单File 100% 向后兼容:
    from taurus.official_scripts import OFFICIAL_SCRIPTS

所有Script以「Category / Script名」组织在childdirectory中:
    System ops/磁盘use率巡检/manifest.py + script.sh
    ...

具体维护方式, FieldDescription见项目relateddocument.
"""

from .loader import OFFICIAL_SCRIPTS

__all__ = ["OFFICIAL_SCRIPTS"]