"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'Docker Resource Usage Stats',
    'script_type': 'Shell',
    'category_name': 'Container & Cloud',
    'tags': '容器,Docker,监控',
    'desc': '统计Docker容器的CPU、内存、磁盘等资源使用情况，列出资源占用Top的容器。',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+',
    'risk_level': 'low',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'changelog': 'v1.0.0 initial version',
    'timeout': 60,
    'script_params': [],
    'edition_scope': 'enterprise'}
