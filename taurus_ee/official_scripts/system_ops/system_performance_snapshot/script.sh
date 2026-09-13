#!/bin/bash
# Function: 系统性能快照脚本
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 低危
# Official built-in script v1.0.0

set -euo pipefail

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }

log_info "========== 系统性能快照 =========="
log_info "采集时间: $(date)"

log_info "--- 系统负载 ---"
uptime

log_info "--- CPU使用率 ---"
if command -v mpstat >/dev/null 2>&1; then
    mpstat 1 1 | tail -3
else
    top -bn1 | grep "Cpu(s)"
fi

log_info "--- 内存使用 ---"
free -h

log_info "--- 磁盘使用 ---"
df -h | grep -v tmpfs

log_info "--- 磁盘IO ---"
if command -v iostat >/dev/null 2>&1; then
    iostat -x 1 1 2>/dev/null | tail -20
else
    log_info "iostat未安装"
fi

log_info "--- 网络连接 ---"
ss -s 2>/dev/null || netstat -s 2>/dev/null || true

log_info "--- Top 10 CPU进程 ---"
ps aux --sort=-%cpu | head -11

log_info "--- Top 10 内存进程 ---"
ps aux --sort=-%mem | head -11

log_info "--- 僵尸进程 ---"
zombie_count=$(ps aux | awk '$8=="Z" {count++} END {print count+0}')
log_info "僵尸进程数: ${zombie_count}"

log_info "--- 打开文件数 ---"
open_files=$(lsof 2>/dev/null | wc -l || echo "unknown")
log_info "系统打开文件数: ${open_files}"

log_info "========== 快照采集完成 =========="
exit 0
