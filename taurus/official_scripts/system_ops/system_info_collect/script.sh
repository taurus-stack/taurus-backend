#!/bin/bash
# Function: 系统信息采集脚本
# Supported: CentOS 7+/Ubuntu 18.04+/Debian 10+
# Risk Level: 低危
# Official built-in script v1.0.0

set -euo pipefail

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }

log_info "========== 系统信息采集开始 =========="

log_info "--- 主机名 ---"
hostname

log_info "--- 操作系统 ---"
if [ -f /etc/os-release ]; then
    cat /etc/os-release | head -5
elif [ -f /etc/redhat-release ]; then
    cat /etc/redhat-release
fi

log_info "--- 内核版本 ---"
uname -r

log_info "--- CPU信息 ---"
echo "CPU型号: $(cat /proc/cpuinfo | grep "model name" | head -1 | cut -d: -f2 | xargs)"
echo "CPU核心数: $(nproc)"
echo "CPU物理核数: $(cat /proc/cpuinfo | grep "physical id" | sort | uniq | wc -l)"

log_info "--- 内存信息 ---"
free -h

log_info "--- 磁盘信息 ---"
df -h | grep -v tmpfs

log_info "--- 网络信息 ---"
hostname -I 2>/dev/null || ip addr show | grep "inet " | awk '{print $2}'
echo "主机名: $(hostname)"

log_info "--- 运行时间 ---"
uptime

log_info "--- 已安装服务 ---"
systemctl list-unit-files --type=service --state=enabled 2>/dev/null | head -20 || echo "无法获取服务列表"

log_info "========== 系统信息采集完成 =========="
exit 0
