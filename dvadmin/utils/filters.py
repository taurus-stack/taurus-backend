# -*- coding: utf-8 -*-

"""
@author: Yuan Xiaotian
@contact: QQ:1638245306
@Created on: 2021/6/6 12:39
@Remark: Custom filter
"""
import operator
import re
from collections import OrderedDict
from functools import reduce

import six
from django.db import models
from django.db.models import Q, F
from django.db.models.constants import LOOKUP_SEP
from django_filters import utils, FilterSet
from django_filters.constants import ALL_FIELDS
from django_filters.filters import CharFilter, DateTimeFromToRangeFilter
from django_filters.rest_framework import DjangoFilterBackend
from django_filters.utils import get_model_field
from rest_framework.filters import BaseFilterBackend
from django_filters.conf import settings
from dvadmin.system.models import Dept, ApiWhiteList, RoleMenuButtonPermission
from dvadmin.utils.models import CoreModel

class CoreModelFilterBankend(BaseFilterBackend):
    """
    Custom datetime range filter
    """
    def filter_queryset(self, request, queryset, view):
        create_datetime_after = request.query_params.get('create_datetime_after', None)
        create_datetime_before = request.query_params.get('create_datetime_before', None)
        update_datetime_after = request.query_params.get('update_datetime_after', None)
        update_datetime_before = request.query_params.get('update_datetime_after', None)
        if any([create_datetime_after, create_datetime_before, update_datetime_after, update_datetime_before]):
            create_filter = Q()
            if create_datetime_after and create_datetime_before:
                create_filter &= Q(create_datetime__gte=create_datetime_after) & Q(create_datetime__lte=create_datetime_before)
            elif create_datetime_after:
                create_filter &= Q(create_datetime__gte=create_datetime_after)
            elif create_datetime_before:
                create_filter &= Q(create_datetime__lte=create_datetime_before)

            # Update datetime range filter conditions
            update_filter = Q()
            if update_datetime_after and update_datetime_before:
                update_filter &= Q(update_datetime__gte=update_datetime_after) & Q(update_datetime__lte=update_datetime_before)
            elif update_datetime_after:
                update_filter &= Q(update_datetime__gte=update_datetime_after)
            elif update_datetime_before:
                update_filter &= Q(update_datetime__lte=update_datetime_before)
            # Combine two datetime range filter conditions
            queryset = queryset.filter(create_filter & update_filter)
            return queryset
        return queryset

def get_dept(dept_id: int, dept_all_list=None, dept_list=None):
    """
    Recursively get all subordinate depts of a dept
    :param dept_id: dept id to get
    :param dept_all_list: all depts list
    :param dept_list: recursive dept list
    :return:
    """
    if not dept_all_list:
        dept_all_list = Dept.objects.all().values("id", "parent")
    if dept_list is None:
        dept_list = [dept_id]
    for ele in dept_all_list:
        if ele.get("parent") == dept_id:
            dept_list.append(ele.get("id"))
            get_dept(ele.get("id"), dept_all_list, dept_list)
    return list(set(dept_list))


class DataLevelPermissionsFilter(BaseFilterBackend):
    """
    Data level permission filter
    0. Get user dept id, return empty if no dept
    1. Check whether filtered data has "creator" dept field, return all if not present
    2. If user has no associated roles, return current dept data
    3. Filter data by max role permission (multiple roles, deduplicate and take max permission)
    3.1 Check if user is super admin role / if has 1 (all data) return all data

    4. When only self-data permission is set, only return filtered self-data, and dept is current user's own dept (considering dept changes, only see current user's dept data)
    5. Custom data permission - get depts, filter by dept
    """

    def filter_queryset(self, request, queryset, view):
        """
        Whether API whitelist authenticates data permissions
        """
        api = request.path  # Current request API
        method = request.method  # Current request method
        methodList = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
        method = methodList.index(method)
        # ***API whitelist***
        api_white_list = ApiWhiteList.objects.filter(enable_datasource=False).values(
            permission__api=F("url"), permission__method=F("method")
        )
        api_white_list = [
            str(item.get("permission__api").replace("{id}", ".*?"))
            + ":"
            + str(item.get("permission__method"))
            for item in api_white_list
            if item.get("permission__api")
        ]
        for item in api_white_list:
            new_api = f"{api}:{method}"
            matchObj = re.match(item, new_api, re.M | re.I)
            if matchObj is None:
                continue
            else:
                return queryset
        """
        Check if super admin:
        If not super admin, proceed to next permission check
        """
        if request.user.is_superuser == 0:
            return self._extracted_from_filter_queryset_33(request, queryset, api, method)
        else:
            return queryset

    # TODO Rename this here and in `filter_queryset`
    def _extracted_from_filter_queryset_33(self, request, queryset, api, method):
        # 0. Get user dept id, return empty if no dept
        user_dept_id = getattr(request.user, "dept_id", None)
        if not user_dept_id:
            return queryset.none()

        # 1. Check whether filtered data has "dept_belong_id" field
        if not getattr(queryset.model, "dept_belong_id", None):
            return queryset

        # 2. If user has no associated roles, return current dept data
        if not hasattr(request.user, "role"):
            return queryset.filter(dept_belong_id=user_dept_id)

        # 3. Get all permission ranges from all roles
        # (0, "Self data only"),
        # (1, "Current dept and subordinate"),
        # (2, "Current dept only"),
        # (3, "All data"),
        # (4, "Custom data")
        re_api = api
        _pk = request.parser_context["kwargs"].get('pk')
        if _pk: # Check if it's a singleton query
            re_api = re.sub(_pk,'{id}', api)
        role_id_list = request.user.role.values_list('id', flat=True)
        role_permission_list=RoleMenuButtonPermission.objects.filter(
            role__in=role_id_list,
            role__status=1,
            menu_button__api=re_api,
            menu_button__method=method).values(
            'data_range'
        )
        dataScope_list = []  # Permission range list
        for ele in role_permission_list:
                # Check if user is super admin role / if has [all data permission] return all data
            if ele.get("data_range") == 3:
                return queryset
            dataScope_list.append(ele.get("data_range"))
        dataScope_list = list(set(dataScope_list))

        # 4. When only self-data permission is set, only return filtered self-data, and dept is current user's own dept (considering dept changes, only see current user's dept data)
        if 0 in dataScope_list:
            return queryset.filter(
                creator=request.user, dept_belong_id=user_dept_id
            )

        # 5. Custom data permission - get depts, filter by dept
        dept_list = []
        for ele in dataScope_list:
            if ele == 1:
                dept_list.append(user_dept_id)
                dept_list.extend(
                    get_dept(
                        user_dept_id,
                    )
                )
            elif ele == 2:
                dept_list.append(user_dept_id)
            elif ele == 4:
                dept_ids = RoleMenuButtonPermission.objects.filter(
                    role__in=role_id_list,
                    role__status=1,
                    data_range=4).values_list(
                    'dept__id',flat=True
                )
                dept_list.extend(
                    dept_ids
                )
        if queryset.model._meta.model_name == 'dept':
            return queryset.filter(id__in=list(set(dept_list)))
        return queryset.filter(dept_belong_id__in=list(set(dept_list)))


class CustomDjangoFilterBackend(DjangoFilterBackend):
    lookup_prefixes = {
        "^": "istartswith",
        "=": "iexact",
        "@": "search",
        "$": "iregex",
        "~": "icontains",
    }
    filter_fields = "__all__"

    def construct_search(self, field_name, lookup_expr=None):
        lookup = self.lookup_prefixes.get(field_name[0])
        if lookup:
            field_name = field_name[1:]
        else:
            lookup = lookup_expr
        if lookup:
            if field_name.endswith(lookup):
                return field_name
            return LOOKUP_SEP.join([field_name, lookup])
        return field_name

    def find_filter_lookups(self, orm_lookups, search_term_key):
        for lookup in orm_lookups:
            # if lookup.find(search_term_key) >= 0:
            new_lookup = LOOKUP_SEP.join(lookup.split(LOOKUP_SEP)[:-1]) if len(lookup.split(LOOKUP_SEP)) > 1 else lookup
            # Fix conditional search error bug
            if new_lookup == search_term_key:
                return lookup
        return None

    def get_filterset_class(self, view, queryset=None):
        """
        Return the `FilterSet` class used to filter the queryset.
        """
        filterset_class = getattr(view, "filterset_class", None)
        filterset_fields = getattr(view, "filterset_fields", None)

        # TODO: remove assertion in 2.1
        if filterset_class is None and hasattr(view, "filter_class"):
            utils.deprecate(
                "`%s.filter_class` attribute should be renamed `filterset_class`." % view.__class__.__name__
            )
            filterset_class = getattr(view, "filter_class", None)

        # TODO: remove assertion in 2.1
        if filterset_fields is None and hasattr(view, "filter_fields"):
            utils.deprecate(
                "`%s.filter_fields` attribute should be renamed `filterset_fields`." % view.__class__.__name__
            )
            self.filter_fields = getattr(view, "filter_fields", None)
            if isinstance(self.filter_fields, (list, tuple)):
                filterset_fields = [
                    field[1:] if field[0] in self.lookup_prefixes.keys() else field for field in self.filter_fields
                ]
            else:
                filterset_fields = self.filter_fields

        if filterset_class:
            filterset_model = filterset_class._meta.model

            # FilterSets do not need to specify a Meta class
            if filterset_model and queryset is not None:
                assert issubclass(
                    queryset.model, filterset_model
                ), "FilterSet model %s does not match queryset model %s" % (
                    filterset_model,
                    queryset.model,
                )

            return filterset_class

        if filterset_fields and queryset is not None:
            MetaBase = getattr(self.filterset_base, "Meta", object)

            class AutoFilterSet(self.filterset_base):
                @classmethod
                def get_all_model_fields(cls, model):
                    opts = model._meta

                    return [
                        f.name
                        for f in sorted(opts.fields + opts.many_to_many)
                        if (f.name == "id")
                        or not isinstance(f, models.AutoField)
                        and not (getattr(f.remote_field, "parent_link", False))
                    ]

                @classmethod
                def get_fields(cls):
                    """
                    Resolve the 'fields' argument that should be used for generating filters on the
                    filterset. This is 'Meta.fields' sans the fields in 'Meta.exclude'.
                    """
                    model = cls._meta.model
                    fields = cls._meta.fields
                    exclude = cls._meta.exclude

                    assert not (fields is None and exclude is None), (
                        "Setting 'Meta.model' without either 'Meta.fields' or 'Meta.exclude' "
                        "has been deprecated since 0.15.0 and is now disallowed. Add an explicit "
                        "'Meta.fields' or 'Meta.exclude' to the %s class." % cls.__name__
                    )

                    # Setting exclude with no fields implies all other fields.
                    if exclude is not None and fields is None:
                        fields = ALL_FIELDS

                    # Resolve ALL_FIELDS into all fields for the filterset's model.
                    if fields == ALL_FIELDS:
                        fields = cls.get_all_model_fields(model)

                    # Remove excluded fields
                    exclude = exclude or []
                    if not isinstance(fields, dict):
                        fields = [(f, [settings.DEFAULT_LOOKUP_EXPR]) for f in fields if f not in exclude]
                    else:
                        fields = [(f, lookups) for f, lookups in fields.items() if f not in exclude]

                    return OrderedDict(fields)

                @classmethod
                def get_filters(cls):
                    """
                    Get all filters for the filterset. This is the combination of declared and
                    generated filters.
                    """

                    # No model specified - skip filter generation
                    if not cls._meta.model:
                        return cls.declared_filters.copy()

                    # Determine the filters that should be included on the filterset.
                    filters = OrderedDict()
                    fields = cls.get_fields()
                    undefined = []

                    for field_name, lookups in fields.items():
                        field = get_model_field(cls._meta.model, field_name)
                        from django.db import models
                        from timezone_field import TimeZoneField

                        # Model class excluded from filtering
                        if isinstance(field, (models.JSONField, TimeZoneField)):
                            continue
                        # warn if the field doesn't exist.
                        if field is None:
                            undefined.append(field_name)
                        # Update default string search to fuzzy search
                        if (
                            isinstance(field, (models.CharField))
                            and filterset_fields == "__all__"
                            and lookups == ["exact"]
                        ):
                            lookups = ["icontains"]
                        for lookup_expr in lookups:
                            filter_name = cls.get_filter_name(field_name, lookup_expr)

                            # If the filter is explicitly declared on the class, skip generation
                            if filter_name in cls.declared_filters:
                                filters[filter_name] = cls.declared_filters[filter_name]
                                continue

                            if field is not None:
                                filters[filter_name] = cls.filter_for_field(field, field_name, lookup_expr)

                    # Allow Meta.fields to contain declared filters *only* when a list/tuple
                    if isinstance(cls._meta.fields, (list, tuple)):
                        undefined = [f for f in undefined if f not in cls.declared_filters]

                    if undefined:
                        raise TypeError(
                            "'Meta.fields' must not contain non-model field names: %s" % ", ".join(undefined)
                        )

                    # Add in declared filters. This is necessary since we don't enforce adding
                    # declared filters to the 'Meta.fields' option
                    filters.update(cls.declared_filters)
                    return filters

                class Meta(MetaBase):
                    model = queryset.model
                    fields = filterset_fields

            return AutoFilterSet

        return None

    def filter_queryset(self, request, queryset, view):
        filterset = self.get_filterset(request, queryset, view)
        if filterset is None:
            return queryset
        if filterset.__class__.__name__ == "AutoFilterSet":
            queryset = filterset.queryset
            filter_fields = filterset.filters if self.filter_fields == "__all__" else self.filter_fields
            orm_lookup_dict = dict(
                zip(
                    [field for field in filter_fields],
                    [filterset.filters[lookup].lookup_expr for lookup in filterset.filters.keys()],
                )
            )
            orm_lookups = [
                self.construct_search(lookup, lookup_expr) for lookup, lookup_expr in orm_lookup_dict.items()
            ]
            # print(orm_lookups)
            conditions = []
            queries = []
            for search_term_key in filterset.data.keys():
                orm_lookup = self.find_filter_lookups(orm_lookups, search_term_key)
                if not orm_lookup or filterset.data.get(search_term_key) == '':
                    continue
                filterset_data_len = len(filterset.data.getlist(search_term_key))
                if filterset_data_len == 1:
                    query = Q(**{orm_lookup: filterset.data[search_term_key]})
                    queries.append(query)
                elif filterset_data_len == 2:
                    orm_lookup += '__range'
                    query = Q(**{orm_lookup: filterset.data.getlist(search_term_key)})
                    queries.append(query)
            if len(queries) > 0:
                conditions.append(reduce(operator.and_, queries))
                queryset = queryset.filter(reduce(operator.and_, conditions))
                return queryset
            else:
                return queryset

        if not filterset.is_valid() and self.raise_exception:
            raise utils.translate_validation(filterset.errors)
        return filterset.qs