"""
Start pure asyncio WebSocket server (does not depend on Twisted/Daphne)

Usage:
  python manage.py run_websocket_server
  python manage.py run_websocket_server --port 8765
"""

import asyncio
import logging
from django.core.management.base import BaseCommand
from django.conf import settings

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Start pure asyncio WebSocket server (does not depend on Twisted/Daphne)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--host',
            default=None,
            help='Listen address (default: 0.0.0.0)',
        )
        parser.add_argument(
            '--port',
            type=int,
            default=None,
            help='Listen port (default: 8765)',
        )

    def handle(self, *args, **options):
        from taurus.websocket_async import start_websocket_server
        
        host = options['host'] or getattr(settings, 'WEBSOCKET_HOST', '0.0.0.0')
        port = options['port'] or getattr(settings, 'WEBSOCKET_PORT', 8765)
        
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS('  Taurus WebSocket Server'))
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS(f'Listen address: ws://{host}:{port}'))
        self.stdout.write(self.style.SUCCESS('Protocol: pure asyncio (websockets library)'))
        self.stdout.write(self.style.SUCCESS('Dependencies: no Twisted/Daphne'))
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('Press Ctrl+C to stop the server'))
        self.stdout.write('')
        
        try:
            asyncio.run(start_websocket_server(host, port))
        except KeyboardInterrupt:
            self.stdout.write(self.style.SUCCESS('\nServer stopped'))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'Server runtime exception: {e}'))