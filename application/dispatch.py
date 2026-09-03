#!/usr/bin/env python
# -*- coding: utf-8 -*-
from django.conf import settings
from django.db import connection


def is_tenants_mode():
    """
    判断YesNo为租户模式
    :return:
    """
    return hasattr(connection, "tenant") and connection.tenant.schema_name


# ================================================= #
# ******************** initialize ******************** #
# ================================================= #
def _get_all_dictionary():
    from dvadmin.system.models import Dictionary

    queryset = Dictionary.objects.filter(status=True, is_value=False)
    data = []
    for instance in queryset:
        data.append(
            {
                "id": instance.id,
                "value": instance.value,
                "children": list(
                    Dictionary.objects.filter(parent=instance.id)
                    .filter(status=1)
                    .values("label", "value", "type", "color")
                ),
            }
        )
    return {ele.get("value"): ele for ele in data}


def _get_all_system_config():
    data = {}
    from dvadmin.system.models import SystemConfig

    system_config_obj = (
        SystemConfig.objects.filter(parent_id__isnull=False)
        .values("parent__key", "key", "value", "form_item_type")
        .order_by("sort")
    )
    for system_config in system_config_obj:
        value = system_config.get("value", "")
        if value and system_config.get("form_item_type") == 7:
            value = value[0].get("url")
        if value and system_config.get("form_item_type") == 11:
            new_value = []
            for ele in value:
                new_value.append({
                    "key": ele.get('key'),
                    "title": ele.get('title'),
                    "value": ele.get('value'),
                })
            new_value.sort(key=lambda s: s["key"])
            value = new_value
        data[f"{system_config.get('parent__key')}.{system_config.get('key')}"] = value
    return data


def init_dictionary():
    """
    initializeDictionaryConfig
    :return:
    """
    try:
        if is_tenants_mode():
            from django_tenants.utils import tenant_context, get_tenant_model

            for tenant in get_tenant_model().objects.filter():
                with tenant_context(tenant):
                    settings.DICTIONARY_CONFIG[connection.tenant.schema_name] = _get_all_dictionary()
        else:
            settings.DICTIONARY_CONFIG = _get_all_dictionary()
    except Exception as e:
        print("Please run database migrations first!")
    return


def init_system_config():
    """
    initializeSystem config
    :param name:
    :return:
    """
    try:

        if is_tenants_mode():
            from django_tenants.utils import tenant_context, get_tenant_model

            for tenant in get_tenant_model().objects.filter():
                with tenant_context(tenant):
                    settings.SYSTEM_CONFIG[connection.tenant.schema_name] = _get_all_system_config()
        else:
            settings.SYSTEM_CONFIG = _get_all_system_config()
    except Exception as e:
        print("Please run database migrations first!")
    return


def refresh_dictionary():
    """
    RefreshDictionaryConfig
    :return:
    """
    if is_tenants_mode():
        from django_tenants.utils import tenant_context, get_tenant_model

        for tenant in get_tenant_model().objects.filter():
            with tenant_context(tenant):
                settings.DICTIONARY_CONFIG[connection.tenant.schema_name] = _get_all_dictionary()
    else:
        settings.DICTIONARY_CONFIG = _get_all_dictionary()


def refresh_system_config():
    """
    RefreshSystem config
    :return:
    """
    if is_tenants_mode():
        from django_tenants.utils import tenant_context, get_tenant_model

        for tenant in get_tenant_model().objects.filter():
            with tenant_context(tenant):
                settings.SYSTEM_CONFIG[connection.tenant.schema_name] = _get_all_system_config()
    else:
        settings.SYSTEM_CONFIG = _get_all_system_config()


# ================================================= #
# ******************** Dictionary管理 ******************** #
# ================================================= #
def get_dictionary_config(schema_name=None):
    """
    FetchDictionary所有Config
    :param schema_name: 对应DictionaryConfig的租户schema_name值
    :return:
    """
    if not settings.DICTIONARY_CONFIG:
        refresh_dictionary()
    if is_tenants_mode():
        dictionary_config = settings.DICTIONARY_CONFIG[schema_name or connection.tenant.schema_name]
    else:
        dictionary_config = settings.DICTIONARY_CONFIG
    return dictionary_config or {}


def get_dictionary_values(key, schema_name=None):
    """
    FetchDictionary数据array
    :param key: 对应DictionaryConfig的key值(Dictionary key)
    :param schema_name: 对应DictionaryConfig的租户schema_name值
    :return:
    """
    dictionary_config = get_dictionary_config(schema_name)
    return dictionary_config.get(key)


def get_dictionary_label(key, name, schema_name=None):
    """
    FetchFetchDictionarylabel值
    :param key: Dictionary管理中的key值(Dictionary key)
    :param name: 对应DictionaryConfig的value值
    :param schema_name: 对应DictionaryConfig的租户schema_name值
    :return:
    """
    children = get_dictionary_values(key, schema_name) or []
    for ele in children:
        if ele.get("value") == str(name):
            return ele.get("label")
    return ""


# ================================================= #
# ******************** System config ******************** #
# ================================================= #
def get_system_config(schema_name=None):
    """
    FetchSystem config中所有Config
    1.只传Parent的key, returnAllchild级, { "Parentkey.child level key" : "值" }
    2."Parentkey.child level key", returnchild级值
    :param schema_name: 对应DictionaryConfig的租户schema_name值
    :return:
    """
    if not settings.SYSTEM_CONFIG:
        refresh_system_config()
    if is_tenants_mode():
        dictionary_config = settings.SYSTEM_CONFIG[schema_name or connection.tenant.schema_name]
    else:
        dictionary_config = settings.SYSTEM_CONFIG
    return dictionary_config or {}


def get_system_config_values(key, schema_name=None):
    """
    FetchSystem config数据array
    :param key: 对应System config的key值(Dictionary key)
    :param schema_name: 对应System config的租户schema_name值
    :return:
    """
    system_config = get_system_config(schema_name)
    return system_config.get(key)


def get_system_config_label(key, name, schema_name=None):
    """
    FetchFetchSystem configlabel值
    :param key: System config中的key值(Dictionary key)
    :param name: 对应System config的value值
    :param schema_name: 对应System config的租户schema_name值
    :return:
    """
    children = get_system_config_values(key, schema_name) or []
    for ele in children:
        if ele.get("value") == str(name):
            return ele.get("label")
    return ""