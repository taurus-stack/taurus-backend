"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'System Security Baseline Check',
    'script_type': 'Shell',
    'category_name': 'Security Hardening',
    'tags': '安全,基线,巡检',
    'desc': '检查系统安全基线配置，包括密码策略、SSH配置、防火墙、SELinux、用户权限等。',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+',
    'risk_level': 'low',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'changelog': 'v1.0.0 initial version',
    'timeout': 120,
    'script_params': [],
    'edition_scope': 'enterprise'}
