#!/bin/bash
# Function: Nginx配置检查脚本
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 低危
# Official built-in script v1.0.0

set -euo pipefail

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_warn()  { echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

if ! command -v nginx >/dev/null 2>&1; then
    log_error "Nginx未安装"
    exit 1
fi

log_info "--- Nginx版本 ---"
nginx -v 2>&1

log_info "--- 配置文件语法检查 ---"
if nginx -t 2>&1; then
    log_info "配置文件语法正确"
else
    log_error "配置文件语法错误"
    exit 1
fi

log_info "--- 配置文件路径 ---"
nginx_conf=$(nginx -V 2>&1 | grep -oP 'conf-path=\K[^ ]+' || echo "/etc/nginx/nginx.conf")
log_info "主配置文件: ${nginx_conf}"

log_info "--- 监听端口 ---"
nginx -T 2>/dev/null | grep "listen " | grep -v "#" | sed 's/^ *//' | sort | uniq || true

log_info "--- 虚拟主机 ---"
nginx -T 2>/dev/null | grep "server_name " | grep -v "#" | sed 's/^ *//' | sort | uniq || true

log_info "--- 运行状态 ---"
if pgrep -x nginx >/dev/null 2>&1; then
    log_info "Nginx运行中"
    worker_count=$(pgrep -x nginx | wc -l)
    log_info "进程数: ${worker_count}"
else
    log_warn "Nginx未运行"
fi

log_info "Nginx配置检查完成"
exit 0
