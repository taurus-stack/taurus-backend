"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'SSL Certificate Expiry Check',
    'script_type': 'Shell',
    'category_name': 'Monitoring & Alerts',
    'tags': '监控,SSL,证书',
    'desc': '检测指定域名的SSL证书到期时间，支持批量检测多个域名，提前预警。',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+',
    'risk_level': 'low',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'changelog': 'v1.0.0 initial version',
    'timeout': 120,
    'script_params': [   {   'key': 'domains',
                             'value': 'www.example.com',
                             'desc': '域名列表，英文逗号分隔',
                             'type': 'string'},
                         {'key': 'warn_days', 'value': '30', 'desc': '剩余天数小于该值告警', 'type': 'number'}],
    'edition_scope': 'enterprise'}
