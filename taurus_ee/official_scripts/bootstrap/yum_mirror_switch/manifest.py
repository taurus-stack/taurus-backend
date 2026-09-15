"""Script metadata. Sync official_version after modifying fields."""

MANIFEST = {   'name': 'YUM Mirror Switch to China',
    'script_type': 'Shell',
    'category_name': 'Environment Bootstrap',
    'tags': '初始化,源配置,YUM',
    'desc': '一键切换CentOS/RHEL系统的YUM源为国内镜像源，支持阿里云、清华、华为等镜像站。',
    'supported_systems': 'CentOS 7+,RHEL 7+',
    'risk_level': 'medium',
    'official_version': 'v1.0.0',
    'license_type': 'MIT',
    'source_url': 'https://github.com/SuperManito/LinuxMirrors',
    'changelog': 'v1.0.0 initial version',
    'timeout': 120,
    'script_params': [   {   'key': 'mirror',
                             'value': 'aliyun',
                             'desc': '镜像站: aliyun/tsinghua/huawei',
                             'type': 'string'}],
    'edition_scope': 'enterprise'}
