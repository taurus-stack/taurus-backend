"""taurus_ee — Taurus Ops 扩展功能 Django App（已并入开源版本）。

单仓库 + 单 Schema 原则：
  • 所有 Django Model 仍定义在 `taurus.models`（taurus_ee.migrations 始终为空）；
  • 本 App 承载审批流、DAG 扩展服务、分享、安全检查、集中日志、调度 HA 服务、
    官方脚本包与 License 授权等模块的 Serializer / ViewSet / Service /
    Management Command 实现；
  • 自全功能开源版本起，本 App 随仓库常驻启用，不再做功能门禁；
    商业区分仅通过 License 控制主机配额与服务等级（见 taurus_ee.license）。

导入方向（单向）：
    taurus_ee  →  taurus   ✅ 合法
    taurus     →  taurus_ee ❌ 禁止，如必须调用本 App 服务使用 taurus.ee_registry
"""

default_app_config = "taurus_ee.apps.TaurusEEConfig"
