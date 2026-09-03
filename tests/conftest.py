"""项目级 conftest:initialize Django settings 以便 tests 可直接use taurus.* module."""
from __future__ import annotations

import os

# 在任何 django.* import before设定 settings module
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "application.settings")

import django  # noqa: E402

django.setup()

# 把 conf.env 中 INSTALLED_UNIT_ADAPTERS 显式load一遍(等价于 TaurusConfig.ready 的行为).
# 某些 pytest 收集path下 apps.ready() may在 conftest afterTrigger, 这里做Idempotencyload.
import importlib  # noqa: E402
import logging  # noqa: E402

try:
    from conf.env import INSTALLED_UNIT_ADAPTERS  # noqa: E402
except Exception:  # pragma: no cover - 收集environmentconfigexception不会在正常 pytest 出现
    INSTALLED_UNIT_ADAPTERS = []

_logger = logging.getLogger(__name__)
for _mod in INSTALLED_UNIT_ADAPTERS or []:
    try:
        importlib.import_module(_mod)
    except Exception:  # pragma: no cover
        _logger.exception("conftest: 加载适配器失败: %s", _mod)
        raise