#!/bin/bash
# Function: Docker磁盘清理脚本
# Args: prune_all - 是否清理所有未使用资源
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 中危
# Official built-in script v1.0.0

set -euo pipefail

prune_all=${prune_all:-false}

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

if ! command -v docker >/dev/null 2>&1; then
    log_error "Docker未安装"
    exit 1
fi

log_info "开始Docker磁盘清理"

before_size=$(docker system df 2>/dev/null | grep "Reclaimable" | awk '{print $NF}' || echo "未知")
log_info "清理前可回收空间: ${before_size}"

log_info "清理已停止的容器..."
docker container prune -f 2>/dev/null || true

log_info "清理悬空镜像..."
docker image prune -f 2>/dev/null || true

if [ "$prune_all" = "true" ]; then
    log_info "清理所有未使用的镜像..."
    docker image prune -a -f 2>/dev/null || true
    
    log_info "清理未使用的卷..."
    docker volume prune -f 2>/dev/null || true
    
    log_info "清理未使用的网络..."
    docker network prune -f 2>/dev/null || true
fi

after_size=$(docker system df 2>/dev/null | grep "Reclaimable" | awk '{print $NF}' || echo "未知")
log_info "清理后可回收空间: ${after_size}"

log_info "Docker磁盘清理完成"
exit 0
