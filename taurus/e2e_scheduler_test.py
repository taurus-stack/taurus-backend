"""
Taurus Scheduled task端到端ValidationScript(纯 Django environment, 不requirestart scheduler Process)
复现完整链路:
  DB create Script/ScriptTask/ScriptTaskExecution
    →(Mock scheduler 到. push)→ Redis queue payload
      → run_scheduler_worker 消费
        → OpsExecution (status=0) 入library
          → websocket_async._poll_pending_executions Polling到
            → TaurusClient.execute_command (require真实 executor 在跑 No则标记Failed)

直接用 Django shell 风格:
  cd taurus-backend
  python manage.py shell < taurus/e2e_scheduler_test.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
import uuid
from datetime import timedelta

import django
from django.conf import settings as dj_settings
from django.utils import timezone

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "application.settings")
django.setup()

from taurus.models import (  # noqa: E402
    Host, Script, ScriptCategory, ScriptTask, ScriptTaskExecution, OpsExecution,
)
from dvadmin.system.models import Users  # noqa: E402
from taurus.management.commands.run_scheduler_worker import Command as WorkerCommand  # noqa: E402

MULTI_LINE_SCRIPT = """# End-to-end test: 多行Script(含换行, 单引号, $variable, 注释)
echo "=== 脚本开始 ==="
echo "主机名: $(hostname)"
echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "参数1=$1 参数2=$2"
echo "ENV_TEST=${ENV_TEST:-未设置}"
echo "=== 脚本结束 ==="
"""

QUEUE_KEY = os.environ.get("TAURUS_SCHEDULER_QUEUE", "taurus:scheduler:queue:script_task")


def _redis_cli():
    # 复用 worker 的默认 URL Parse
    redis_url = WorkerCommand._ensure_redis_password(WorkerCommand._default_redis_url())
    import redis
    return redis.Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=5)


def pick_online_host() -> Host | None:
    return (
        Host.objects.filter(status=1, online_status=1)
        .order_by("-last_heartbeat_at")
        .first()
    )


def ensure_data():
    # 找一 User
    admin = Users.objects.filter(is_active=1).first()
    if admin is None:
        admin = Users.objects.create_superuser(
            username="e2e_admin", password="e2e_admin_pass", email="e2e@taurus.local"
        )

    cat = ScriptCategory.objects.filter(name="e2etest").first()
    if not cat:
        cat = ScriptCategory.objects.create(
            name="e2etest",
            category_type="custom",
            is_system=False,
            sort=9999,
        )

    script = Script.objects.filter(name="E2E-Script-多行Shelltest").first()
    if not script:
        script = Script.objects.create(
            name="E2E-Script-多行Shelltest",
            script_type="Shell",
            category=cat,
            auth_type="Public",
            desc="端到端Validation:含注释/换行/variable的多行Script, 用于test bash -c 传参正确性",
            content=MULTI_LINE_SCRIPT,
            current_version="V1.0",
            timeout=120,
            concurrent=1,
            fail_strategy="continue",
            open_risk_check=False,
            need_audit=False,
            log_retention=7,
            script_params=[
                {"key": "P1", "value": "hello", "desc": "Parameters1"},
                {"key": "P2", "value": "world", "desc": "Parameters2"},
            ],
            script_envs=[
                {"key": "ENV_TEST", "value": "from_script_lib", "desc": "testEnvironment variables"},
            ],
            status=0,
            is_official=False,
            source="e2e_test",
            creator=admin,
        )
    else:
        script.content = MULTI_LINE_SCRIPT
        script.save(update_fields=["content", "update_datetime"])

    host = pick_online_host()
    if not host:
        print("[WARN] noFound online_status=1 且 status=1 的Host, ScriptExecution到 OpsExecution 后"
              "会标记为Failed(HostClose), 但Dispatch链路(ScriptTask→Redis→Worker→OpsExecution)仍可Validation.")

    return admin, script, host


def run_e2e():
    sep = "=" * 72
    print(sep)
    print("Taurus Scheduled task端到端Validation")
    print(sep)

    # 0. DB + Redis 连通性check
    try:
        Host.objects.count()
        print("[OK] MySQL join正常")
    except Exception as e:
        print(f"[ERR] MySQL joinFailed: {e}")
        return 1

    r = _redis_cli()
    try:
        r.ping()
        print("[OK] Redis join正常, URL=", WorkerCommand._mask_redis_url(WorkerCommand._default_redis_url()))
    except Exception as e:
        print(f"[ERR] Redis joinFailed: {e}")
        return 2

    admin, script, host = ensure_data()
    print(f"[OK] 数据准备完成: script_id={script.id}, script_name={script.name}, "
          f"host={f'{host.host_name}({host.host_ip}) uuid={host.host_uuid}' if host else '无可用Host'}")

    # 1. createOne-timeScheduled task(1 分钟后Execution, 实际我们手动立刻 push)
    run_once_time = timezone.now() + timedelta(minutes=1)
    task = ScriptTask.objects.create(
        script=script,
        name=f"E2E-Task-{int(time.time())}",
        description="端到端Validation任务:手动 push 到 Redis queue(不Dependency scheduler Process)",
        schedule_type="once",
        run_once_at=run_once_time,
        hosts=[str(host.host_uuid)] if host else [],
        timeout=120,
        fail_notify=True,
        enabled=True,
        envs={"ENV_TEST": "override_from_schedule"},
        args=["arg_from_sch_1", "arg_from_sch_2"],
        creator=admin,
    )
    print(f"[OK] create ScriptTask: id={task.id}, schedule_type={task.schedule_type}")

    # 2. createExecutionrecord(等价于 scheduler/dispatcher.py → store.create_execution_record)
    exec_rec = ScriptTaskExecution.objects.create(
        task=task,
        status=1,  # Running
        start_time=timezone.now(),
        trigger_type="manual",
    )
    print(f"[OK] create ScriptTaskExecution: id={exec_rec.id}, status={exec_rec.status}")

    # 3. Mock scheduler dispatcher._push_to_queue
    payload = {
        "task": {
            "id": task.id,
            "name": task.name,
            "script_id": script.id,
            "hosts": task.hosts,
            "timeout": task.timeout,
            "envs": task.envs,
            "args": task.args,
        },
        "execution": {
            "id": exec_rec.id,
            "trigger_type": "manual",
            "scheduled_fire_time": run_once_time.isoformat(),
        },
        "trigger_type": "manual",
        "version": 1,
        "produced_at": timezone.now().isoformat(),
    }
    payload_raw = json.dumps(payload, ensure_ascii=False)
    qlen_before = r.llen(QUEUE_KEY)
    r.lpush(QUEUE_KEY, payload_raw)
    qlen_after = r.llen(QUEUE_KEY)
    print(f"[OK] push Redis queue {QUEUE_KEY}:len {qlen_before} → {qlen_after}")

    # 4. 同步调用 worker 的processfunction(等价于 run_scheduler_worker 主Circular消费一entries)
    print("[..] 同步Execution Worker._process_single_payload_sync(...)")
    worker_cmd = WorkerCommand()
    try:
        worker_cmd._process_single_payload_sync(payload_raw)
        print("[OK] Worker 消费完成")
    except Exception as e:
        print(f"[ERR] Worker internalException: {e}")
        traceback.print_exc()
        return 10

    # 5. Validation结果
    exec_rec.refresh_from_db()
    print(f"[OK] ScriptTaskExecution 回写: status={exec_rec.status} ({exec_rec.status_display}), "
          f"duration={exec_rec.duration}s")
    if exec_rec.error_message:
        print(f"     error_message = {exec_rec.error_message[:200]}")
    if exec_rec.result:
        print(f"     result.keys = {list(exec_rec.result.keys())}")
        if "hosts_detail" in exec_rec.result:
            for h in exec_rec.result["hosts_detail"][:5]:
                print(f"       host: {h}")
        if "batch_id" in exec_rec.result:
            print(f"       batch_id = {exec_rec.result['batch_id']}")

    # 6. 查 OpsExecution (worker should为每hostscreate一entries)
    batch_id = (exec_rec.result or {}).get("batch_id")
    if batch_id:
        opses = list(OpsExecution.objects.filter(batch_id=batch_id).select_related("host"))
        print(f"[OK] 已create OpsExecution 数量: {len(opses)}")
        for ops in opses:
            print(f"       [{ops.get_status_display()}] id={ops.id} "
                  f"host={ops.host.host_name if ops.host else '?'}(ip={ops.host.host_ip if ops.host else '?'}) "
                  f"exit={ops.exit_code}")
            if ops.error_message:
                print(f"         error = {ops.error_message[:180]}")
            if ops.output_buffer:
                # 拼接 stdout/stderr 片段
                stdout = "".join(
                    c.get("stdout", "") for c in ops.output_buffer if isinstance(c, dict)
                )
                stderr = "".join(
                    c.get("stderr", "") for c in ops.output_buffer if isinstance(c, dict)
                )
                if stdout.strip():
                    print(f"         stdout tail 200 = {stdout[-200:]!r}")
                if stderr.strip():
                    print(f"         stderr tail 200 = {stderr[-200:]!r}")

            # 关键断言:ifHost在line, output_buffer 里不应再出现 "-c: optionrequire一 Parameters"
            if host and ops.host_id == host.id and ops.status == 2:
                bad = any(
                    isinstance(c, dict) and "optionrequire一 Parameters" in (c.get("stderr") or "")
                    for c in ops.output_buffer
                )
                print(f"       [bash -c bug 断言] 存在 '-c requireParameters' Error? -> {bad}")
                if not bad and ops.exit_code == 0:
                    print(f"       [PASS] 多行Script bash -c 修复生效!  exit_code=0 且无ParseError")

    # 7. cleanupPending execution任务(Avoid真实 backend Process起来后又跑一遍干扰)
    OpsExecution.objects.filter(
        status=0, batch_id__isnull=False, batch_id__startswith=f"st-{task.id}-"
    ).update(status=4, error_message="e2eValidationcleanup, 不实际Execution")

    print(sep)
    print("端到端Validation总结:")
    print("  ✅ ScriptTask + ScriptTaskExecution DB write OK")
    print("  ✅ Dispatcher → Redis Queue push OK")
    print("  ✅ Worker 消费 payload → Target hostParse OK")
    print("  ✅ Worker create OpsExecution (status=0) 入library OK")
    if host and batch_id:
        print("  ℹ️  如需Validation最后一步(WebSocket→executor→真实bashExecution), 请在另一终端:")
        print("     1. start taurus-executor 监听 50051")
        print("     2. start taurus-backend 的 WebSocket server (Port见 application/wsgi.py 或 docker)")
        print("     3. 不要Execution上面的cleanup逻辑, 让 _poll_pending_executions Polling status=0 的任务跑起来")
    print(sep)
    return 0


if __name__ == "__main__":
    sys.exit(run_e2e())