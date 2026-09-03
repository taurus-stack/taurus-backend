#!/bin/bash
# Function: 磁盘使用率巡检脚本
# Args: threshold(默认85) - 告警阈值百分比
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 低危
# Official built-in script v1.0.0

set -euo pipefail

threshold=${threshold:-85}

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_warn()  { echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

log_info "开始磁盘使用率巡检，告警阈值: ${threshold}%"

abnormal_count=0
while read -r usage mount; do
    usage_num=${usage%\%}
    if [ "$usage_num" -ge "$threshold" ]; then
        log_warn "磁盘异常: ${mount} 使用率 ${usage} 超过阈值 ${threshold}%"
        abnormal_count=$((abnormal_count + 1))
    else
        log_info "磁盘正常: ${mount} 使用率 ${usage}"
    fi
done < <(df -h | awk 'NR>1 && $1 !~ /tmpfs|devtmpfs/ {print $5, $6}')

if [ "$abnormal_count" -gt 0 ]; then
    log_error "巡检完成，共发现 ${abnormal_count} 个磁盘异常"
    exit 1
else
    log_info "巡检完成，所有磁盘使用率正常"
    exit 0
fi
