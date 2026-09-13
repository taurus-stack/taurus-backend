#!/bin/bash
# Function: MySQL连接数巡检脚本
# Args: mysql_host, mysql_port, mysql_user, mysql_password
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 低危
# Official built-in script v1.0.0

set -euo pipefail

mysql_host=${mysql_host:-127.0.0.1}
mysql_port=${mysql_port:-3306}
mysql_user=${mysql_user:-root}
mysql_password=${mysql_password:-}

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_warn()  { echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

MYSQL_CMD="mysql -h${mysql_host} -P${mysql_port} -u${mysql_user}"
if [ -n "$mysql_password" ]; then
    MYSQL_CMD="${MYSQL_CMD} -p${mysql_password}"
fi

log_info "开始MySQL巡检，主机: ${mysql_host}:${mysql_port}"

if ! ${MYSQL_CMD} -e "SELECT 1;" >/dev/null 2>&1; then
    log_error "无法连接到MySQL数据库"
    exit 1
fi

log_info "--- 连接数 ---"
${MYSQL_CMD} -e "SHOW STATUS LIKE 'Threads_connected';"
${MYSQL_CMD} -e "SHOW VARIABLES LIKE 'max_connections';"

threads_connected=$(${MYSQL_CMD} -N -e "SHOW STATUS LIKE 'Threads_connected';" | awk '{print $2}')
max_connections=$(${MYSQL_CMD} -N -e "SHOW VARIABLES LIKE 'max_connections';" | awk '{print $2}')
conn_usage=$((threads_connected * 100 / max_connections))

log_info "连接使用率: ${conn_usage}% (${threads_connected}/${max_connections})"

log_info "--- 慢查询 ---"
${MYSQL_CMD} -e "SHOW VARIABLES LIKE 'slow_query%';"
${MYSQL_CMD} -e "SHOW STATUS LIKE 'Slow_queries';"

log_info "--- InnoDB状态 ---"
${MYSQL_CMD} -e "SHOW ENGINE INNODB STATUS\G" 2>/dev/null | head -50 || true

if [ "$conn_usage" -ge 80 ]; then
    log_warn "连接使用率超过80%，请关注"
fi

log_info "MySQL巡检完成"
exit 0
