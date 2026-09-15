"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'Crontab Task List',
    'script_type': 'Shell',
    'category_name': 'System Operations',
    'tags': '定时任务,crontab,审计',
    'desc': '列出系统所有用户的crontab任务，包括系统级和用户级定时任务，便于审计。',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+',
    'risk_level': 'low',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'changelog': 'v1.0.0 initial version',
    'timeout': 30,
    'script_params': [],
    'edition_scope': 'enterprise'}
