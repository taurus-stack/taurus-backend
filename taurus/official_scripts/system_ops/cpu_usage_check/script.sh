#!/bin/bash
# Function: CPU使用率巡检脚本
# Args: threshold(默认90) - CPU使用率告警阈值
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 低危
# Official built-in script v1.0.0

set -euo pipefail

threshold=${threshold:-90}

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_warn()  { echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

log_info "开始CPU使用率巡检，告警阈值: ${threshold}%"

cpu_idle=$(top -bn1 | grep "Cpu(s)" | awk '{print $8}' | cut -d. -f1)
cpu_usage=$((100 - cpu_idle))

load_1min=$(uptime | awk -F'load average: ' '{print $2}' | cut -d, -f1 | xargs)
cpu_cores=$(nproc)

log_info "CPU使用率: ${cpu_usage}%"
log_info "CPU核心数: ${cpu_cores}"
log_info "1分钟负载: ${load_1min}"

if [ "$cpu_usage" -ge "$threshold" ]; then
    log_warn "CPU使用率超过阈值 ${threshold}%"
    log_info "Top 5 CPU占用进程:"
    ps aux --sort=-%cpu | head -6 | awk '{printf "  %-10s %-5s %-5s %s\n", $1, $2, $3"%", $11}'
    log_error "CPU巡检异常"
    exit 1
else
    log_info "CPU使用率正常"
    exit 0
fi
