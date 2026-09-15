"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'Service Status Query',
    'script_type': 'Shell',
    'category_name': 'Application Release',
    'tags': '服务,状态,systemd',
    'desc': '查询systemd管理的服务运行状态，支持批量查询多个服务。',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+',
    'risk_level': 'low',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'changelog': 'v1.0.0 initial version',
    'timeout': 60,
    'script_params': [{'key': 'services', 'value': 'nginx,sshd', 'desc': '服务名称列表，英文逗号分隔', 'type': 'string'}],
    'edition_scope': 'enterprise'}
