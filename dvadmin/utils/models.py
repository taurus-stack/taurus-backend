# -*- coding: utf-8 -*-

"""
@author: Yuan Xiaotian
@contact: QQ:1638245306
@Created on: 2021/5/31 22:08
@Remark: Common base model class
"""
from importlib import import_module

from django.apps import apps
from django.db import models
from django.conf import settings

from application import settings

table_prefix = settings.TABLE_PREFIX  # Database table name prefix


class SoftDeleteQuerySet(models.QuerySet):
    pass


class SoftDeleteManager(models.Manager):
    """Support soft delete"""

    def __init__(self, *args, **kwargs):
        self.__add_is_del_filter = False
        super(SoftDeleteManager, self).__init__(*args, **kwargs)

    def filter(self, *args, **kwargs):
        # Consider whether is_deleted is explicitly passed
        if not kwargs.get('is_deleted') is None:
            self.__add_is_del_filter = True
        return super(SoftDeleteManager, self).filter(*args, **kwargs)

    def get_queryset(self):
        if self.__add_is_del_filter:
            return SoftDeleteQuerySet(self.model, using=self._db).exclude(is_deleted=False)
        return SoftDeleteQuerySet(self.model).exclude(is_deleted=True)

    def get_by_natural_key(self, name):
        return SoftDeleteQuerySet(self.model).get(username=name)


class SoftDeleteModel(models.Model):
    """
    Soft delete model
    Once inherited, soft delete will be enabled
    """
    is_deleted = models.BooleanField(verbose_name="Is soft deleted", help_text='Is soft deleted', default=False, db_index=True)
    objects = SoftDeleteManager()

    class Meta:
        abstract = True
        verbose_name = 'Soft delete model'
        verbose_name_plural = verbose_name

    def delete(self, using=None, soft_delete=True, *args, **kwargs):
        """
        Override delete method, enable soft delete directly
        """
        self.is_deleted = True
        self.save(using=using)


class CoreModel(models.Model):
    """
    Core standard abstract model, can be directly inherited and used
    Audit fields are added. When overriding fields, DO NOT modify field names; audit field names must be unified
    """
    id = models.BigAutoField(primary_key=True, help_text="Id", verbose_name="Id")
    description = models.CharField(max_length=255, verbose_name="Description", null=True, blank=True, help_text="Description")
    creator = models.ForeignKey(to=settings.AUTH_USER_MODEL, related_query_name='creator_query', null=True,
                                verbose_name='Creator', help_text="Creator", on_delete=models.SET_NULL,
                                db_constraint=False)
    modifier = models.CharField(max_length=255, null=True, blank=True, help_text="Modifier", verbose_name="Modifier")
    dept_belong_id = models.CharField(max_length=255, help_text="Data belonging dept", null=True, blank=True,
                                      verbose_name="Data belonging dept")
    update_datetime = models.DateTimeField(auto_now=True, null=True, blank=True, help_text="Update time",
                                           verbose_name="Update time")
    create_datetime = models.DateTimeField(auto_now_add=True, null=True, blank=True, help_text="Create time",
                                           verbose_name="Create time")

    class Meta:
        abstract = True
        verbose_name = 'Core model'
        verbose_name_plural = verbose_name


def get_all_models_objects(model_name=None):
    """
    Get all models objects
    :return: {}
    """
    settings.ALL_MODELS_OBJECTS = {}
    if not settings.ALL_MODELS_OBJECTS:
        all_models = apps.get_models()
        for item in list(all_models):
            table = {
                "tableName": item._meta.verbose_name,
                "table": item.__name__,
                "tableFields": []
            }
            for field in item._meta.fields:
                fields = {
                    "title": field.verbose_name,
                    "field": field.name
                }
                table['tableFields'].append(fields)
            settings.ALL_MODELS_OBJECTS.setdefault(item.__name__, {"table": table, "object": item})
    if model_name:
        return settings.ALL_MODELS_OBJECTS[model_name] or {}
    return settings.ALL_MODELS_OBJECTS or {}


def get_model_from_app(app_name):
    """Get fields in model"""
    model_module = import_module(app_name + '.models')
    filter_model = [
        getattr(model_module, item) for item in dir(model_module)
        if item != 'CoreModel' and issubclass(getattr(model_module, item).__class__, models.base.ModelBase)
    ]
    model_list = []
    for model in filter_model:
        if model.__name__ == 'AbstractUser':
            continue
        fields = [
            {'title': field.verbose_name, 'name': field.name, 'object': field}
            for field in model._meta.fields
        ]
        model_list.append({
            'app': app_name,
            'verbose': model._meta.verbose_name,
            'model': model.__name__,
            'object': model,
            'fields': fields
        })
    return model_list


def get_custom_app_models(app_name=None):
    """
    Get models from all apps under the project
    """
    if app_name:
        return get_model_from_app(app_name)
    all_apps = apps.get_app_configs()
    res = []
    for app in all_apps:
        if app.name.startswith('django'):
            continue
        if app.name in settings.COLUMN_EXCLUDE_APPS:
            continue
        try:
            all_models = get_model_from_app(app.name)
            if all_models:
                for model in all_models:
                    res.append(model)
        except Exception as e:
            pass
    return res