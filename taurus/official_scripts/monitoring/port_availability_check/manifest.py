"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'Port Availability Check',
    'script_type': 'Shell',
    'category_name': 'Monitoring & Alerts',
    'tags': '监控,端口,网络',
    'desc': '检测指定主机端口是否可连通，支持批量检测多个端口，用于服务可用性监控。',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+',
    'risk_level': 'low',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'changelog': 'v1.0.0 initial version',
    'timeout': 60,
    'script_params': [   {'key': 'host', 'value': '127.0.0.1', 'desc': '目标主机地址', 'type': 'string'},
                         {'key': 'ports', 'value': '22,80,443', 'desc': '端口列表，英文逗号分隔', 'type': 'string'},
                         {'key': 'timeout', 'value': '3', 'desc': '连接超时时间(秒)', 'type': 'number'}],
    'edition_scope': 'community'}
