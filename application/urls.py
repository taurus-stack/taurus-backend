"""backend URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf.urls.static import static
from django.urls import path, include, re_path
from django.views.generic import RedirectView
from rest_framework_simplejwt.views import (
    TokenRefreshView,
)
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView

from application import dispatch
from application import settings
from dvadmin.system.views.dictionary import InitDictionaryViewSet, HostTypeDictionaryViewSet
from dvadmin.system.views.login import (
    LoginView,
    CaptchaView,
    ApiLogin,
    LogoutView,
    LoginTokenView
)
from dvadmin.system.views.system_config import InitSettingsViewSet
from dvadmin.system.views.bootstrap import BootstrapViewSet

# =========== initializeSystem config =================
dispatch.init_system_config()
dispatch.init_dictionary()
# =========== initializeSystem config =================


def _metrics_view(request):
    """S1-06 Prometheus 指标端.:return prometheus_client 采集的指标."""
    from django.http import HttpResponse

    try:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    except ImportError:
        return HttpResponse(
            'prometheus_client not installed',
            status=503,
            content_type='text/plain',
        )
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)

# Frontend面map
from django.http import Http404, HttpResponse
from django.shortcuts import render
import mimetypes
import os


def web_view(request):
    return render(request, 'web/index.html')


def serve_web_files(request, filename):
    # 设定Filepath
    filepath = os.path.join(settings.BASE_DIR, 'templates', 'web', filename)

    # checkFileYesNo存在
    if not os.path.exists(filepath):
        raise Http404("File does not exist")

    # according toFilescale out名, 确定 MIME class型
    mime_type, _ = mimetypes.guess_type(filepath)

    # OpenFile并read内容
    with open(filepath, 'rb') as f:
        response = HttpResponse(f.read(), content_type=mime_type)
        return response


urlpatterns = (
        [
            # API Schema and Documentation
            path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
            # Legacy paths for backward compatibility
            path("swagger.json", RedirectView.as_view(url="/api/schema/", permanent=False)),
            path("swagger/", RedirectView.as_view(url="/api/schema/swagger-ui/", permanent=False)),
            path("redoc/", RedirectView.as_view(url="/api/schema/redoc/", permanent=False)),
            path(
                "api/schema/swagger-ui/",
                SpectacularSwaggerView.as_view(url_name="schema"),
                name="swagger-ui",
            ),
            path(
                "api/schema/redoc/",
                SpectacularRedocView.as_view(url_name="schema"),
                name="redoc",
            ),
            path("api/system/", include("dvadmin.system.urls")),
            # WorkflowAPI
            path("api/taurus/", include("taurus.urls")),
            path("api/login/", LoginView.as_view(), name="token_obtain_pair"),
            path("api/logout/", LogoutView.as_view(), name="token_obtain_pair"),
            path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
            re_path(
                r"^api-auth/", include("rest_framework.urls", namespace="rest_framework")
            ),
            path("api/host_type/", HostTypeDictionaryViewSet.as_view()),
            path("api/captcha/", CaptchaView.as_view()),
            path("api/init/dictionary/", InitDictionaryViewSet.as_view()),
            path("api/init/settings/", InitSettingsViewSet.as_view()),
            path("api/init/bootstrap/", BootstrapViewSet.as_view()),
            path("apiLogin/", ApiLogin.as_view()),

            # Only used fordevelop, 上line需Close
            path("api/token/", LoginTokenView.as_view()),
            # Frontend面map
            path('web/', web_view, name='web_view'),
            path('web/<path:filename>', serve_web_files, name='serve_web_files'),
            # S1-06 Prometheus 指标端.(prometheus_client Not installed时return 503)
            path('metrics/', _metrics_view, name='prometheus_metrics'),
        ]
        + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
        + static(settings.STATIC_URL, document_root=settings.STATIC_URL)
        + [re_path(ele.get('re_path'), include(ele.get('include'))) for ele in settings.PLUGINS_URL_PATTERNS]
)