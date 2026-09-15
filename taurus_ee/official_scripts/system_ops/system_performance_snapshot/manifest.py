"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'System Performance Snapshot',
    'script_type': 'Shell',
    'category_name': 'System Operations',
    'tags': '性能,排查,诊断',
    'desc': '快速采集系统性能数据快照，包括CPU、内存、磁盘IO、网络、进程等，用于问题排查。',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+',
    'risk_level': 'low',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'changelog': 'v1.0.0 initial version',
    'timeout': 60,
    'script_params': [],
    'edition_scope': 'enterprise'}
