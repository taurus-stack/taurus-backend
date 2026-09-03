"""
Request utility class
"""
import json

import requests
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.urls.resolvers import ResolverMatch
from rest_framework_simplejwt.authentication import JWTAuthentication
from user_agents import parse

from dvadmin.system.models import LoginLog


def get_request_user(request):
    """
    Get request user
    (1) If user in request is not authenticated, manually authenticate once
    :param request:
    :return:
    """
    user: AbstractBaseUser = getattr(request, 'user', None)
    if user and user.is_authenticated:
        return user
    try:
        user, tokrn = JWTAuthentication().authenticate(request)
    except Exception as e:
        pass
    return user or AnonymousUser()


def get_request_ip(request):
    """
    Get request IP
    :param request:
    :return:
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[-1].strip()
        return ip
    ip = request.META.get('REMOTE_ADDR', '') or getattr(request, 'request_ip', None)
    return ip or 'unknown'


def get_request_data(request):
    """
    Get request parameters
    :param request:
    :return:
    """
    request_data = getattr(request, 'request_data', None)
    if request_data:
        return request_data
    data: dict = {**request.GET.dict(), **request.POST.dict()}
    if not data:
        try:
            body = request.body
            if body:
                data = json.loads(body)
        except Exception as e:
            pass
        if not isinstance(data, dict):
            data = {'data': data}
    return data


def get_request_path(request, *args, **kwargs):
    """
    Get request path
    :param request:
    :param args:
    :param kwargs:
    :return:
    """
    request_path = getattr(request, 'request_path', None)
    if request_path:
        return request_path
    values = []
    for arg in args:
        if len(arg) == 0:
            continue
        if isinstance(arg, str):
            values.append(arg)
        elif isinstance(arg, (tuple, set, list)):
            values.extend(arg)
        elif isinstance(arg, dict):
            values.extend(arg.values())
    if len(values) == 0:
        return request.path
    path: str = request.path
    for value in values:
        path = path.replace('/' + value, '/' + '{id}')
    return path


def get_request_canonical_path(request, ):
    """
    Get request canonical path
    :param request:
    :param args:
    :param kwargs:
    :return:
    """
    request_path = getattr(request, 'request_canonical_path', None)
    if request_path:
        return request_path
    path: str = request.path
    resolver_match: ResolverMatch = request.resolver_match
    for value in resolver_match.args:
        path = path.replace(f"/{value}", "/{id}")
    for key, value in resolver_match.kwargs.items():
        if key == 'pk':
            path = path.replace(f"/{value}", f"/{{id}}")
            continue
        path = path.replace(f"/{value}", f"/{{{key}}}")

    return path


def get_browser(request, ):
    """
    Get browser name
    :param request:
    :param args:
    :param kwargs:
    :return:
    """
    ua_string = request.META.get('HTTP_USER_AGENT', '')
    user_client = parse(ua_string)
    return user_client.get_browser()


def get_os(request, ):
    """
    Get operating system
    :param request:
    :param args:
    :param kwargs:
    :return:
    """
    ua_string = request.META.get('HTTP_USER_AGENT', '')
    user_client = parse(ua_string)
    return user_client.get_os()


def get_verbose_name(queryset=None, view=None, model=None):
    """
    Get verbose_name
    :param request:
    :param view:
    :return:
    """
    try:
        if queryset is not None and hasattr(queryset, 'model'):
            model = queryset.model
        elif view and hasattr(view.get_queryset(), 'model'):
            model = view.get_queryset().model
        elif view and hasattr(view.get_serializer(), 'Meta') and hasattr(view.get_serializer().Meta, 'model'):
            model = view.get_serializer().Meta.model
        if model:
            return getattr(model, '_meta').verbose_name
        else:
            model = queryset.model._meta.verbose_name
    except Exception as e:
        pass
    return model if model else ""


def get_ip_analysis(ip):
    """
    Get IP detail overview
    :param ip: IP address
    :return:
    """
    data = {
        "continent": "",
        "country": "",
        "province": "",
        "city": "",
        "district": "",
        "isp": "",
        "area_code": "",
        "country_english": "",
        "country_code": "",
        "longitude": "",
        "latitude": ""
    }
    if ip != 'unknown' and ip:
        if getattr(settings, 'ENABLE_LOGIN_ANALYSIS_LOG', True):
            try:
                res = requests.get(url='https://ip.django-vue-admin.com/ip/analysis', params={"ip": ip}, timeout=5)
                if res.status_code == 200:
                    res_data = res.json()
                    if res_data.get('code') == 0:
                        data = res_data.get('data')
                return data
            except Exception as e:
                print(e)
    return data


def save_login_log(request):
    """
    Save login log
    :return:
    """
    ip = get_request_ip(request=request)
    analysis_data = get_ip_analysis(ip)
    analysis_data['username'] = request.user.username
    analysis_data['ip'] = ip
    analysis_data['agent'] = str(parse(request.META.get('HTTP_USER_AGENT', '')))
    analysis_data['browser'] = get_browser(request)
    analysis_data['os'] = get_os(request)
    analysis_data['creator_id'] = request.user.id
    analysis_data['dept_belong_id'] = getattr(request.user, 'dept_id', '')
    LoginLog.objects.create(**analysis_data)