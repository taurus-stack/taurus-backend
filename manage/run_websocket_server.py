#!/usr/bin/env python
"""
independent的 WebSocket Serviceserver(纯 asyncio, 不depend on Twisted/Daphne)

用法:
  # 直接start
  python manage/run_websocket_server.py
  
  # 指定Port
  python manage/run_websocket_server.py --port 8765
  
  # 后台run
  nohup python manage/run_websocket_server.py &
"""

import os
import sys

# 添加项目rootdirectory到 Python path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

# setting Djangoenvironment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')

import django
django.setup()

import asyncio
import logging
from django.conf import settings

# configLog
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger('websocket-server')


def main():
    """主function"""
    from taurus.websocket_async import start_websocket_server
    
    # 从 Django configread
    host = getattr(settings, 'WEBSOCKET_HOST', '0.0.0.0')
    port = getattr(settings, 'WEBSOCKET_PORT', 8765)
    
    # Command行Parameters覆盖
    if '--host' in sys.argv:
        host_idx = sys.argv.index('--host') + 1
        if host_idx < len(sys.argv):
            host = sys.argv[host_idx]
    
    if '--port' in sys.argv:
        port_idx = sys.argv.index('--port') + 1
        if port_idx < len(sys.argv):
            port = int(sys.argv[port_idx])
    
    logger.info("=" * 60)
    logger.info("  Taurus WebSocket 服务器")
    logger.info("=" * 60)
    logger.info(f"监听地址: ws://{host}:{port}")
    logger.info(f"协议: 纯 asyncio (websockets 库)")
    logger.info(f"依赖: 无 Twisted/Daphne")
    logger.info("=" * 60)
    
    try:
        asyncio.run(start_websocket_server(host, port))
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在关闭...")
    except Exception as e:
        logger.error(f"服务器运行异常: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()