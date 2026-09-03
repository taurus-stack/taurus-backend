#!/bin/bash
# Function: 内存使用率巡检脚本
# Args: threshold(默认90) - 内存使用率告警阈值
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 低危
# Official built-in script v1.0.0

set -euo pipefail

threshold=${threshold:-90}

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_warn()  { echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

log_info "开始内存使用率巡检，告警阈值: ${threshold}%"

mem_total=$(free -m | awk 'NR==2 {print $2}')
mem_used=$(free -m | awk 'NR==2 {print $3}')
mem_usage=$((mem_used * 100 / mem_total))

swap_total=$(free -m | awk 'NR==3 {print $2}')
swap_used=$(free -m | awk 'NR==3 {print $3}')
if [ "$swap_total" -gt 0 ]; then
    swap_usage=$((swap_used * 100 / swap_total))
else
    swap_usage=0
fi

log_info "总内存: ${mem_total}MB"
log_info "已用内存: ${mem_used}MB (${mem_usage}%)"
log_info "Swap使用: ${swap_used}MB (${swap_usage}%)"

if [ "$mem_usage" -ge "$threshold" ]; then
    log_warn "内存使用率超过阈值 ${threshold}%"
    log_info "Top 5 内存占用进程:"
    ps aux --sort=-%mem | head -6 | awk '{printf "  %-10s %-5s %-5s %s\n", $1, $2, $4"%", $11}'
    log_error "内存巡检异常"
    exit 1
else
    log_info "内存使用率正常"
    exit 0
fi
