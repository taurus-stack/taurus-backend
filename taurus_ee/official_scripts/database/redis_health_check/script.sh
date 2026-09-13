#!/bin/bash
# Function: Redis健康检查脚本
# Args: redis_host, redis_port, redis_password
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 低危
# Official built-in script v1.0.0

set -euo pipefail

redis_host=${redis_host:-127.0.0.1}
redis_port=${redis_port:-6379}
redis_password=${redis_password:-}

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_warn()  { echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

REDIS_CMD="redis-cli -h ${redis_host} -p ${redis_port}"
if [ -n "$redis_password" ]; then
    REDIS_CMD="${REDIS_CMD} -a ${redis_password} --no-auth-warning"
fi

log_info "开始Redis健康检查，主机: ${redis_host}:${redis_port}"

if ! ${REDIS_CMD} ping >/dev/null 2>&1; then
    log_error "无法连接到Redis"
    exit 1
fi

pong=$(${REDIS_CMD} ping)
log_info "连接状态: ${pong}"

log_info "--- 服务器信息 ---"
${REDIS_CMD} info server | head -10

log_info "--- 内存使用 ---"
${REDIS_CMD} info memory | grep -E "used_memory_human|maxmemory_human|mem_fragmentation_ratio"

log_info "--- 统计信息 ---"
${REDIS_CMD} info stats | grep -E "total_connections_received|total_commands_processed|keyspace_hits|keyspace_misses"

log_info "--- 客户端连接 ---"
${REDIS_CMD} info clients | grep -E "connected_clients|blocked_clients"

log_info "--- 持久化 ---"
${REDIS_CMD} info persistence | grep -E "rdb_last_bgsave_status|aof_last_bgrewrite_status"

log_info "--- Key数量 ---"
${REDIS_CMD} info keyspace

log_info "Redis健康检查完成"
exit 0
