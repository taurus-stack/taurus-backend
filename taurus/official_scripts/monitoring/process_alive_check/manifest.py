"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'Process Liveness Check',
    'script_type': 'Shell',
    'category_name': 'Monitoring & Alerts',
    'tags': '监控,进程,服务',
    'desc': '检测指定名称的进程是否在运行，支持多个进程批量检测。',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+',
    'risk_level': 'low',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'changelog': 'v1.0.0 initial version',
    'timeout': 30,
    'script_params': [   {   'key': 'process_names',
                             'value': 'nginx,sshd',
                             'desc': '进程名称列表，英文逗号分隔',
                             'type': 'string'}],
    'edition_scope': 'community'}
