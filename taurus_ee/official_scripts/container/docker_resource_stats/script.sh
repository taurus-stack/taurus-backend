#!/bin/bash
# Function: Docker资源使用统计脚本
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 低危
# Official built-in script v1.0.0

set -euo pipefail

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

if ! command -v docker >/dev/null 2>&1; then
    log_error "Docker未安装"
    exit 1
fi

log_info "========== Docker资源统计 =========="

log_info "--- Docker版本 ---"
docker --version

log_info "--- 容器概览 ---"
running_count=$(docker ps -q | wc -l)
total_count=$(docker ps -aq | wc -l)
log_info "运行中容器: ${running_count}，总容器数: ${total_count}"

log_info "--- 镜像概览 ---"
image_count=$(docker images -q | wc -l)
log_info "镜像数量: ${image_count}"

log_info "--- 容器资源使用Top 10 ---"
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}" 2>/dev/null | head -11 || true

log_info "--- 磁盘使用 ---"
docker system df 2>/dev/null || true

log_info "--- 异常退出容器 ---"
exited_count=$(docker ps -f status=exited -q | wc -l)
log_info "已退出容器数: ${exited_count}"
if [ "$exited_count" -gt 0 ]; then
    docker ps -f status=exited --format "table {{.Names}}\t{{.Status}}" | head -11
fi

log_info "========== 统计完成 =========="
exit 0
