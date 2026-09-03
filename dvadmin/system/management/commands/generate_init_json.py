"""
Generate init JSON files from existing database data.

Usage:
  python manage.py generate_init_json                       # Generate all
  python manage.py generate_init_json users                  # Generate one model
  python manage.py generate_init_json users role dept        # Generate multiple
"""
import json
import os

import django
from django.db.models import QuerySet

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')
django.setup()
from django.core.management.base import BaseCommand

from application.settings import BASE_DIR
from dvadmin.system.models import Menu, Users, Dept, Role, ApiWhiteList, Dictionary, SystemConfig
from dvadmin.system.fixtures.initSerializer import UsersInitSerializer, DeptInitSerializer, RoleInitSerializer, \
    MenuInitSerializer, ApiWhiteListInitSerializer, DictionaryInitSerializer, SystemConfigInitSerializer, \
    RoleMenuInitSerializer, RoleMenuButtonInitSerializer


class Command(BaseCommand):
    help = 'Generate init JSON files from current database data'

    def serializer_data(self, serializer, query_set: QuerySet):
        serializer = serializer(query_set, many=True)
        data = json.loads(json.dumps(serializer.data, ensure_ascii=False))
        filename = f'init_{query_set.model._meta.model_name}.json'
        filepath = os.path.join(BASE_DIR, 'dvadmin', 'system', 'fixtures', filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        self.stdout.write(self.style.SUCCESS(f'  ✓ Exported {len(data)} records → {filepath}'))

    def add_arguments(self, parser):
        parser.add_argument(
            "generate_name",
            nargs="*",
            type=str,
            help="Model names to generate (users, role, dept, menu, api_white_list, dictionary, system_config)",
        )

    def generate_users(self):
        self.serializer_data(UsersInitSerializer, Users.objects.all())

    def generate_role(self):
        self.serializer_data(RoleInitSerializer, Role.objects.all())

    def generate_dept(self):
        self.serializer_data(DeptInitSerializer, Dept.objects.filter(parent_id__isnull=True))

    def generate_menu(self):
        self.serializer_data(MenuInitSerializer, Menu.objects.filter(parent_id__isnull=True))

    def generate_api_white_list(self):
        self.serializer_data(ApiWhiteListInitSerializer, ApiWhiteList.objects.all())

    def generate_dictionary(self):
        self.serializer_data(DictionaryInitSerializer, Dictionary.objects.filter(parent_id__isnull=True))

    def generate_system_config(self):
        self.serializer_data(SystemConfigInitSerializer, SystemConfig.objects.filter(parent_id__isnull=True))

    def handle(self, *args, **options):
        generate_name = options.get('generate_name')
        generate_name_dict = {
            "users": self.generate_users,
            "role": self.generate_role,
            "dept": self.generate_dept,
            "menu": self.generate_menu,
            "api_white_list": self.generate_api_white_list,
            "dictionary": self.generate_dictionary,
            "system_config": self.generate_system_config,
        }
        if not generate_name:
            self.stdout.write(self.style.NOTICE('Exporting all init JSON files...'))
            for ele in generate_name_dict.keys():
                generate_name_dict[ele]()
            self.stdout.write(self.style.SUCCESS('✓ All init JSON files exported'))
            return

        for generate_name in generate_name:
            if generate_name not in generate_name_dict:
                self.stdout.write(self.style.ERROR(
                    f'Unknown init target: {generate_name}. Available: {list(generate_name_dict.keys())}'
                ))
                raise SystemExit(1)
            self.stdout.write(self.style.NOTICE(f'Exporting {generate_name}...'))
            generate_name_dict[generate_name]()
