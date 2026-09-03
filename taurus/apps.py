from django.apps import AppConfig


class TaurusConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'taurus'

    def ready(self) -> None:
        """Django start完成hook:load INSTALLED_UNIT_ADAPTERS.

        每  import 都会Trigger被ImportModules顶at layer `@register_unit_adapter(...)` Decorator, 
        从而把AdapterRegister到global UnitAdapterRegistry 单例中.
        """
        # Note:不要在Modules顶层 import registry(may导致CircularReference), apps.ready 时 Django 已Import完毕.
        import importlib
        import logging

        from conf.env import INSTALLED_UNIT_ADAPTERS

        logger = logging.getLogger(__name__)
        loaded_count = 0
        for mod_path in INSTALLED_UNIT_ADAPTERS or []:
            try:
                importlib.import_module(mod_path)
                loaded_count += 1
            except Exception:
                # 严格模式:任何ConfigError的Adapter都让startFailed, Avoidline上静默不生效
                logger.exception("[WorkflowEngine] Failed to load adapter: %s", mod_path)
                raise

        from taurus.workflow.engine.registry import get_registry

        registry = get_registry()
        types = registry.list_all_adapter_types()
        logger.info(
            "[WorkflowEngine] Adapters loaded: loaded=%d registered=%d types=%s",
            loaded_count,
            len(types),
            types,
        )