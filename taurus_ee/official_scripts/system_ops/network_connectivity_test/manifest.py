"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'Network Connectivity Test',
    'script_type': 'Shell',
    'category_name': 'System Operations',
    'tags': 'network,connectivity,test',
    'desc': '测试指定目标的网络连通性，包括ping延迟、路由追踪、端口检测等综合诊断。',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+',
    'risk_level': 'low',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'changelog': 'v1.0.0 initial version',
    'timeout': 120,
    'script_params': [   {'key': 'target', 'value': 'www.baidu.com', 'desc': '测试目标地址', 'type': 'string'},
                         {'key': 'port', 'value': '80', 'desc': '测试端口', 'type': 'number'}],
    'edition_scope': 'enterprise'}
