"""
ASGI config for application project.

Removed Channels/Twisted dependency, switched to standard WSGI.
WebSocket functionality handled by independent asyncio server (see taurus/websocket_async.py).
"""

import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')

# HTTP support only, WebSocket handled by independent server
application = get_asgi_application()