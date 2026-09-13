"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'User Login Log Analysis',
    'script_type': 'Shell',
    'category_name': 'Security Hardening',
    'tags': '安全,登录,审计',
    'desc': '分析系统用户登录日志，统计登录成功/失败次数，列出异常登录IP和时间。',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+',
    'risk_level': 'low',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'changelog': 'v1.0.0 initial version',
    'timeout': 60,
    'script_params': [   {'key': 'days', 'value': '7', 'desc': '分析最近多少天的日志', 'type': 'number'},
                         {'key': 'top_n', 'value': '10', 'desc': '显示Top N IP', 'type': 'number'}],
    'edition_scope': 'enterprise'}
