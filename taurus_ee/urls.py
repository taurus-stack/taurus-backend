"""taurus_ee URLconf（M2.1+ 逐步迁入 EE 命名空间路由）。

注意：`taurus/urls.py` 中的「老 URL 前缀」不会改（保持前端 0 改动），
这里的路由是 EE 专属新增接口的命名空间 `taurus_ee:`，例如：
    taurus_ee:script-check-run  →  /api/taurus-ee/script-check/run/
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

app_name = "taurus_ee"

router = DefaultRouter()
# M2.1+ migration 阶段逐个 register：
#   router.register("script-check", ScriptCheckViewSet, basename="ee-script-check")

urlpatterns = [
    path("", include(router.urls)),
]
