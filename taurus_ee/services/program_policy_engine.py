"""taurus_ee.services.program_policy_engine — M2.4 Supervisor program_policy_engine (EE 专属 Service).

功能（供 4 个 Thin Wrapper ViewSet 复用 / 避免 orphan 覆盖）：
  · apply_template_to_hosts(viewset, request, pk)
    → 从 ProgramInstallTemplate.apply_to_hosts 剥离，创建 binding + (optional) install config
  · host_binding_install(viewset, request, pk)
  · host_binding_uninstall(viewset, request, pk)
  · config_batch_create(viewset, request)
  · config_redispatch(viewset, request, pk)
  · policy_apply(viewset, request, pk)
  · policy_preview_hosts(viewset, request, pk)
  · policy_upgrade_version(viewset, request, pk)
  · batch_create_command(viewset, request) — F_PROGRAM_COMMAND_BATCH Gate
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)



# ---- _request_data(request) helper (兼容 DRF Request(_request_data(request)) vs Django WSGIRequest(POST/body)) ----
def _request_data(request) -> Dict[str, Any]:
    if request is None:
        return {}
    data = getattr(request, "data", None)
    if data is not None:
        return data
    post = getattr(request, "POST", None)
    if post:
        return dict(post)
    body = getattr(request, "body", None)
    if body:
        import json as _json
        try:
            if isinstance(body, bytes):
                return _json.loads(body.decode("utf-8"))
            return _json.loads(body)
        except Exception:  # noqa: BLE001
            return {}
    return {}


class ProgramPolicyEngine:
    """跨 5 个 VS 共享的算法容器（纯静态类，无状态，便于单例注入）."""

    # =====================================================================
    # ProgramInstallTemplate.apply_to_hosts
    # =====================================================================
    @staticmethod
    def apply_template_to_hosts(viewset, request, pk) -> Tuple[bool, Dict[str, int], str]:
        from taurus.models import Host, ProgramHostBinding, ProgramInstallConfig
        from taurus_ee.serializers.supervisor_program import (
            _EEApplyTemplateToHostsRequestSerializer,
        )
        from rest_framework.exceptions import ValidationError

        template = viewset.get_object()
        req_ser = _EEApplyTemplateToHostsRequestSerializer(data=_request_data(request))
        if not req_ser.is_valid():
            raise ValidationError(req_ser.errors)
        v = req_ser.validated_data
        host_ids: List[int] = v["host_ids"]
        auto_install: bool = v["auto_install"]
        created_count = 0
        install_count = 0
        for host_id in host_ids:
            try:
                host = Host.objects.get(id=host_id)
            except Host.DoesNotExist:
                continue
            binding, created = ProgramHostBinding.objects.get_or_create(
                host=host, template=template,
            )
            if created:
                created_count += 1
            _config, _config_created = ProgramInstallConfig.objects.get_or_create(
                host=host, template=template,
                defaults={
                    "program_name": template.program_name,
                    "version": template.version,
                    "config": template.config,
                    "auto_start": template.auto_start,
                    "user": template.user,
                    "group": template.group,
                    "max_retries": template.max_retries,
                    "installed": False,
                    "installing": bool(auto_install),
                },
            )
            if auto_install:
                if not binding.installed and not binding.installing:
                    binding.installing = True
                    binding.dispatched_at = timezone.now()
                    binding.save()
                    install_count += 1
                    logger.info(
                        "[ProgramPolicyEngine][ApplyTemplate] auto_install host=%s tpl=%s",
                        host.host_name, template.name,
                    )
            else:
                if _config.installing:
                    _config.installing = False
                    _config.save(update_fields=["installing"])
                if binding.installing:
                    binding.installing = False
                    binding.dispatched_at = None
                    binding.save()
        data = {"created_count": created_count, "install_count": install_count}
        msg = (
            f"Template applied to {created_count} hosts"
            + (
                f", {install_count} install commands issued"
                if auto_install else
                f", {created_count} install configs created (manual install required)"
            )
        )
        return True, data, msg

    # =====================================================================
    # ProgramHostBinding.install / uninstall
    # =====================================================================
    @staticmethod
    def host_binding_install(viewset, request, pk) -> Tuple[bool, Dict[str, int], str]:
        from taurus.models import ProgramInstallConfig
        binding = viewset.get_object()
        if binding.installed:
            return False, {}, "Program is already installed"
        if binding.installing:
            return False, {}, "Installing, please do not repeat"
        tpl = binding.template
        config = ProgramInstallConfig.objects.create(
            host=binding.host, template=tpl,
            program_name=tpl.program_name, version=tpl.version,
            config=tpl.config, auto_start=tpl.auto_start,
            user=tpl.user, group=tpl.group, max_retries=tpl.max_retries,
        )
        binding.installing = True
        binding.dispatched_at = timezone.now()
        binding.save()
        return True, {"config_id": config.id}, "Install command issued"

    @staticmethod
    def host_binding_uninstall(viewset, request, pk) -> Tuple[bool, Dict[str, int], str]:
        from taurus.models import ProgramCommand
        binding = viewset.get_object()
        if not binding.installed:
            return False, {}, "Program is not installed"
        command = ProgramCommand.objects.create(
            host=binding.host,
            program_name=binding.template.program_name,
            action="remove",
        )
        return True, {"command_id": command.id}, "Uninstall command issued"

    # =====================================================================
    # ProgramInstallConfig.batch_create / redispatch
    # =====================================================================
    @staticmethod
    def config_batch_create(viewset, request) -> Tuple[bool, Dict[str, int], str]:
        from taurus.models import Host, ProgramInstallConfig, ProgramInstallTemplate
        from taurus_ee.serializers.supervisor_program import (
            _EEBatchInstallConfigRequestSerializer,
        )
        from rest_framework.exceptions import ValidationError

        req_ser = _EEBatchInstallConfigRequestSerializer(data=_request_data(request))
        if not req_ser.is_valid():
            raise ValidationError(req_ser.errors)
        v = req_ser.validated_data
        host_ids: List[int] = v["host_ids"]
        tpl_id = v.get("template_id")

        tpl: ProgramInstallTemplate | None = None
        if tpl_id:
            try:
                tpl = ProgramInstallTemplate.objects.get(id=tpl_id)
            except ProgramInstallTemplate.DoesNotExist:
                raise ValidationError({"template_id": f"Template {tpl_id} not found"})

        program_name = v["program_name"] or (tpl.program_name if tpl else "")
        version = v["version"] or (tpl.version if tpl else "")
        config_dict: Dict[str, Any] = v["config"] or (tpl.config if tpl else {})
        auto_start: bool = v.get("auto_start") if v.get("auto_start") is not None else (tpl.auto_start if tpl else True)
        user: str | None = v.get("user") or (tpl.user if tpl else None)
        group: str | None = v.get("group") or (tpl.group if tpl else None)

        if not program_name:
            raise ValidationError({"program_name": "program_name or template_id is required"})
        if not version:
            raise ValidationError({"version": "version or template_id is required"})

        created_count = 0
        for host_id in host_ids:
            try:
                host = Host.objects.get(id=host_id)
            except Host.DoesNotExist:
                continue
            ProgramInstallConfig.objects.create(
                host=host, template=tpl,
                program_name=program_name, version=version,
                config=config_dict, auto_start=auto_start,
                user=user, group=group,
                creator=getattr(request, "user", None),
            )
            created_count += 1
        return True, {"created_count": created_count}, f"Created {created_count} configs"

    @staticmethod
    def config_redispatch(viewset, request, pk) -> Tuple[bool, Dict[str, Any], str]:
        try:
            config = viewset.get_object()
            config.dispatched_at = None
            config.installing = False
            config.save(update_fields=["dispatched_at", "installing"])
            logger.info(
                "[ProgramPolicyEngine][Redispatch] config_id=%s program=%s host=%s",
                pk, config.program_name, config.host.host_name,
            )
            return True, {}, "Reset dispatch status, will re-issue on next heartbeat"
        except Exception as exc:  # noqa: BLE001
            return False, {}, f"Failed to reissue: {exc}"

    # =====================================================================
    # ProgramInstallPolicy.apply / preview_hosts / upgrade_version
    # =====================================================================
    @staticmethod
    def policy_apply(viewset, request, pk) -> Tuple[bool, Dict[str, int], str]:
        from taurus.models import ProgramInstallConfig

        policy = viewset.get_object()
        if policy.status != 1:
            return False, {}, "Policy not enabled, cannot apply"
        matched_hosts = ProgramPolicyEngine._get_matched_hosts(policy)
        created = 0
        skipped = 0
        for host in matched_hosts:
            exists = ProgramInstallConfig.objects.filter(
                host=host, program_name=policy.program_name, version=policy.version,
            ).exists()
            if exists:
                skipped += 1
                continue
            ProgramInstallConfig.objects.create(
                host=host, program_name=policy.program_name, version=policy.version,
                config=policy.config, auto_start=policy.auto_start,
                user=policy.user if policy.user else None,
                group=policy.group if policy.group else None,
                creator=getattr(request, "user", None),
            )
            created += 1
        policy.matched_hosts_count = len(matched_hosts)
        policy.applied_hosts_count += created
        policy.save(update_fields=["matched_hosts_count", "applied_hosts_count"])
        data = {"matched": len(matched_hosts), "created": created, "skipped": skipped}
        msg = (f"Policy applied: {len(matched_hosts)} hosts matched, "
               f"{created} configs created, {skipped} skipped")
        return True, data, msg

    @staticmethod
    def policy_preview_hosts(viewset, request, pk) -> Tuple[bool, Dict[str, Any], str]:
        from django.core.paginator import Paginator

        policy = viewset.get_object()
        matched = ProgramPolicyEngine._get_matched_hosts(policy)
        page = Paginator(matched, 20)
        # request 可能是 WSGIRequest(GET) 或 DRF Request(query_params)
        query_dict = getattr(request, "query_params", None) or getattr(request, "GET", {})
        page_num = query_dict.get("page", 1)
        try:
            page_num_i = int(page_num)
        except (TypeError, ValueError):
            page_num_i = 1
        page_data = page.get_page(page_num_i)
        hosts_data = [
            {
                "id": h.id, "host_name": h.host_name,
                "host_ip": h.host_ip, "host_type": h.host_type,
                "status": h.status, "online_status": h.online_status,
            }
            for h in page_data
        ]
        data = {"total": len(matched), "hosts": hosts_data}
        return True, data, f"Matched {len(matched)} hosts total"

    @staticmethod
    def policy_upgrade_version(viewset, request, pk) -> Tuple[bool, Dict[str, Any], str]:
        from taurus.models import ProgramInstallConfig
        from taurus_ee.serializers.supervisor_program import (
            _EEPolicyUpgradeRequestSerializer,
        )
        from rest_framework.exceptions import ValidationError

        req_ser = _EEPolicyUpgradeRequestSerializer(data=_request_data(request))
        if not req_ser.is_valid():
            raise ValidationError(req_ser.errors)
        policy = viewset.get_object()
        new_version = req_ser.validated_data["version"]
        old_version = policy.version
        policy.version = new_version
        policy.save(update_fields=["version"])
        applied = ProgramInstallConfig.objects.filter(
            program_name=policy.program_name, version=old_version,
        ).select_related("host")
        created_count = 0
        for cfg in applied:
            exists = ProgramInstallConfig.objects.filter(
                host=cfg.host, program_name=policy.program_name, version=new_version,
            ).exists()
            if not exists:
                ProgramInstallConfig.objects.create(
                    host=cfg.host, program_name=policy.program_name, version=new_version,
                    config=policy.config, auto_start=policy.auto_start,
                    user=policy.user if policy.user else None,
                    group=policy.group if policy.group else None,
                    creator=getattr(request, "user", None),
                )
                created_count += 1
        data = {"old_version": old_version, "new_version": new_version, "created": created_count}
        msg = f"Version upgraded: {old_version} -> {new_version}, {created_count} new configs created"
        return True, data, msg

    # =====================================================================
    # ProgramCommandViewSet.batch_create — F_PROGRAM_COMMAND_BATCH EE 批量
    # =====================================================================
    @staticmethod
    def batch_create_command(viewset, request) -> Tuple[bool, Dict[str, int], str]:
        from taurus.models import Host, ProgramCommand
        from taurus_ee.serializers.supervisor_program import (
            _EEBatchProgramCommandRequestSerializer,
        )
        from rest_framework.exceptions import ValidationError

        req_ser = _EEBatchProgramCommandRequestSerializer(data=_request_data(request))
        if not req_ser.is_valid():
            raise ValidationError(req_ser.errors)
        v = req_ser.validated_data
        host_ids: List[int] = v["host_ids"]
        program_name = v["program_name"]
        action = v["action"]
        target_version = v.get("target_version") or None
        config_dict: Dict[str, Any] = v.get("config") or {}

        created_count = 0
        for host_id in host_ids:
            try:
                host = Host.objects.get(id=host_id)
            except Host.DoesNotExist:
                continue
            ProgramCommand.objects.create(
                host=host, program_name=program_name,
                action=action, target_version=target_version, config=config_dict,
                creator=getattr(request, "user", None),
            )
            created_count += 1
        return True, {"created_count": created_count}, f"Created {created_count} commands"

    # =====================================================================
    # Internal helpers (对应 taurus.views._get_matched_hosts)
    # =====================================================================
    @staticmethod
    def _get_matched_hosts(policy):
        from taurus.models import Host

        match_rules = policy.match_rules
        if not match_rules:
            return list(Host.objects.filter(status=1))
        query = Q()
        for field in ["host_type", "status", "online_status"]:
            if field in match_rules:
                query &= Q(**{field: match_rules[field]})
        if "host_ip_prefix" in match_rules:
            query &= Q(host_ip__startswith=match_rules["host_ip_prefix"])
        if "host_ip_range" in match_rules:
            rng = match_rules["host_ip_range"]
            if isinstance(rng, list) and len(rng) == 2:
                query &= Q(host_ip__gte=rng[0], host_ip__lte=rng[1])
        if "host_name_pattern" in match_rules:
            query &= Q(host_name__icontains=match_rules["host_name_pattern"])
        extra_info_tags = match_rules.get("extra_info_tags") or []
        for tag in extra_info_tags:
            query &= Q(extra_info__icontains=tag)
        return list(Host.objects.filter(query))


__all__ = ["ProgramPolicyEngine"]
