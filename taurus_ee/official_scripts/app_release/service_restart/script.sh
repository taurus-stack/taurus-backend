#!/bin/bash
# Function: 服务重启脚本
# Args: service_name - 服务名称，check_port - 检测端口，wait_seconds - 等待时间
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 中危
# Official built-in script v1.0.0

set -euo pipefail

service_name=${service_name:-nginx}
check_port=${check_port:-}
wait_seconds=${wait_seconds:-10}

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_warn()  { echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

log_info "开始重启服务: ${service_name}"

if ! systemctl list-unit-files | grep -q "^${service_name}.service"; then
    log_error "服务 ${service_name} 不存在"
    exit 1
fi

old_status=$(systemctl is-active "$service_name" 2>/dev/null || echo "unknown")
log_info "重启前状态: ${old_status}"

log_info "执行重启..."
systemctl restart "$service_name"

log_info "等待 ${wait_seconds} 秒后检查状态..."
sleep "$wait_seconds"

new_status=$(systemctl is-active "$service_name" 2>/dev/null || echo "unknown")
log_info "重启后状态: ${new_status}"

if [ -n "$check_port" ]; then
    log_info "检测端口 ${check_port}..."
    if timeout 3 bash -c "echo > /dev/tcp/127.0.0.1/${check_port}" 2>/dev/null; then
        log_info "端口 ${check_port} 正常"
    else
        log_warn "端口 ${check_port} 无法连接"
    fi
fi

if [ "$new_status" = "active" ]; then
    log_info "服务重启成功"
    exit 0
else
    log_error "服务重启失败，当前状态: ${new_status}"
    log_info "服务日志:"
    journalctl -u "$service_name" -n 20 --no-pager 2>/dev/null || true
    exit 1
fi
