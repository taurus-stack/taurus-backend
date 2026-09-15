"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'Docker Disk Cleanup',
    'script_type': 'Shell',
    'category_name': 'Container & Cloud',
    'tags': '容器,Docker,清理',
    'desc': '清理Docker无用镜像、容器、卷和网络，释放磁盘空间。支持安全模式（仅清理未使用资源）。',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+',
    'risk_level': 'medium',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'changelog': 'v1.0.0 initial version',
    'timeout': 300,
    'script_params': [   {   'key': 'prune_all',
                             'value': 'false',
                             'desc': '是否清理所有未使用资源(true/false)',
                             'type': 'string'}],
    'edition_scope': 'enterprise'}
