"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'Nginx Config Check',
    'script_type': 'Shell',
    'category_name': 'Application Release',
    'tags': 'nginx,config,check',
    'desc': '检查Nginx配置文件语法是否正确，显示已加载的虚拟主机和监听端口。',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+',
    'risk_level': 'low',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'changelog': 'v1.0.0 initial version',
    'timeout': 30,
    'script_params': [],
    'edition_scope': 'enterprise'}
