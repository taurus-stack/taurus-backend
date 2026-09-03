"""S2-01 / S2-02 / S2-03 Script, Command & FileOperation ExecutableUnit Adapter.

script / command adapters reuse the OpsExecution async execution chain (dispatch -> poll -> cancel);
file_op adapter uses synchronous calls due to the underlying SDK, completing synchronously in dispatch and returning terminal state directly.

Error code convention:
- E0301: requires_host=True scenario with host_id=NO_HOST
- E0302: params.script_id references a non-existent Script (script adapter only)
- E0304: file_op source_path / remote_path missing or invalid
- E2401: immediate execution submit_execution failed and no polling fallback (rarely triggered in practice, written to adapter_state for diagnosis)
- E3001: execution finished with exit_code != 0 (business failure, not retryable)
- E3002: OpsExecution status marked as 3=Failed but exit_code is None (remote environment exception)
- E3003: File transfer SDK call returned success=False
- E4001: task timeout (killed by executor, corresponding to OpsExecution.status=4 aborted)
"""
from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from taurus.workflow.engine.base_adapter import ExecutableUnit
from taurus.workflow.engine.context import WorkflowContext
from taurus.workflow.engine.registry import register_unit_adapter
from taurus.workflow.engine.schemas import (
    NO_HOST_SENTINEL,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    RenderedNodeConfig,
    UnitOutput,
    ValidationResult,
)

logger = logging.getLogger(__name__)

# OpsExecution.status constants (model: 0=pending 1=running 2=success 3=failed 4=aborted)
_OPS_PENDING = 0
_OPS_RUNNING = 1
_OPS_SUCCESS = 2
_OPS_FAILED = 3
_OPS_ABORTED = 4

# OpsExecution execution strategy constants
FAIL_STRATEGY_VALUES = {"stop", "continue"}
EXEC_MODE_VALUES = {"serial", "parallel", "pilot"}


def _extract_stdout_stderr(output_buffer: list[Any] | None) -> tuple[str, str, dict[str, Any]]:
    """Extract stdout / stderr / structured key-value pairs from OpsExecution.output_buffer.

    Supports two executor line protocols:
      A. Old (type/content layered): {"type":"stdout|stderr|exit_code|env","content":...,"ts":...}
      B. New (flat fields): {"stdout":"..."} / {"stderr":"..."} / {"finished":true,"exit_code":N}
    Both formats may appear in actual deployments, so both are supported.
    Additionally, env-type lines directly write structured output.
    """
    buf: list[dict[str, Any]] = list(output_buffer or [])
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    envs: dict[str, Any] = {}
    raw_lines: list[dict[str, Any]] = []
    for item in buf:
        if not isinstance(item, dict):
            raw_lines.append({"line": str(item)})
            continue
        raw_lines.append(item)

        # ---- Protocol A: {type, content} ----
        t = item.get("type")
        c = item.get("content")
        if t == "stdout":
            if isinstance(c, str):
                stdout_parts.append(c)
            continue
        if t == "stderr":
            if isinstance(c, str):
                stderr_parts.append(c)
            continue
        if t == "env" and isinstance(c, dict):
            for k, v in c.items():
                envs[k] = v
            continue

        # ---- Protocol B: flat fields (stdout / stderr as direct keys) ----
        flat_stdout = item.get("stdout")
        if isinstance(flat_stdout, str) and flat_stdout:
            stdout_parts.append(flat_stdout)
        flat_stderr = item.get("stderr")
        if isinstance(flat_stderr, str) and flat_stderr:
            stderr_parts.append(flat_stderr)

        # ---- Others: exit_code / finished / ts and other meta message fields, already in raw_lines, no special processing needed ----

    stdout = "".join(stdout_parts)
    stderr = "".join(stderr_parts)
    structured: dict[str, Any] = {}
    if envs:
        structured["envs"] = envs
    if raw_lines:
        structured["lines"] = raw_lines
    return stdout, stderr, structured


def _trigger_or_schedule_ops_execution(execution_id: str) -> None:
    """Attempt to dispatch the newly created OpsExecution to the executor immediately;
    if it fails, fall back to polling.

    Reuses the submit_execution pattern from views.execute_script / execute_command,
    but does not depend on a request object.
    """
    try:
        from taurus.websocket_async import (
            _execute_ops_async,
            get_event_loop,
            submit_execution,
        )
    except Exception:  # noqa: BLE001
        logger.debug("[wf-adapter] Unable to import immediate trigger chain, will fall back to polling")
        return
    try:
        loop = get_event_loop()
        if loop is None or loop.is_closed():
            return
        submitted = submit_execution(execution_id, _execute_ops_async(execution_id))
        if submitted:
            logger.info("[wf-adapter] Immediate trigger OpsExecution %s", execution_id)
        else:
            logger.info("[wf-adapter] Immediate trigger submission failed, will fall back to polling: %s", execution_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("[wf-adapter] Immediate trigger exception, will fall back to polling: %s", e)


def _normalize_frontend_params(params: dict[str, Any]) -> dict[str, Any]:
    """Align flat field names used in frontend manifest to the backend adapter layer convention.

    Compatibility description: frontend and backend manifest field names are not fully consistent,
    unified normalization before validate/dispatch:
      - timeout         -> timeout_seconds        (command / script / file_op / program etc.)
      - operation       -> action                 (file_op frontend uses operation, backend uses action)
      - file_paths      -> split into remote_path/source_path (file_op frontend key-value-table stores paths)
      - params (dict)   -> args (list)            (script node: frontend writes params kv, backend OpsExecution uses args)
      - args: dict      -> list                   (frontend key-value-table saves as {k:v},
                                                      backend OpsExecution.args accepts list positional parameters;
                                                      if dict, convert values in order to positional parameter array;
                                                      also allow empty dict as no parameters)
    """
    if not isinstance(params, dict):
        return params
    p = dict(params)

    # 0. script node: frontend manifest uses params as script parameters key-value-table
    if "params" in p and "args" not in p:
        params_val = p["params"]
        if isinstance(params_val, dict):
            values = [v for v in params_val.values() if v is not None and v != ""]
            p["args"] = values if values else []
        elif isinstance(params_val, list):
            p["args"] = list(params_val)

    # 1. timeout / timeout_sec -> timeout_seconds
    for src in ("timeout", "timeout_sec"):
        if src in p and "timeout_seconds" not in p:
            val = p[src]
            if isinstance(val, bool):
                pass
            elif isinstance(val, (int, float)):
                p["timeout_seconds"] = int(val)
            elif isinstance(val, str) and val.strip():
                try:
                    p["timeout_seconds"] = int(val.strip())
                except ValueError:
                    pass

    # 2. operation -> action (file_op), default upload
    if "operation" in p and "action" not in p:
        op = p["operation"]
        if op in ("download", "upload", "transfer", "copy"):
            if op == "transfer":
                op = "download"
            p["action"] = op
    if "action" not in p:
        p["action"] = "upload"

    # 2.1 file_op create fields: source_type / source_protocol / source_url / source_auth_* (pass-through normalization)
    if "source_type" in p:
        st = p["source_type"]
        if isinstance(st, str) and st.strip():
            p["source_type"] = st.strip()
        else:
            p["source_type"] = "local_path"

    VALID_SOURCE_PROTOCOLS = ("http", "https", "ftp", "ftps", "sftp")

    # New approach: remote_sources (multi-remote link array)
    remote_sources = p.get("remote_sources")
    if isinstance(remote_sources, (list, tuple)) and remote_sources:
        normalized_rs = []
        for rs in remote_sources:
            if not isinstance(rs, dict):
                continue
            url = str(rs.get("url") or "").strip()
            if not url:
                continue
            proto = str(rs.get("protocol") or "https").strip().lower()
            if proto not in VALID_SOURCE_PROTOCOLS:
                proto = "https"
            entry: dict[str, str] = {"url": url, "protocol": proto}
            if proto in ("ftp", "ftps", "sftp"):
                uname = str(rs.get("username") or "").strip()
                pwd = str(rs.get("password") or "").strip()
                entry["username"] = uname
                entry["password"] = pwd
            normalized_rs.append(entry)
        p["remote_sources"] = normalized_rs
    elif "remote_sources" in p:
        p.pop("remote_sources", None)

    # Backward compat: if only source_url exists (no remote_sources), convert to single-element array
    if not p.get("remote_sources") and "source_url" in p:
        su = p.get("source_url")
        if isinstance(su, str) and su.strip():
            sp = str(p.get("source_protocol") or "https").strip().lower()
            if sp not in VALID_SOURCE_PROTOCOLS:
                sp = "https"
            legacy_rs: dict[str, str] = {"url": su.strip(), "protocol": sp}
            if sp in ("ftp", "ftps", "sftp"):
                un = p.get("source_auth_username")
                pw = p.get("source_auth_password")
                if isinstance(un, str) and un.strip():
                    legacy_rs["username"] = un.strip()
                if isinstance(pw, str) and pw != "":
                    legacy_rs["password"] = pw
            p["remote_sources"] = [legacy_rs]

    # Clean up old fields (if already converted to remote_sources)
    if p.get("remote_sources"):
        for k in ("source_protocol", "source_url", "source_auth_username", "source_auth_password"):
            p.pop(k, None)
    else:
        if "source_protocol" in p:
            sp = p["source_protocol"]
            if isinstance(sp, str) and sp.strip():
                spv = sp.strip().lower()
                if spv in VALID_SOURCE_PROTOCOLS:
                    p["source_protocol"] = spv
            else:
                p["source_protocol"] = "https"
        elif "source_url" in p and isinstance(p["source_url"], str):
            from urllib.parse import urlparse
            parsed = urlparse(p["source_url"])
            if parsed.scheme in VALID_SOURCE_PROTOCOLS:
                p["source_protocol"] = parsed.scheme
            else:
                p["source_protocol"] = "https"
        if "source_url" in p:
            su = p["source_url"]
            if isinstance(su, str) and su.strip():
                p["source_url"] = su.strip()
            else:
                p.pop("source_url", None)
        for k in ("source_auth_username", "source_auth_password"):
            if k in p:
                v = p[k]
                if isinstance(v, str) and v != "":
                    p[k] = v
                else:
                    p.pop(k, None)

    # 3. file_paths (dict/kv) -> remote_path & source_path & file_paths_list
    #    when source_type=http_url, source_path is not taken from here (leave empty, generated at dispatch phase)
    http_url_mode = p.get("source_type") == "http_url"
    file_paths = p.get("file_paths")
    file_paths_list: list[dict[str, str]] = []
    if isinstance(file_paths, dict) and file_paths:
        for src, dst in file_paths.items():
            src_s = str(src or "").strip()
            dst_s = str(dst or "").strip()
            if dst_s:
                entry = {"source_path": src_s, "remote_path": dst_s}
                file_paths_list.append(entry)
                if "remote_path" not in p:
                    p["remote_path"] = dst_s
                if (not http_url_mode) and src_s and "source_path" not in p:
                    p["source_path"] = src_s
    elif isinstance(file_paths, (list, tuple)) and file_paths:
        for item in file_paths:
            if not isinstance(item, dict):
                continue
            key = item.get("key") or item.get("source") or item.get("src") or ""
            val = item.get("value") or item.get("destination") or item.get("dst") or ""
            key_s = str(key or "").strip()
            val_s = str(val or "").strip()
            if val_s or key_s:
                entry = {"source_path": key_s, "remote_path": val_s}
                file_paths_list.append(entry)
                if val_s and "remote_path" not in p:
                    p["remote_path"] = val_s
                if (not http_url_mode) and key_s and "source_path" not in p:
                    p["source_path"] = key_s
    p["file_paths_list"] = file_paths_list

    # 4. args compat: allow dict / None / empty values, unified no-error
    args_val = p.get("args", None)
    if isinstance(args_val, dict):
        values = [v for v in args_val.values() if v is not None and v != ""]
        if values:
            p["args"] = values
        else:
            p["args"] = []
    elif args_val is None or (isinstance(args_val, str) and not args_val.strip()):
        p["args"] = []

    return p


class _OpsExecutionMixin(ExecutableUnit):
    """OpsExecution adapter common logic (shared by script / command / upload / download).

    Subclasses must implement:
      - _make_execution_type() -> str
      - _extra_validate(params, r: ValidationResult)
      - _fill_ops_exec_kwargs(params, user) -> dict  # return extra fields required by OpsExecution.objects.create(...)
    """

    requires_host = True
    is_asynchronous_human = False

    # ---- Abstract hooks to be implemented by subclasses ----
    def _execution_type(self) -> str:
        raise NotImplementedError

    def _extra_validate(self, params: dict[str, Any], r: ValidationResult) -> None:
        raise NotImplementedError

    def _fill_ops_exec_kwargs(self, params: dict[str, Any], user: Any) -> dict[str, Any]:
        raise NotImplementedError

    # ---- Common public methods ----
    def validate_config(self, params, *, secrets_mask=None):
        p = _normalize_frontend_params(params)
        r = ValidationResult.success()
        node = getattr(self, "node_type", "unknown")
        logger.debug("[wf-adapter] validate_config start node=%s keys=%s", node, list(p.keys()))

        timeout = p.get("timeout_seconds")
        if timeout is not None:
            if not isinstance(timeout, int) or timeout <= 0 or timeout > 86400 * 7:
                logger.warning("[wf-adapter] validate_config failed node=%s field=timeout_seconds value=%s reason=out of range (0, 604800]", node, timeout)
                r.add_error("/params/timeout_seconds", "E0xxx:timeout_seconds must be an integer in (0, 604800]")

        if "args" in p and not isinstance(p["args"], list):
            logger.warning("[wf-adapter] validate_config failed node=%s field=args value_type=%s reason=must be a JSON array", node, type(p["args"]).__name__)
            r.add_error("/params/args", "E0xxx:args must be a JSON array")

        if "environment" in p and not isinstance(p["environment"], dict):
            logger.warning("[wf-adapter] validate_config failed node=%s field=environment value_type=%s reason=must be a JSON object", node, type(p["environment"]).__name__)
            r.add_error("/params/environment", "E0xxx:environment must be a JSON object")

        # ---- OpsExecution execution strategy field validation ----
        fs = p.get("ops_fail_strategy")
        if fs is not None and fs not in FAIL_STRATEGY_VALUES:
            logger.warning("[wf-adapter] validate_config failed node=%s field=ops_fail_strategy value=%s reason=must be in %s", node, fs, FAIL_STRATEGY_VALUES)
            r.add_error("/params/ops_fail_strategy", f"E0xxx:ops_fail_strategy must be in {FAIL_STRATEGY_VALUES}")

        em = p.get("exec_mode")
        if em is not None and em not in EXEC_MODE_VALUES:
            logger.warning("[wf-adapter] validate_config failed node=%s field=exec_mode value=%s reason=must be in %s", node, em, EXEC_MODE_VALUES)
            r.add_error("/params/exec_mode", f"E0xxx:exec_mode must be in {EXEC_MODE_VALUES}")

        conc = p.get("concurrent")
        if conc is not None:
            if not isinstance(conc, int) or conc < 1 or conc > 50:
                logger.warning("[wf-adapter] validate_config failed node=%s field=concurrent value=%s reason=must be an integer in [1, 50]", node, conc)
                r.add_error("/params/concurrent", "E0xxx:concurrent must be an integer in [1, 50]")

        pc = p.get("pilot_count")
        if pc is not None:
            if not isinstance(pc, int) or pc < 1 or pc > 10:
                logger.warning("[wf-adapter] validate_config failed node=%s field=pilot_count value=%s reason=must be an integer in [1, 10]", node, pc)
                r.add_error("/params/pilot_count", "E0xxx:pilot_count must be an integer in [1, 10]")

        psr = p.get("pilot_success_rate")
        if psr is not None:
            if not isinstance(psr, int) or psr < 1 or psr > 100:
                logger.warning("[wf-adapter] validate_config failed node=%s field=pilot_success_rate value=%s reason=must be an integer in [1, 100]", node, psr)
                r.add_error("/params/pilot_success_rate", "E0xxx:pilot_success_rate must be an integer in [1, 100]")

        self._extra_validate(p, r)

        if r.ok:
            logger.info("[wf-adapter] validate_config passed node=%s", node)
        else:
            logger.warning("[wf-adapter] validate_config failed node=%s error_count=%d", node, len(r.errors or {}))
        return r

    def validate_and_render(
        self,
        params: dict[str, Any],
        context: WorkflowContext,
        *,
        secrets_mask: list[str] | None = None,
    ) -> dict[str, Any]:
        # Recursively interpolate environment values and args items as well
        # secrets_mask parameter kept to match base class signature, render_structure internal unified processing
        rendered = context.render_structure(_normalize_frontend_params(params))
        return rendered

    def dispatch(self, cfg: RenderedNodeConfig) -> UnitOutput:
        # 0. requires_host=True, confirm host_id is not NO_HOST_SENTINEL
        if cfg.host_id is None or cfg.host_id == NO_HOST_SENTINEL:
            return UnitOutput(
                status=STATUS_FAILED,
                output={},
                exit_code=1,
                error_message="E0301: script/command requires_host=True, but current node did not resolve to a specific host_id",
            )

        from taurus.models import Host, OpsExecution
        from dvadmin.system.models import Users

        user: Any = None
        if getattr(cfg, "user_id", None):
            try:
                user = Users.objects.filter(pk=cfg.user_id).first()
            except Exception:  # noqa: BLE001
                user = None
        host = Host.objects.filter(pk=cfg.host_id).first()
        if host is None:
            return UnitOutput(
                status=STATUS_FAILED,
                output={},
                exit_code=1,
                error_message=f"E0303: host_id={cfg.host_id!r} does not exist in Host table",
            )

        # 1. Idempotency: for the same dispatch_id, if a corresponding OpsExecution row already exists, retrieve and return it directly
        #    Reuse ops_execution_id as foreign key, also write dispatch_id into batch_id simultaneously for easy querying
        existing = OpsExecution.objects.filter(batch_id=str(cfg.dispatch_id)).first()
        if existing is not None:
            return self._unit_output_from_ops(existing)

        # 2. Create new OpsExecution row
        privileged_flag = bool(cfg.params.get("privileged", False))
        env_dict: dict[str, Any] = dict(cfg.params.get("environment") or {})
        if privileged_flag:
            env_dict["PRIVILEGED_EXECUTION"] = "true"
            su_user = cfg.params.get("su_user")
            if su_user:
                env_dict["SU_USER"] = str(su_user)
            su_pwd = cfg.params.get("su_password")
            if su_pwd:
                from taurus.config_crypto import encrypt_value
                env_dict["SU_PASSWORD"] = encrypt_value(str(su_pwd))

        timeout_val = cfg.params.get("timeout_seconds")
        timeout_sec = int(timeout_val if timeout_val is not None else (cfg.global_timeout_sec or 300))
        timeout_sec = max(10, min(timeout_sec, 86400))

        concurrent_val = cfg.params.get("concurrent")
        concurrent = int(concurrent_val if concurrent_val is not None else 10)
        concurrent = max(1, min(concurrent, 50))

        pilot_count_val = cfg.params.get("pilot_count")
        pilot_count = int(pilot_count_val if pilot_count_val is not None else 2)
        pilot_count = max(1, min(pilot_count, 10))

        pilot_sr_val = cfg.params.get("pilot_success_rate")
        pilot_success_rate = int(pilot_sr_val if pilot_sr_val is not None else 100)
        pilot_success_rate = max(1, min(pilot_success_rate, 100))

        kwargs: dict[str, Any] = {
            "execution_type": self._execution_type(),
            "host": host,
            "user": user,
            "timeout_seconds": timeout_sec,
            "args": list(cfg.params.get("args") or []),
            "working_directory": cfg.params.get("working_directory") or None,
            "environment": env_dict,
            "merge_streams": bool(cfg.params.get("merge_streams", False)),
            "load_profile": cfg.params.get("load_profile") or "false",
            "privileged": privileged_flag,
            "su_user": cfg.params.get("su_user") or None,
            "exec_mode": cfg.params.get("exec_mode") or "parallel",
            "concurrent": concurrent,
            "fail_strategy": cfg.params.get("ops_fail_strategy") or "stop",
            "pilot_count": pilot_count,
            "pilot_success_rate": pilot_success_rate,
            "need_audit": False,  # Workflow's own approval nodes cover approval semantics, no secondary approval here
            "auto_notify": False,
            "status": _OPS_PENDING,
            # Key: store dispatch_id in batch_id, implement idempotent de-duplication + reverse tracing
            "batch_id": str(cfg.dispatch_id),
        }
        kwargs.update(self._fill_ops_exec_kwargs(dict(cfg.params), user))
        try:
            # === 配额校验：最大并发执行数 ===
            from taurus.editions.loader import check_quota as _check_quota
            from taurus.models import OpsExecution as _OpsExec
            _check_quota('max_concurrent_executions',
                         _OpsExec.objects.filter(status__in=[0, 1, 5]).count(), '并发执行任务')
            ops = OpsExecution.objects.create(
                execution_id=str(cfg.dispatch_id)[:56] + "-" + str(host.pk)[:7],
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[wf-adapter] Failed to create OpsExecution dispatch_id=%s", cfg.dispatch_id)
            return UnitOutput(
                status=STATUS_FAILED,
                output={},
                exit_code=1,
                error_message=f"E0310: Failed to create OpsExecution {type(exc).__name__}: {exc}",
            )

        # 3. Immediate trigger (fall back to polling on failure, not treated as error)
        _trigger_or_schedule_ops_execution(ops.execution_id)

        # 4. Return RUNNING, next poll will query status
        return UnitOutput(
            status=STATUS_RUNNING,
            output={"ops_execution_id": ops.execution_id},
            adapter_state={
                "ops_execution_id": ops.execution_id,
                "created_at": timezone.now().isoformat(),
            },
        )

    # Threshold for re-triggering execution under PENDING status (seconds)
    _PENDING_RETRY_SEC = 15
    # Fallback threshold for synchronous execution under PENDING (seconds), beyond which the submit_execution chain is considered unavailable
    _PENDING_FORCE_EXEC_SEC = 60

    def poll(self, cfg, adapter_state):
        ops_exec_id = None
        if isinstance(adapter_state, dict):
            ops_exec_id = adapter_state.get("ops_execution_id")
        if not ops_exec_id:
            # Fallback: reverse lookup from batch_id=dispatch_id
            # Note: this runs without a transaction context in advance_workflow(), direct read is fine
            # (autocommit mode means each query reads current data, getting the latest committed value)
            from taurus.models import OpsExecution
            found = OpsExecution.objects.filter(batch_id=str(cfg.dispatch_id)).first()
            if found is None:
                logger.warning(
                    "[wf-adapter] poll cannot find OpsExecution batch_id=%s adapter_state_keys=%s",
                    cfg.dispatch_id, list((adapter_state or {}).keys()) if isinstance(adapter_state, dict) else "N/A",
                )
                return UnitOutput(
                    status=STATUS_FAILED,
                    output={},
                    exit_code=1,
                    error_message="E0311: poll cannot find corresponding OpsExecution row, please check if dispatch threw an exception",
                )
            ops_exec_id = found.execution_id

        from taurus.models import OpsExecution
        ops = OpsExecution.objects.filter(execution_id=ops_exec_id).first()
        if ops is None:
            logger.warning(
                "[wf-adapter] poll OpsExecution does not exist execution_id=%r batch_id=%s",
                ops_exec_id, cfg.dispatch_id,
            )
            return UnitOutput(
                status=STATUS_FAILED,
                output={},
                exit_code=1,
                error_message=f"E0312: OpsExecution.execution_id={ops_exec_id!r} does not exist (may have been accidentally deleted)",
            )

        # [Fix Bug 2] When immediate trigger commit fails causing OpsExecution to be permanently stuck in PENDING,
        # fall back to re-dispatch via poll
        if ops.status == _OPS_PENDING:
            self._resubmit_pending_ops_if_needed(ops)
            # Re-read (resubmit may have changed status to RUNNING)
            ops.refresh_from_db()

        uo = self._unit_output_from_ops(ops, from_poll=True)
        # INFO level log: for easy confirmation of REPEATABLE READ snapshot issue resolution
        logger.info(
            "[wf-adapter] poll ops_id=%s ops_status=%d exit_code=%s -> uo_status=%d",
            ops_exec_id, ops.status, ops.exit_code, uo.status,
        )
        return uo

    def _resubmit_pending_ops_if_needed(self, ops: Any) -> None:
        """Fallback re-dispatch for OpsExecution stuck in PENDING.

        During dispatch, _trigger_or_schedule_ops_execution() immediately commits to the ws event loop,
        but scenarios like ws process not started / event loop unavailable / commit exception will silently fail,
        causing ops to stay in PENDING forever and never execute. Detect and fall back on the poll path:
          1. > _PENDING_RETRY_SEC: retry submit_execution (ws chain may have recovered)
          2. > _PENDING_FORCE_EXEC_SEC: bypass ws chain, run execution directly in current thread using asyncio
            (although synchronous blocking poll is not elegant, it's better than E4401 failure after 200 polls)
        """
        now = timezone.now()
        created = ops.create_datetime or ops.started_at or now
        pending_sec = (now - created).total_seconds()
        if pending_sec < self._PENDING_RETRY_SEC:
            return

        # phase 1: retry submit_execution (ws may have recovered)
        submitted_ok = False
        try:
            from taurus.websocket_async import (
                _execute_ops_async,
                get_event_loop,
                submit_execution,
            )
            loop = get_event_loop()
            if loop is not None and not loop.is_closed():
                submitted_ok = submit_execution(
                    ops.execution_id, _execute_ops_async(ops.execution_id)
                )
        except Exception:  # noqa: BLE001
            submitted_ok = False

        if submitted_ok:
            logger.info(
                "[wf-adapter] PENDING fallback re-dispatch succeeded (submit_execution) ops_id=%s pending_sec=%.1f",
                ops.execution_id, pending_sec,
            )
            return

        # phase 2: exceeded force threshold, direct synchronous execution (fallback of fallback)
        if pending_sec < self._PENDING_FORCE_EXEC_SEC:
            # Not yet reached force threshold, wait for next round (avoid duplicate trigger during concurrency)
            return

        # Record to adapter_state to prevent duplicate force execution (protected against concurrent polls from multiple processes)
        try:
            from taurus.websocket_async import _execute_ops_async  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            logger.warning(
                "[wf-adapter] PENDING fallback cannot import _execute_ops_async ops_id=%s",
                ops.execution_id,
            )
            return

        import asyncio as _asyncio
        import concurrent.futures as _futures
        import threading as _threading

        lock_key = f"__pending_force_running_{ops.execution_id}__"
        if getattr(type(self), lock_key, False):
            return
        # Use class-level atomic flag to avoid duplicate commits within same process (cross-process protected by DB status change below)
        try:
            setattr(type(self), lock_key, True)

            # DB-level change status to RUNNING as "claimed" marker, prevent other processes from duplicate execution
            from django.db import transaction
            with transaction.atomic():
                ops_refresh = type(ops).objects.select_for_update().filter(pk=ops.pk).first()
                if ops_refresh is None or ops_refresh.status != _OPS_PENDING:
                    return
                ops_refresh.status = _OPS_RUNNING
                ops_refresh.started_at = ops_refresh.started_at or timezone.now()
                ops_refresh.save(update_fields=["status", "started_at", "update_datetime"])
                ops.status = _OPS_RUNNING
                ops.started_at = ops_refresh.started_at

            logger.warning(
                "[wf-adapter] PENDING timeout (%.1fs), fallback direct synchronous execution ops_id=%s",
                pending_sec, ops.execution_id,
            )

            def _run_sync():
                try:
                    try:
                        loop = _asyncio.get_running_loop()
                    except RuntimeError:
                        loop = None
                    if loop and loop.is_running():
                        with _futures.ThreadPoolExecutor(max_workers=1) as pool:
                            return pool.submit(
                                _asyncio.run, _execute_ops_async(ops.execution_id)
                            ).result(timeout=ops.timeout_seconds + 300 if ops.timeout_seconds else 600)
                    else:
                        return _asyncio.run(_execute_ops_async(ops.execution_id))
                except Exception as e:  # noqa: BLE001
                    logger.exception("[wf-adapter] PENDING fallback execution exception ops_id=%s err=%s", ops.execution_id, e)
                    # On failure, change status back to PENDING for next round retry (simple process, limited attempts)
                    try:
                        ops.status = _OPS_PENDING
                        ops.save(update_fields=["status", "update_datetime"])
                    except Exception:  # noqa: BLE001
                        pass

            # Run in independent thread, don't block poll (avoid dragging advance_workflow transaction too long)
            t = _threading.Thread(target=_run_sync, name=f"ops-pending-force-{ops.execution_id[:8]}", daemon=True)
            t.start()
        finally:
            try:
                delattr(type(self), lock_key)
            except AttributeError:
                pass

    def cancel(self, cfg, adapter_state):
        ops_exec_id = None
        if isinstance(adapter_state, dict):
            ops_exec_id = adapter_state.get("ops_execution_id")
        from taurus.models import OpsExecution
        if not ops_exec_id:
            found = OpsExecution.objects.filter(batch_id=str(cfg.dispatch_id)).first()
            if found is not None:
                ops_exec_id = found.execution_id
        if not ops_exec_id:
            # Cancelled before creation, treat as success
            return UnitOutput(status=STATUS_CANCELLED, output={"cancelled": True, "noop": True})

        # Mark cancel flag on adapter_state, simultaneously attempt to send cancel RPC to executor
        ops = OpsExecution.objects.filter(execution_id=ops_exec_id).first()
        if ops is None:
            return UnitOutput(status=STATUS_CANCELLED, output={"cancelled": True})

        # If already in terminal state, return actual value (don't force override to CANCELLED, avoid semantic contradiction)
        if ops.status in (_OPS_SUCCESS, _OPS_FAILED, _OPS_ABORTED):
            return self._unit_output_from_ops(ops)

        # Attempt trigger cancel via existing cancel API path: reuse ops_cancel flow from views
        try:
            from taurus.websocket_async import cancel_execution  # type: ignore[attr-defined]
            ok = cancel_execution(ops_exec_id)
            cancelled_ok = bool(ok)
        except Exception:  # noqa: BLE001
            # Fallback: directly write DB row as 4=aborted, will be overwritten by executor side on next write-back
            ops.status = _OPS_ABORTED
            ops.finished_at = timezone.now()
            ops.error_message = "E4002: Workflow-level cancel (executor side not yet actually killed)"
            ops.save(update_fields=["status", "finished_at", "error_message", "update_datetime"])
            cancelled_ok = True
        if cancelled_ok:
            return UnitOutput(
                status=STATUS_CANCELLED,
                output={"ops_execution_id": ops_exec_id, "cancelled": True},
            )
        # Cancel interface call failed, return RUNNING for upper layer to resume poll until terminal state
        return UnitOutput(status=STATUS_RUNNING, output={"cancel_submitted": False})

    # ---- internal utility helpers ----
    def _unit_output_from_ops(self, ops: Any, *, from_poll: bool = False) -> UnitOutput:
        stdout, stderr, structured = _extract_stdout_stderr(ops.output_buffer or [])
        output: dict[str, Any] = {
            "ops_execution_id": ops.execution_id,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": ops.exit_code,
        }
        if structured:
            output["structured"] = structured
        if ops.started_at and ops.finished_at:
            dur_ms = int((ops.finished_at - ops.started_at).total_seconds() * 1000)
            output["duration_ms"] = dur_ms
        status = ops.status
        if status in (_OPS_PENDING, _OPS_RUNNING):
            # Note: RUNNING phase also returns collected stdout/stderr, allowing frontend/downstream to progressively
            # see output during polling, instead of only appearing once at SUCCESS.
            running_output: dict[str, Any] = dict(output)
            running_output["ops_status"] = status
            # Running phase exit_code is usually None, avoid misleading downstream into thinking it's completed
            running_output["exit_code"] = ops.exit_code
            return UnitOutput(
                status=STATUS_RUNNING,
                output=running_output,
                adapter_state={"ops_execution_id": ops.execution_id},
            )
        if status == _OPS_SUCCESS:
            return UnitOutput(status=STATUS_SUCCESS, output=output, exit_code=ops.exit_code or 0)
        if status == _OPS_FAILED:
            ec = ops.exit_code if ops.exit_code is not None else 1
            msg = ops.error_message or ""
            if not msg.startswith("E300"):
                msg = f"E3002: {msg}" if ops.exit_code is None else f"E3001: {msg}"
            return UnitOutput(status=STATUS_FAILED, output=output, exit_code=ec, error_message=msg)
        if status == _OPS_ABORTED:
            # 4=aborted = timeout / cancel: distinguish E4001 vs CANCELLED: check if error_message contains "Workflow-level cancel"
            reason = ops.error_message or ""
            if "workflow-level cancel" in reason.lower() or "cancel" in reason.lower():
                return UnitOutput(
                    status=STATUS_CANCELLED,
                    output=output,
                    exit_code=ops.exit_code or 0,
                )
            return UnitOutput(
                status=STATUS_FAILED,
                output=output,
                exit_code=ops.exit_code or 1,
                error_message=f"E4001: Task timeout or interrupted by executor, detail={reason}",
            )
        # Unknown status, conservatively FAILED
        return UnitOutput(
            status=STATUS_FAILED,
            output=output,
            exit_code=ops.exit_code or 1,
            error_message=f"E0399: Unknown OpsExecution.status={status}",
        )


# --------------------------------------------------------------------------- Script Adapter
@register_unit_adapter("script")
class ScriptAdapter(_OpsExecutionMixin):
    """S2-01 Script Node: Reuses OpsExecution(script) + existing TaurusExecutor / Host SDK.

    manifest.params fields:
      - script_id: str / int, **Required**. References taurus.models.Script primary key
      - script_overrides: dict(Optional). Allows runtime override:
            * script_content: str (dynamic script, bypass script_id; disabled in approval/audit scenarios, allowed by default)
            * script_type:   str("sh"/"python", required when using script_content)
      - args: list[any], Optional (passed as $1, $2... or sys.argv[1:] to the script)
      - timeout_seconds: int, Optional, default 300
      - working_directory / environment / merge_streams / load_profile / privileged / su_user: Optional
    """
    node_type = "script"
    display_name = "Script Execution"
    category = "execution"

    def _execution_type(self) -> str:
        return "script"

    def _extra_validate(self, params: dict[str, Any], r: ValidationResult) -> None:
        has_id = "script_id" in params and params["script_id"] not in (None, "",)
        has_content = isinstance(params.get("script_overrides"), dict) and params["script_overrides"].get("script_content")
        if (not has_id) and (not has_content):
            r.add_error("/params", "E0302: Must provide script_id, or script_overrides.script_content for dynamic script")
            return

        allowed_types = {"sh", "python", "powershell", "bat", "sql"}
        overrides = params.get("script_overrides")
        if isinstance(overrides, dict) and overrides.get("script_content"):
            st = overrides.get("script_type") or params.get("script_type")
            if st not in allowed_types:
                r.add_error(
                    "/params/script_type",
                    f"E0302: script_type must be in {allowed_types} when using dynamic scripts",
                )

        top_st = params.get("script_type")
        if top_st is not None and top_st not in allowed_types:
            r.add_error("/params/script_type", f"E0302: script_type must be in {allowed_types}")

    def _fill_ops_exec_kwargs(self, params: dict[str, Any], user: Any) -> dict[str, Any]:
        from taurus.models import Script
        from taurus.utils import map_script_type

        script_content: str | None = None
        script_type: str | None = None
        overrides = params.get("script_overrides")
        if isinstance(overrides, dict) and overrides.get("script_content"):
            script_content = str(overrides["script_content"])
            script_type = str(overrides.get("script_type") or params.get("script_type") or "sh")
        else:
            script_id = params.get("script_id")
            script = Script.objects.filter(pk=script_id).first()
            if script is None:
                raise ValueError(f"E0302: Script(id={script_id!r}) does not exist")
            script_content = script.content or ""
            script_type = map_script_type(script.script_type)
            # Allow top-level script_type to override the DB value
            top_st = params.get("script_type")
            if top_st:
                script_type = str(top_st)

        return {
            "script_type": script_type,
            "script_content": script_content,
        }

    def dispatch(self, cfg: RenderedNodeConfig) -> UnitOutput:
        try:
            return super().dispatch(cfg)
        except ValueError as exc:
            msg = str(exc)
            if msg.startswith("E0302"):
                return UnitOutput(status=STATUS_FAILED, output={}, exit_code=1, error_message=msg)
            raise


# -------------------------------------------------------------------------- Command Adapter
@register_unit_adapter("command")
class CommandAdapter(_OpsExecutionMixin):
    """S2-02 Command Node: Reuses OpsExecution(command) execution chain.

    manifest.params fields:
      - command: str, **Required**. Command string, supports shell pipes
      - use_shell: bool, default True. If True, executes via /bin/bash -c "{command}"
      - Other fields same as ScriptAdapter (args/timeout/environment/merge_streams/load_profile/...)
    """
    node_type = "command"
    display_name = "Command Execution"
    category = "execution"

    def _execution_type(self) -> str:
        return "command"

    def _extra_validate(self, params: dict[str, Any], r: ValidationResult) -> None:
        if not isinstance(params.get("command"), str) or not params["command"].strip():
            r.add_error("/params/command", "E0302: command field is required and cannot be empty string")

    def _fill_ops_exec_kwargs(self, params: dict[str, Any], user: Any) -> dict[str, Any]:
        return {
            "command": str(params.get("command") or ""),
            "use_shell": bool(params.get("use_shell", True)),
        }


# --------------------------------------------------------------------------- File Op Adapter
@register_unit_adapter("file_op")
class FileOpAdapter(ExecutableUnit):
    """S2-03 File Operation Node: Reuses OpsExecution(upload/download) records, SDK synchronous calls.

    Unlike script/command, file transfer uses TaurusClient SDK's synchronous upload_file /
    download_file interface (underlying gRPC), completes directly in dispatch and returns terminal state.
    poll / cancel directly return for rows already in terminal state, no secondary operations.

    manifest.params fields:
      - action: str, **Required**, "upload" | "download"
      - source_path: str, **Required for upload**. Local filepath on executor side (to be uploaded to target host)
      - remote_path: str, **Required**. Path on target host (upload target / download source)
      - timeout_seconds: int, Optional, default 300
    """
    node_type = "file_op"
    display_name = "File Transfer"
    category = "execution"
    requires_host = True
    is_asynchronous_human = False

    _ACTION_UPLOAD = "upload"
    _VALID_ACTIONS = (_ACTION_UPLOAD,)

    def validate_config(self, params, *, secrets_mask=None):
        p = _normalize_frontend_params(params)
        r = ValidationResult.success()
        action = p.get("action")
        if action not in self._VALID_ACTIONS:
            r.add_error(
                "/params/action",
                f"E0304: action must be upload, currently {action!r}",
            )
            return r

        remote_path = p.get("remote_path")
        if not isinstance(remote_path, str) or not remote_path.strip():
            r.add_error("/params/remote_path", "E0304: remote_path is required and cannot be empty")

        source_type = p.get("source_type") or "local_path"
        if source_type == "http_url":
            valid_protocols = ("http", "https", "ftp", "ftps", "sftp")
            remote_sources = p.get("remote_sources")
            if not isinstance(remote_sources, list) or not remote_sources:
                r.add_error("/params/remote_sources", "E0304: remote_sources requires at least one link")
            else:
                for i, rs in enumerate(remote_sources):
                    if not isinstance(rs, dict):
                        r.add_error(f"/params/remote_sources[{i}]", "E0304: remote source must be an object")
                        continue
                    url = rs.get("url")
                    if not isinstance(url, str) or not url.strip():
                        r.add_error(f"/params/remote_sources[{i}].url", "E0304: url is required")
                    proto = rs.get("protocol", "https")
                    if proto not in valid_protocols:
                        r.add_error(
                            f"/params/remote_sources[{i}].protocol",
                            f"E0304: protocol must be in {list(valid_protocols)}, currently {proto!r}",
                        )
                    if proto in ("ftp", "ftps", "sftp"):
                        un = rs.get("username")
                        pw = rs.get("password")
                        if not isinstance(un, str) or not un.strip():
                            r.add_error(f"/params/remote_sources[{i}].username", f"E0304: username is required under {proto} protocol")
                        if not isinstance(pw, str) or pw == "":
                            r.add_error(f"/params/remote_sources[{i}].password", f"E0304: password is required under {proto} protocol")
        else:
            src = p.get("source_path")
            if not isinstance(src, str) or not src.strip():
                r.add_error("/params/source_path", "E0304: source_path is required when source_type=local_path")

        timeout = p.get("timeout_seconds")
        if timeout is not None:
            if not isinstance(timeout, int) or timeout <= 0 or timeout > 86400 * 7:
                r.add_error("/params/timeout_seconds", "E0304: timeout_seconds must be an integer in (0, 604800]")
        return r

    def validate_and_render(
        self,
        params: dict[str, Any],
        context: WorkflowContext,
        *,
        secrets_mask: list[str] | None = None,
    ) -> dict[str, Any]:
        return context.render_structure(_normalize_frontend_params(params))

    def dispatch(self, cfg: RenderedNodeConfig) -> UnitOutput:
        import os

        if cfg.host_id is None or cfg.host_id == NO_HOST_SENTINEL:
            return UnitOutput(
                status=STATUS_FAILED,
                output={},
                exit_code=1,
                error_message="E0301: file_op requires_host=True, but current node did not resolve to a specific host_id",
            )

        from taurus.models import Host, OpsExecution
        from dvadmin.system.models import Users

        host = Host.objects.filter(pk=cfg.host_id).first()
        if host is None:
            return UnitOutput(
                status=STATUS_FAILED,
                output={},
                exit_code=1,
                error_message=f"E0303: host_id={cfg.host_id!r} does not exist in Host table",
            )

        user: Any = None
        if getattr(cfg, "user_id", None):
            try:
                user = Users.objects.filter(pk=cfg.user_id).first()
            except Exception:  # noqa: BLE001
                user = None

        # Idempotency: if same dispatch_id already has a terminal OpsExecution row, return directly
        existing = OpsExecution.objects.filter(batch_id=str(cfg.dispatch_id)).first()
        if existing is not None and existing.status in (_OPS_SUCCESS, _OPS_FAILED, _OPS_ABORTED):
            return self._terminal_output_from_ops(existing)

        action = cfg.params.get("action")
        timeout = int(cfg.params.get("timeout_seconds") or 300)
        file_paths_list = cfg.params.get("file_paths_list") or []

        if not file_paths_list:
            source_path = str(cfg.params.get("source_path") or "")
            remote_path = str(cfg.params.get("remote_path") or "")
            if source_path or remote_path:
                file_paths_list = [{"source_path": source_path, "remote_path": remote_path}]

        if not file_paths_list:
            return UnitOutput(
                status=STATUS_FAILED,
                output={},
                exit_code=1,
                error_message="E0313: No file paths to process",
            )

        # Process remote URL sources (supports multiple remote links)
        source_type = cfg.params.get("source_type") or "local_path"
        temp_downloaded_map: dict[int, str] = {}  # index -> tmp_path
        if action == self._ACTION_UPLOAD and source_type == "http_url":
            remote_sources = cfg.params.get("remote_sources")
            if not isinstance(remote_sources, list) or not remote_sources:
                return UnitOutput(
                    status=STATUS_FAILED,
                    output={},
                    exit_code=1,
                    error_message="E0312: remote_sources is empty",
                )
            for idx, rs in enumerate(remote_sources):
                if not isinstance(rs, dict):
                    continue
                url = str(rs.get("url") or "")
                if not url.strip():
                    continue
                protocol = str(rs.get("protocol") or "https")
                username = rs.get("username")
                password = rs.get("password")
                download_res = self._download_source_to_temp(
                    protocol=protocol,
                    source_url=url,
                    timeout=timeout,
                    username=username if isinstance(username, str) else None,
                    password=password if isinstance(password, str) else None,
                )
                if not download_res["success"]:
                    return UnitOutput(
                        status=STATUS_FAILED,
                        output={},
                        exit_code=1,
                        error_message=f"Failed to download remote link #{idx + 1}: {download_res['error']}",
                    )
                temp_downloaded_map[idx] = download_res["tmp_path"]

        # Process file paths one by one
        transfer_results = []
        total_bytes = 0
        all_success = True

        for file_pair in file_paths_list:
            src = str(file_pair.get("source_path", "") or "")
            dst = str(file_pair.get("remote_path", "") or "")

            # Remote source: src is the remote source index (stringified number), find from map
            effective_source = src
            if source_type == "http_url" and temp_downloaded_map:
                try:
                    rs_idx = int(src)
                    if rs_idx in temp_downloaded_map:
                        effective_source = temp_downloaded_map[rs_idx]
                    elif src:
                        effective_source = src
                except (ValueError, TypeError):
                    pass

            if not dst and not effective_source and src:
                dst = src.split("/")[-1] if "/" in src else src
            if not dst:
                transfer_results.append({"source": src, "target": dst, "success": False, "error": "Target path is empty"})
                all_success = False
                continue

            if not effective_source:
                transfer_results.append({"source": src, "target": dst, "success": False, "error": "Source path is empty"})
                all_success = False
                continue

            try:
                result = self._do_transfer(host, "upload", effective_source, dst, timeout)
                if result.get("success"):
                    total_bytes += int(result.get("file_size", 0) or 0)
                    transfer_results.append({"source": src, "target": dst, "success": True, "file_size": result.get("file_size", 0)})
                else:
                    all_success = False
                    transfer_results.append({"source": src, "target": dst, "success": False, "error": result.get("error", "Transfer failed")})
            except Exception as exc:  # noqa: BLE001
                all_success = False
                transfer_results.append({"source": src, "target": dst, "success": False, "error": str(exc)})

        # Clean up temporary files from remote downloads
        for tmp_path in temp_downloaded_map.values():
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:  # noqa: BLE001
                    pass

        # Create OpsExecution record
        ops_kwargs: dict[str, Any] = {
            "execution_type": action,
            "host": host,
            "user": user,
            "file_path": str(file_paths_list[0].get("remote_path", "") or "") if file_paths_list else "",
            "timeout_seconds": timeout,
            "status": _OPS_SUCCESS if all_success else _OPS_FAILED,
            "batch_id": str(cfg.dispatch_id),
            "file_size": total_bytes,
        }

        if not all_success:
            failed = [r for r in transfer_results if not r.get("success")]
            ops_kwargs["error_message"] = "; ".join(r.get("error", "Unknown error") for r in failed[:5])

        try:
            ops = OpsExecution.objects.create(
                execution_id=str(cfg.dispatch_id)[:56] + "-" + str(host.pk)[:7],
                **ops_kwargs,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[wf-adapter] Failed to create OpsExecution(file_op) dispatch_id=%s", cfg.dispatch_id)
            return UnitOutput(
                status=STATUS_FAILED,
                output={},
                exit_code=1,
                error_message=f"E0310: Failed to create OpsExecution {type(exc).__name__}: {exc}",
            )

        return self._terminal_output_from_ops(ops)

    def poll(self, cfg, adapter_state):
        # file_op already reached terminal state during dispatch, poll only needs to verify
        from taurus.models import OpsExecution
        ops = OpsExecution.objects.filter(batch_id=str(cfg.dispatch_id)).first()
        if ops is None:
            return UnitOutput(
                status=STATUS_FAILED,
                output={},
                exit_code=1,
                error_message="E0311: poll cannot find the corresponding OpsExecution row",
            )
        return self._terminal_output_from_ops(ops, from_poll=True)

    def cancel(self, cfg, adapter_state):
        from taurus.models import OpsExecution
        ops = OpsExecution.objects.filter(batch_id=str(cfg.dispatch_id)).first()
        if ops is None:
            return UnitOutput(status=STATUS_CANCELLED, output={"cancelled": True, "noop": True})
        if ops.status in (_OPS_SUCCESS, _OPS_FAILED, _OPS_ABORTED):
            return self._terminal_output_from_ops(ops)
        ops.status = _OPS_ABORTED
        ops.error_message = "E4002: Workflow-level cancel (file transfer interrupted)"
        from django.utils import timezone
        ops.finished_at = timezone.now()
        ops.save(update_fields=["status", "error_message", "finished_at", "update_datetime"])
        return UnitOutput(status=STATUS_CANCELLED, output={"cancelled": True})

    # ---- internal helpers ----
    def _parse_host_path(self, protocol: str, source_url: str) -> tuple[str, int, str]:
        """Parse (host, port, path) from source_url.

        source_url allows two formats:
          1) hostname[:port]/remote/path  —— no protocol prefix (user selects protocol in dropdown)
          2) scheme://[user[:pass]@]hostname[:port]/path  —— full URL (supports direct HTTP/HTTPS paste)
        return (host, port, path)
        """
        import os
        from urllib.parse import urlparse, unquote

        url = source_url.strip()
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            # Full URL format
            host = parsed.hostname or ""
            port = parsed.port or 0
            path = unquote(parsed.path) or "/"
            if protocol in ("ftp", "ftps") and port == 0:
                port = 21
            elif protocol == "sftp" and port == 0:
                port = 22
            elif protocol in ("http",) and port == 0:
                port = 80
            elif protocol in ("https",) and port == 0:
                port = 443
            return host, port, path

        # Short format (no protocol prefix): host[:port]/path
        if "//" in url:
            url = url.split("//", 1)[1]
        if "/" in url:
            host_port, _, path = url.partition("/")
            path = "/" + path
        else:
            host_port = url
            path = "/"
        host_port = host_port.strip()
        if host_port.startswith("["):
            # IPv6
            end = host_port.find("]")
            if end > 0:
                host = host_port[1:end]
                rest = host_port[end + 1 :]
                port = int(rest[1:]) if rest.startswith(":") and rest[1:].isdigit() else 0
            else:
                host = host_port
                port = 0
        elif ":" in host_port and host_port.count(":") == 1:
            h, p = host_port.split(":", 1)
            host = h
            port = int(p) if p.isdigit() else 0
        else:
            host = host_port
            port = 0

        if port == 0:
            if protocol in ("ftp", "ftps"):
                port = 21
            elif protocol == "sftp":
                port = 22
            elif protocol == "http":
                port = 80
            elif protocol == "https":
                port = 443
        # Normalize path: remove trailing slashes, but keep file name
        if path and path != "/" and not os.path.basename(path):
            path = path.rstrip("/") or "/"
        return host, port, path

    def _download_source_to_temp(
        self,
        *,
        protocol: str,
        source_url: str,
        timeout: int,
        username: str | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        """Multi-protocol download: http / https / ftp / ftps / sftp to backend temporary file.

        return {"success": bool, "tmp_path": str | None, "error": str | None}
        """
        import os
        import socket
        import tempfile

        from django.conf import settings

        # Size limit: settings.FILE_OP_URL_MAX_BYTES configurable, default 1GB
        max_bytes = int(getattr(settings, "FILE_OP_URL_MAX_BYTES", 1024 * 1024 * 1024))
        chunk_bytes = 64 * 1024
        tmp_path: str | None = None

        def _succeed(p: str) -> dict[str, Any]:
            return {"success": True, "tmp_path": p, "error": None}

        def _fail(msg: str) -> dict[str, Any]:
            nonlocal tmp_path
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:  # noqa: BLE001
                    pass
                tmp_path = None
            return {"success": False, "tmp_path": None, "error": msg}

        try:
            host, port, path = self._parse_host_path(protocol, source_url)
            if not host:
                return _fail("E0312: source_url missing host")

            filename = os.path.basename(path) or "download.bin"
            with tempfile.NamedTemporaryFile(delete=False, suffix="_" + filename) as tmp:
                tmp_path = tmp.name
                tmp_fh = tmp

                if protocol in ("http", "https"):
                    import requests

                    if "://" not in source_url:
                        full_url = f"{protocol}://{host}"
                        if (protocol == "http" and port != 80) or (protocol == "https" and port != 443):
                            full_url += f":{port}"
                        full_url += path
                    else:
                        full_url = source_url
                    auth = None
                    if username and password:
                        auth = (username, password)
                    resp = requests.get(
                        full_url,
                        stream=True,
                        timeout=(5, timeout),
                        allow_redirects=True,
                        auth=auth,
                    )
                    if resp.status_code != 200:
                        return _fail(f"E0312: HTTP {resp.status_code} download failed")
                    read_bytes = 0
                    for chunk in resp.iter_content(chunk_size=chunk_bytes):
                        if not chunk:
                            continue
                        read_bytes += len(chunk)
                        if read_bytes > max_bytes:
                            return _fail(f"E0312: download exceeds size limit {max_bytes} bytes")
                        tmp_fh.write(chunk)
                    return _succeed(tmp_path)

                if protocol in ("ftp", "ftps"):
                    from ftplib import FTP, FTP_TLS

                    if protocol == "ftps":
                        ftp = FTP_TLS()
                        ftp.connect(host, port, timeout=min(timeout, 30))
                        ftp.login(username or "anonymous", password or "anonymous@")
                        try:
                            ftp.prot_p()  # Enter data channel encryption mode
                        except Exception:
                            pass
                    else:
                        ftp = FTP()
                        ftp.connect(host, port, timeout=min(timeout, 30))
                        ftp.login(username or "anonymous", password or "anonymous@")
                    try:
                        # Change to directory (if path contains directory)
                        dir_part = os.path.dirname(path)
                        fname = os.path.basename(path)
                        if dir_part and dir_part != "/":
                            ftp.cwd(dir_part)
                        read_bytes = 0

                        def _write_block(block: bytes) -> None:
                            nonlocal read_bytes
                            if not block:
                                return
                            read_bytes += len(block)
                            if read_bytes > max_bytes:
                                raise OverflowError(f"E0312: download exceeds size limit {max_bytes} bytes")
                            tmp_fh.write(block)

                        ftp.retrbinary(f"RETR {fname}", _write_block, blocksize=chunk_bytes)
                    finally:
                        try:
                            ftp.quit()
                        except Exception:
                            ftp.close()
                    return _succeed(tmp_path)

                if protocol == "sftp":
                    import paramiko

                    sock_timeout = min(timeout, 30)
                    transport = paramiko.Transport((host, port or 22))
                    transport.banner_timeout = sock_timeout
                    transport.auth_timeout = sock_timeout
                    transport.connect_timeout = sock_timeout
                    try:
                        transport.connect(
                            username=username or "",
                            password=password or "",
                        )
                        sftp = paramiko.SFTPClient.from_transport(transport)
                        if sftp is None:
                            return _fail("E0312: SFTP session creation failed")
                        try:
                            with sftp.file(path, "rb") as sftp_fh:
                                sftp_fh.prefetch()
                                read_bytes = 0
                                while True:
                                    data = sftp_fh.read(chunk_bytes)
                                    if not data:
                                        break
                                    read_bytes += len(data)
                                    if read_bytes > max_bytes:
                                        return _fail(f"E0312: download exceeds size limit {max_bytes} bytes")
                                    tmp_fh.write(data)
                        finally:
                            sftp.close()
                    finally:
                        try:
                            transport.close()
                        except Exception:
                            pass
                    return _succeed(tmp_path)

                return _fail(f"E0312: Unsupported protocol {protocol!r}")
        except socket.timeout as exc:
            return _fail(f"E0312: Connection/download timeout {type(exc).__name__}: {exc}")
        except OverflowError as exc:
            return _fail(str(exc))
        except Exception as exc:  # noqa: BLE001
            return _fail(f"E0312: {type(exc).__name__}: {exc}")

    def _do_transfer(
        self,
        host: Any,
        action: str,
        source_path: str,
        remote_path: str,
        timeout: int,
    ) -> dict[str, Any]:
        """Synchronous execution of file upload/download via TaurusClient SDK."""
        import asyncio
        import os

        from django.conf import settings

        try:
            from taurus.sdk import TaurusClient
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"E3003: SDK import failed {exc}", "file_size": 0}

        host_ip = getattr(host, "host_ip", None) or "localhost"
        address = f"{host_ip}:50051"
        client_kwargs: dict[str, Any] = {
            "connect_timeout": min(timeout, int(getattr(settings, "EXECUTOR_CONNECT_TIMEOUT", 5)) or 5),
        }
        sdk_cert_dir = getattr(settings, "SDK_CERT_DIR", None)
        if sdk_cert_dir:
            client_kwargs["cert_file"] = os.path.join(sdk_cert_dir, "client.crt")
            client_kwargs["key_file"] = os.path.join(sdk_cert_dir, "client.key")
            client_kwargs["ca_file"] = os.path.join(sdk_cert_dir, "ca.crt")

        async def _run() -> dict[str, Any]:
            async with TaurusClient(address, **client_kwargs) as client:
                if action == "upload":
                    return await client.upload_file(source_path, remote_path)
                else:
                    tmp_path = None
                    try:
                        suffix = os.path.basename(remote_path)
                        import tempfile
                        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                            tmp_path = tmp.name
                        res = await client.download_file(remote_path, tmp_path)
                        if os.path.exists(tmp_path):
                            res["file_size"] = os.path.getsize(tmp_path)
                        return res
                    finally:
                        if tmp_path and os.path.exists(tmp_path):
                            os.unlink(tmp_path)

        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(asyncio.run, _run()).result()
            else:
                result = asyncio.run(_run())
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"E3003: {type(exc).__name__}: {exc}", "file_size": 0}

        if not isinstance(result, dict):
            return {"success": False, "error": "E3003: SDK returned non-dict", "file_size": 0}
        if result.get("success"):
            return {"success": True, "file_size": int(result.get("file_size", 0) or 0)}
        return {"success": False, "error": result.get("error") or result.get("message") or "E3003: Transfer failed", "file_size": 0}

    def _terminal_output_from_ops(self, ops: Any, *, from_poll: bool = False) -> UnitOutput:
        output: dict[str, Any] = {
            "ops_execution_id": ops.execution_id,
            "file_path": ops.file_path,
            "file_size": ops.file_size or 0,
            "exit_code": ops.exit_code,
        }
        if ops.started_at and ops.finished_at:
            output["duration_ms"] = int((ops.finished_at - ops.started_at).total_seconds() * 1000)
        if ops.status == _OPS_SUCCESS:
            return UnitOutput(status=STATUS_SUCCESS, output=output, exit_code=0)
        if ops.status == _OPS_ABORTED:
            reason = ops.error_message or ""
            if "workflow-level cancel" in reason.lower() or "cancel" in reason.lower():
                return UnitOutput(status=STATUS_CANCELLED, output=output, exit_code=0)
            return UnitOutput(
                status=STATUS_FAILED,
                output=output,
                exit_code=1,
                error_message=f"E4001: {reason}",
            )
        # FAILED or other
        msg = ops.error_message or "E3003: File transfer failed"
        if not msg.startswith("E"):
            msg = f"E3003: {msg}"
        return UnitOutput(status=STATUS_FAILED, output=output, exit_code=ops.exit_code or 1, error_message=msg)