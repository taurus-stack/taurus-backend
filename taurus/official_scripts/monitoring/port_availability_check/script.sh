#!/bin/bash
# Function: 端口可用性检测脚本
# Args: host - 目标主机，ports - 端口列表，timeout - 超时时间
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 低危
# Official built-in script v1.0.0

set -euo pipefail

host=${host:-127.0.0.1}
ports=${ports:-22,80,443}
timeout=${timeout:-3}

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_warn()  { echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

log_info "开始端口检测，目标主机: ${host}"

failed_count=0
IFS=',' read -ra PORT_ARRAY <<< "$ports"

for port in "${PORT_ARRAY[@]}"; do
    port=$(echo "$port" | xargs)
    if timeout "$timeout" bash -c "echo > /dev/tcp/${host}/${port}" 2>/dev/null; then
        log_info "端口 ${port} 正常"
    else
        log_warn "端口 ${port} 无法连接"
        failed_count=$((failed_count + 1))
    fi
done

if [ "$failed_count" -gt 0 ]; then
    log_error "检测完成，${failed_count} 个端口异常"
    exit 1
else
    log_info "检测完成，所有端口正常"
    exit 0
fi
