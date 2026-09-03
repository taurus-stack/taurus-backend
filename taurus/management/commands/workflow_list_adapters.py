"""List all registered workflow adapters (ExecutableUnit).

Usage:
  python manage.py workflow_list_adapters
  python manage.py workflow_list_adapters --json
"""

import json

from django.core.management.base import BaseCommand

from taurus.workflow.engine.registry import get_registry


class Command(BaseCommand):
    help = 'List all registered workflow adapters (ExecutableUnit)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--json',
            action='store_true',
            default=False,
            help='Output in JSON format (for frontend manifest consumption)',
        )

    def handle(self, *args, **options):
        registry = get_registry()
        manifest = registry.manifest_all()

        if options['json']:
            self.stdout.write(json.dumps(manifest, ensure_ascii=False, indent=2))
            return

        if not manifest:
            self.stdout.write(self.style.WARNING('No registered adapters currently.'))
            return

        self.stdout.write(self.style.SUCCESS(f'Registered adapters (total {len(manifest)}):'))
        self.stdout.write('')
        header = f'  {"node_type":<20} {"display_name":<16} {"requires_host":<14} {"async_human"}'
        self.stdout.write(header)
        self.stdout.write(f'  {"-" * 20} {"-" * 16} {"-" * 14} {"-" * 11}')
        for item in manifest:
            self.stdout.write(
                f'  {item["node_type"]:<20} {item["display_name"]:<16} '
                f'{str(item["requires_host"]):<14} {str(item["is_asynchronous_human"])}'
            )
        self.stdout.write('')