"""
Permission code registration command

Scan all view methods marked with @require_perm decorator,
register permission codes to database, and optionally auto-create roles.

Usage:
  python manage.py register_perms                              # Scan + register
  python manage.py register_perms --auto-create-roles           # + Create roles per module
  python manage.py register_perms --auto-create-roles --auto-assign  # + Assign perms
  python manage.py register_perms --dry-run                     # Scan only
"""

import importlib
import logging

from django.apps import apps
from django.core.management.base import BaseCommand

from dvadmin.system.models import Role
from dvadmin.system.permission_models import PermissionCode, RolePermission
from dvadmin.utils.permission_decorator import PermissionRegistry

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Scan and register all permission codes to database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--auto-create-roles',
            action='store_true',
            help='Auto-create roles based on module names',
        )
        parser.add_argument(
            '--auto-assign',
            action='store_true',
            help='Auto-assign permissions to module-based roles',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Scan only — do not write to database',
        )

    def handle(self, *args, **options):
        auto_create_roles = options['auto_create_roles']
        auto_assign = options['auto_assign']
        dry_run = options['dry_run']

        self.stdout.write(self.style.NOTICE('Scanning all apps for permission codes...'))

        self.scan_all_apps()
        entries = PermissionRegistry.all_entries()

        if not entries:
            self.stdout.write(self.style.WARNING('No permission codes found'))
            return

        self.stdout.write(self.style.SUCCESS(f'\nFound {len(entries)} permission codes:'))

        # Group by module for display
        modules = {}
        for code, entry in entries.items():
            module = entry.module or 'uncategorized'
            modules.setdefault(module, []).append(entry)

        for module, perms in sorted(modules.items()):
            self.stdout.write(f'\n[{module}]')
            for perm in perms:
                methods = ', '.join(sorted(perm.methods)) if perm.methods else 'ALL'
                roles_info = f" roles={perm.roles}" if perm.roles else ""
                self.stdout.write(f'  {perm.code:40s} {perm.name:20s} [{methods}]{roles_info}')

        if dry_run:
            self.stdout.write(self.style.WARNING('\n[DRY RUN] No database changes made'))
            return

        # Register codes to database
        self.stdout.write(self.style.NOTICE('\nRegistering permission codes to database...'))
        created_count = 0
        updated_count = 0

        for code, entry in entries.items():
            perm_obj, created = PermissionCode.objects.update_or_create(
                code=code,
                defaults={
                    'name': entry.name or code,
                    'module': entry.module or '',
                    'description': entry.description or '',
                    'status': True,
                }
            )
            if created:
                created_count += 1
                self.stdout.write(f'  ✓ Created: {code}')
            else:
                updated_count += 1
                self.stdout.write(f'  ↻ Updated: {code}')

        self.stdout.write(self.style.SUCCESS(
            f'\nPermission codes registered: {created_count} created, {updated_count} updated'
        ))

        # Auto-create roles (module-based)
        if auto_create_roles:
            self.stdout.write(self.style.NOTICE('\nCreating roles (module-based)...'))
            roles = {}
            for module in modules.keys():
                if module == 'uncategorized':
                    continue
                role, created = Role.objects.get_or_create(
                    key=f'role_{module}',
                    defaults={
                        'name': f'{module} Admin',
                        'status': True,
                    }
                )
                roles[module] = role
                if created:
                    self.stdout.write(f'  ✓ Created role: {role.name}')
                else:
                    self.stdout.write(f'  ↻ Role exists: {role.name}')

            # Auto-assign per module
            if auto_assign:
                self.stdout.write(self.style.NOTICE('\nAssigning permissions (module-based)...'))
                assign_count = 0
                for module, perms in modules.items():
                    if module == 'uncategorized' or module not in roles:
                        continue
                    role = roles[module]
                    for perm in perms:
                        perm_obj = PermissionCode.objects.get(code=perm.code)
                        _, created = RolePermission.objects.get_or_create(
                            role=role,
                            permission=perm_obj,
                        )
                        if created:
                            assign_count += 1
                            self.stdout.write(f'  ✓ {role.name} ← {perm.code}')
                self.stdout.write(self.style.SUCCESS(f'\nModule permission assignment: {assign_count} entries'))

        # Handle roles specified via @permission_code(roles=[...])
        self.stdout.write(self.style.NOTICE('\nProcessing roles from @permission_code decorator...'))
        role_assign_count = 0
        for code, entry in entries.items():
            if not entry.roles:
                continue

            perm_obj = PermissionCode.objects.get(code=code)
            for role_key in entry.roles:
                role, created = Role.objects.get_or_create(
                    key=role_key,
                    defaults={
                        'name': role_key.replace('_', ' ').title(),
                        'status': True,
                    }
                )
                if created:
                    self.stdout.write(f'  ✓ Created role: {role.name} (key={role_key})')

                _, created = RolePermission.objects.get_or_create(
                    role=role,
                    permission=perm_obj,
                )
                if created:
                    role_assign_count += 1
                    self.stdout.write(f'  ✓ {role.name} ← {code}')

        if role_assign_count > 0:
            self.stdout.write(self.style.SUCCESS(
                f'\nDecorator role assignment: {role_assign_count} entries'
            ))

        self.stdout.write(self.style.SUCCESS('\n✓ Done'))

    def scan_all_apps(self):
        """Scan all app view modules to trigger @permission_code decorator registration."""
        import pkgutil
        import sys

        for app_config in apps.get_app_configs():
            views_module_name = f'{app_config.name}.views'
            try:
                views_module = importlib.import_module(views_module_name)
                views_path = getattr(views_module, '__path__', None)
                if views_path:
                    for finder, sub_module_name, ispkg in pkgutil.iter_modules(views_path):
                        full_name = f'{views_module_name}.{sub_module_name}'
                        if full_name in sys.modules:
                            continue
                        try:
                            importlib.import_module(full_name)
                            logger.debug(f'Scanned: {full_name}')
                        except Exception as e:
                            logger.warning(f'Scan failed {full_name}: {e}')
                else:
                    logger.debug(f'Scanned: {views_module_name}')
            except ImportError as e:
                logger.debug(f'No views module: {app_config.name} -> {e}')

            try:
                importlib.import_module(f'{app_config.name}.viewsets')
                logger.debug(f'Scanned: {app_config.name}.viewsets')
            except ImportError:
                pass
