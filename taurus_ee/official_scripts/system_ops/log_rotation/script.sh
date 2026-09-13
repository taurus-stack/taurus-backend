#!/bin/bash
# Function: 系统日志轮转脚本
# Args: force - 是否强制执行
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 低危
# Official built-in script v1.0.0

set -euo pipefail

force=${force:-false}

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

log_info "开始系统日志轮转"

if command -v logrotate >/dev/null 2>&1; then
    if [ "$force" = "true" ]; then
        log_info "强制执行日志轮转"
        logrotate -f /etc/logrotate.conf
    else
        logrotate /etc/logrotate.conf
    fi
    log_info "日志轮转完成"
    exit 0
else
    log_error "logrotate 未安装"
    exit 1
fi
