# Initialize base class
import json
import os

from django.apps import apps
from rest_framework import request

from application import settings
from dvadmin.system.models import Users


class CoreInitialize:
    """
    Base class for data initialization.
    Subclass it, override run(), and call init_base() or save() in run().
    """
    creator_id = None
    reset = False
    request = request
    file_path = None

    def __init__(self, reset=False, creator_id=None, app=None):
        """
        reset: If True, delete existing records before creating
        creator_id: Default creator user ID for created records
        app: App label (used to locate fixtures/)
        """
        self.reset = reset or self.reset
        self.creator_id = creator_id or self.creator_id
        self.app = app or ''
        self.request.user = Users.objects.order_by('create_datetime').first()

    def init_base(self, Serializer, unique_fields=None):
        """Initialize model records from a JSON fixture file using a Serializer."""
        model = Serializer.Meta.model
        app_name = self.app.split('.')[-1]
        path_file = os.path.join(
            apps.get_app_config(app_name).path, 'fixtures',
            f'init_{model._meta.model_name}.json',
        )
        if not os.path.isfile(path_file):
            print(f"[{app_name}][{model._meta.model_name}] fixture not found, skipping")
            return
        print(f"[{app_name}][{model._meta.model_name}] loading from {path_file}...")
        with open(path_file, encoding="utf-8") as f:
            for data in json.load(f):
                filter_data = {}
                if unique_fields:
                    for field in unique_fields:
                        if field in data:
                            filter_data[field] = data[field]
                else:
                    for key, value in data.items():
                        if isinstance(value, list) or value is None or value == '':
                            continue
                        filter_data[key] = value
                instance = model.objects.filter(**filter_data).first()
                data["reset"] = self.reset
                serializer = Serializer(instance, data=data, request=self.request)
                serializer.is_valid(raise_exception=True)
                serializer.save()
        print(f"[{app_name}][{model._meta.model_name}] initialization complete")

    def save(self, obj, data: list, name=None, no_reset=False):
        """Initialize model records from a raw data list (alternative to init_base)."""
        name = name or obj._meta.verbose_name
        print(f"Initializing [{obj._meta.label} => {name}]")
        if not no_reset and self.reset and obj not in settings.INITIALIZE_RESET_LIST:
            try:
                obj.objects.all().delete()
                settings.INITIALIZE_RESET_LIST.append(obj)
            except Exception:
                pass
        for ele in data:
            m2m_dict = {}
            new_data = {}
            for key, value in ele.items():
                if isinstance(value, list) and value and isinstance(value[0], int):
                    m2m_dict[key] = value
                else:
                    new_data[key] = value
            instance, _ = obj.objects.get_or_create(id=ele.get("id"), defaults=new_data)
            for key, m2m in m2m_dict.items():
                m2m = list(set(m2m))
                if m2m and len(m2m) > 0 and m2m[0]:
                    exec(f"""
if instance.{key}:
    values_list = instance.{key}.all().values_list('id', flat=True)
    values_list = list(set(list(values_list) + {m2m}))
    instance.{key}.set(values_list)
""")
        print(f"Initialization complete [{obj._meta.label} => {name}]")

    def run(self):
        raise NotImplementedError('.run() must be overridden')
