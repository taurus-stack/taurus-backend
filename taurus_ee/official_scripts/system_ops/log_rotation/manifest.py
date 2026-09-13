"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'System Log Rotation',
    'script_type': 'Shell',
    'category_name': 'System Operations',
    'tags': '清理,日志,logrotate',
    'desc': '手动触发系统日志轮转，用于紧急清理大日志文件场景。',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+',
    'risk_level': 'low',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'changelog': 'v1.0.0 initial version',
    'timeout': 120,
    'script_params': [{'key': 'force', 'value': 'false', 'desc': '是否强制执行轮转', 'type': 'string'}],
    'edition_scope': 'enterprise'}
