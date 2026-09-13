"""EE 通用工具（EditionGate 装饰器增强 / EE 服务便捷获取）。

⚠️  为避免 apps 启动时的 import 顺序问题，
    不要在此 __init__.py 顶层 import gate.py（它依赖 taurus.editions，
    在某些 Django 启动路径下会触发循环/延迟初始化问题）。

请使用显式路径：
    from taurus_ee.utils.gate import ee_viewset_action, ee_service_or_403
"""
