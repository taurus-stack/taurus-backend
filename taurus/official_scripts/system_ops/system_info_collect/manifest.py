"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'System Info Collection',
    'script_type': 'Shell',
    'category_name': 'System Operations',
    'tags': '巡检,系统信息,资产',
    'desc': '采集服务器基础信息，包括操作系统版本、内核版本、CPU、内存、磁盘、网络等，用于资产盘点。',
    'supported_systems': 'CentOS 7+,Ubuntu 18.04+,Debian 10+',
    'risk_level': 'low',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'changelog': 'v1.0.0 initial version',
    'timeout': 120,
    'script_params': [],
    'edition_scope': 'community'}
