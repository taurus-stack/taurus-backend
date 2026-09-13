"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'Service Restart',
    'script_type': 'Shell',
    'category_name': 'Application Release',
    'tags': '服务,重启,systemd',
    'desc': '安全重启systemd服务，重启前检查状态，重启后验证服务可用性。',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+',
    'risk_level': 'medium',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'changelog': 'v1.0.0 initial version',
    'timeout': 120,
    'script_params': [   {'key': 'service_name', 'value': 'nginx', 'desc': '服务名称', 'type': 'string'},
                         {'key': 'check_port', 'value': '', 'desc': '重启后检测的端口(可选)', 'type': 'string'},
                         {'key': 'wait_seconds', 'value': '10', 'desc': '重启后等待秒数', 'type': 'number'}],
    'edition_scope': 'enterprise'}
