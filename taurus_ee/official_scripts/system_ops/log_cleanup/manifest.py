"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'Log File Cleanup',
    'script_type': 'Shell',
    'category_name': 'System Operations',
    'tags': '清理,日志,磁盘',
    'desc': '清理指定目录下的过期日志文件，支持按保留天数和文件大小清理，可配置匹配模式。',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+',
    'risk_level': 'medium',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'changelog': 'v1.0.0 initial version',
    'timeout': 300,
    'script_params': [   {'key': 'log_dir', 'value': '/var/log', 'desc': '日志目录路径', 'type': 'string'},
                         {'key': 'keep_days', 'value': '30', 'desc': '保留天数', 'type': 'number'},
                         {'key': 'file_pattern', 'value': '*.log', 'desc': '文件匹配模式', 'type': 'string'}],
    'edition_scope': 'enterprise'}
