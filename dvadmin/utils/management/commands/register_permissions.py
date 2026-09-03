"""
Permission registration management command

Used to auto-register decorator-marked permissions to database
"""

from django.core.management.base import BaseCommand
from django.apps import apps
from dvadmin.utils.permission_decorator import get_registered_permissions


class Command(BaseCommand):
    help = '注册所有通过装饰器标记的权限到数据库'

    def add_arguments(self, parser):
        parser.add_argument(
            '--app',
            type=str,
            help='指定应用名称，不指定则注册所有应用的权限',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='仅显示将要注册的权限，不实际写入数据库',
        )

    def handle(self, *args, **options):
        app_name = options.get('app')
        dry_run = options.get('dry_run')
        
        self.stdout.write(self.style.NOTICE('开始扫描权限装饰器...'))
        
        # Scan all views
        self.scan_app_permissions(app_name)
        
        # Fetch permissions from registry table
        permissions = get_registered_permissions()
        
        if not permissions:
            self.stdout.write(self.style.WARNING('未找到任何权限装饰器'))
            return
        
        self.stdout.write(self.style.SUCCESS(f'\n找到 {len(permissions)} 个权限:'))
        
        # Display permission list
        for i, perm in enumerate(permissions, 1):
            method_name = perm['method_name']
            self.stdout.write(
                f"{i}. [{method_name:6s}] {perm['value']:40s} - {perm['name']}\n"
                f"         API: {perm['api'] or '自动生成'}\n"
                f"         模块: {perm['module']}.{perm['func_name']}"
            )
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\n[DRY RUN] 未实际写入数据库'))
            return
        
        # Register to database
        self.register_to_database(permissions)
        
        self.stdout.write(self.style.SUCCESS('\n权限注册完成!'))

    def scan_app_permissions(self, app_name=None):
        """Scan view permissions in apps"""
        import inspect
        
        if app_name:
            app_configs = [apps.get_app_config(app_name)]
        else:
            app_configs = apps.get_app_configs()
        
        for app_config in app_configs:
            try:
                # AttemptImport views Modules
                views_module = __import__(f'{app_config.name}.views', fromlist=[''])
                
                # Iterate through all classes
                for name, obj in inspect.getmembers(views_module):
                    if inspect.isclass(obj):
                        # Check all methods in class
                        for method_name, method in inspect.getmembers(obj, predicate=inspect.isfunction):
                            if hasattr(method, '_permissions'):
                                self.stdout.write(
                                    f'  发现权限: {obj.__name__}.{method_name}'
                                )
            except ImportError:
                continue

    def register_to_database(self, permissions):
        """Register permissions to database"""
        from dvadmin.system.models import MenuButton
        
        created_count = 0
        updated_count = 0
        
        for perm in permissions:
            api = perm['api']
            if not api:
                # TODO: Auto-generate from URL config
                self.stdout.write(
                    self.style.WARNING(f"  跳过 {perm['value']}: 未指定 API 路径")
                )
                continue
            
            # Check whether already exists
            existing = MenuButton.objects.filter(value=perm['value']).first()
            
            if existing:
                # Update existing permission
                existing.name = perm['name']
                existing.api = api
                existing.method = perm['method']
                if perm['menu_id']:
                    existing.menu_id = perm['menu_id']
                existing.save()
                updated_count += 1
                self.stdout.write(
                    self.style.WARNING(f"  更新: {perm['value']}")
                )
            else:
                # create新Permission
                MenuButton.objects.create(
                    name=perm['name'],
                    value=perm['value'],
                    api=api,
                    method=perm['method'],
                    menu_id=perm['menu_id'] if perm['menu_id'] else None,
                )
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f"  创建: {perm['value']}")
                )
        
        self.stdout.write(
            f'\n统计: 创建 {created_count} 个, 更新 {updated_count} 个'
        )
