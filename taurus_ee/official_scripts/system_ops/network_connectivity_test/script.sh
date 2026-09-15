#!/bin/bash
# Function: 网络连通性测试脚本
# Args: target - 目标地址，port - 测试端口
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 低危
# Official built-in script v1.0.0

set -euo pipefail

target=${target:-www.baidu.com}
port=${port:-80}

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_warn()  { echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

log_info "========== 网络连通性测试 =========="
log_info "目标: ${target}:${port}"

log_info "--- DNS解析 ---"
if command -v nslookup >/dev/null 2>&1; then
    nslookup "$target" 2>/dev/null || log_warn "DNS解析失败"
elif command -v dig >/dev/null 2>&1; then
    dig "$target" +short 2>/dev/null || log_warn "DNS解析失败"
else
    log_info "未安装nslookup/dig，跳过DNS详细解析"
fi

log_info "--- Ping测试 ---"
if ping -c 4 -W 2 "$target" 2>/dev/null; then
    log_info "Ping测试通过"
else
    log_warn "Ping测试失败或超时"
fi

log_info "--- 端口连通性 ---"
if timeout 3 bash -c "echo > /dev/tcp/${target}/${port}" 2>/dev/null; then
    log_info "端口 ${port} 连通正常"
else
    log_warn "端口 ${port} 无法连接"
fi

log_info "--- 路由追踪 ---"
if command -v traceroute >/dev/null 2>&1; then
    traceroute -m 15 -w 2 "$target" 2>/dev/null || true
elif command -v mtr >/dev/null 2>&1; then
    mtr --report -c 5 "$target" 2>/dev/null || true
else
    log_info "未安装traceroute/mtr，跳过路由追踪"
fi

log_info "========== 测试完成 =========="
exit 0
