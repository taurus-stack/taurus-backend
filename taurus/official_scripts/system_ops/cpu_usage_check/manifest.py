"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'CPU Usage Check',
    'script_type': 'Shell',
    'category_name': 'System Operations',
    'tags': 'check,cpu,monitoring',
    'desc': 'Check system CPU usage including user, system, iowait metrics. Alerts when exceeding threshold.',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+',
    'risk_level': 'low',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'changelog': 'v1.0.0 initial version',
    'timeout': 60,
    'script_params': [   {   'key': 'threshold',
                             'value': '90',
                             'desc': 'CPU usage alert threshold, default 90',
                             'type': 'number'}],
    'edition_scope': 'community'}
