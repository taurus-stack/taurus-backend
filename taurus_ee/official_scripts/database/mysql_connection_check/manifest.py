"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'MySQL Connection Count Check',
    'script_type': 'Shell',
    'category_name': 'Database Operations',
    'tags': '数据库,MySQL,巡检',
    'desc': '检查MySQL数据库连接数、慢查询、主从同步状态等关键指标。',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+',
    'risk_level': 'low',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'changelog': 'v1.0.0 initial version',
    'timeout': 60,
    'script_params': [   {'key': 'mysql_host', 'value': '127.0.0.1', 'desc': 'MySQL host', 'type': 'string'},
                         {'key': 'mysql_port', 'value': '3306', 'desc': 'MySQL port', 'type': 'number'},
                         {'key': 'mysql_user', 'value': 'root', 'desc': 'MySQL username', 'type': 'string'},
                         {'key': 'mysql_password', 'value': '', 'desc': 'MySQL password', 'type': 'string'}],
    'edition_scope': 'enterprise'}
