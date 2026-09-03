#!/bin/bash
# Function: 进程存活检测脚本
# Args: process_names - 进程名称列表
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 低危
# Official built-in script v1.0.0

set -euo pipefail

process_names=${process_names:-nginx,sshd}

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_warn()  { echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

log_info "开始进程存活检测"

failed_count=0
IFS=',' read -ra PROC_ARRAY <<< "$process_names"

for proc_name in "${PROC_ARRAY[@]}"; do
    proc_name=$(echo "$proc_name" | xargs)
    pids=$(pgrep -f "$proc_name" || true)
    if [ -n "$pids" ]; then
        pid_count=$(echo "$pids" | wc -l)
        log_info "进程 ${proc_name} 正常，运行中 ${pid_count} 个实例"
    else
        log_warn "进程 ${proc_name} 未运行"
        failed_count=$((failed_count + 1))
    fi
done

if [ "$failed_count" -gt 0 ]; then
    log_error "检测完成，${failed_count} 个进程异常"
    exit 1
else
    log_info "检测完成，所有进程正常"
    exit 0
fi
