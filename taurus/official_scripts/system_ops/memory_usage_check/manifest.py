"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'Memory Usage Check',
    'script_type': 'Shell',
    'category_name': 'System Operations',
    'tags': 'check,memory,monitoring',
    'desc': '检查系统内存使用率，包括物理内存和交换分区，超过阈值告警并列出占用内存最多的进程。',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+',
    'risk_level': 'low',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'changelog': 'v1.0.0 initial version',
    'timeout': 60,
    'script_params': [   {   'key': 'threshold',
                             'value': '90',
                             'desc': 'Memory usage alert threshold, default 90',
                             'type': 'number'}],
    'edition_scope': 'community'}
