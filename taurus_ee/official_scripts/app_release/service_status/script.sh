#!/bin/bash
# Function: 服务状态查询脚本
# Args: services - 服务名称列表
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 低危
# Official built-in script v1.0.0

set -euo pipefail

services=${services:-nginx,sshd}

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_warn()  { echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

log_info "开始查询服务状态"

failed_count=0
IFS=',' read -ra SVC_ARRAY <<< "$services"

for svc in "${SVC_ARRAY[@]}"; do
    svc=$(echo "$svc" | xargs)
    log_info "--- ${svc} ---"
    
    if systemctl list-unit-files | grep -q "^${svc}.service"; then
        active_status=$(systemctl is-active "$svc" 2>/dev/null || echo "unknown")
        enabled_status=$(systemctl is-enabled "$svc" 2>/dev/null || echo "unknown")
        log_info "运行状态: ${active_status}"
        log_info "开机自启: ${enabled_status}"
        
        if [ "$active_status" != "active" ]; then
            log_warn "服务未运行"
            failed_count=$((failed_count + 1))
        fi
        
        log_info "最近日志:"
        journalctl -u "$svc" -n 5 --no-pager 2>/dev/null || true
    else
        log_warn "服务 ${svc} 不存在"
        failed_count=$((failed_count + 1))
    fi
done

if [ "$failed_count" -gt 0 ]; then
    log_error "查询完成，${failed_count} 个服务异常"
    exit 1
else
    log_info "查询完成，所有服务正常"
    exit 0
fi
