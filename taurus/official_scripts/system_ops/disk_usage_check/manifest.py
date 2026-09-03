"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'Disk Usage Check',
    'script_type': 'Shell',
    'category_name': 'System Operations',
    'tags': 'check,disk,monitoring',
    'desc': '检查系统各挂载点磁盘使用率，超过阈值则告警。支持自定义阈值，自动排除临时文件系统。',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+,Debian 10+',
    'risk_level': 'low',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'source_url': 'https://github.com/HariSekhon/DevOps-Bash-tools',
    'changelog': 'v1.0.0 initial version',
    'timeout': 60,
    'script_params': [{'key': 'threshold', 'value': '85', 'desc': '告警阈值百分比，默认85', 'type': 'number'}],
    'edition_scope': 'community'}
