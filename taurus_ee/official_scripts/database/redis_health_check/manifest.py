"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'Redis Health Check',
    'script_type': 'Shell',
    'category_name': 'Database Operations',
    'tags': '数据库,Redis,巡检',
    'desc': '检查Redis运行状态、内存使用、连接数、持久化等关键指标。',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+',
    'risk_level': 'low',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'changelog': 'v1.0.0 initial version',
    'timeout': 60,
    'script_params': [   {'key': 'redis_host', 'value': '127.0.0.1', 'desc': 'Redis host', 'type': 'string'},
                         {'key': 'redis_port', 'value': '6379', 'desc': 'Redis port', 'type': 'number'},
                         {'key': 'redis_password', 'value': '', 'desc': 'Redis password', 'type': 'string'}],
    'edition_scope': 'enterprise'}
