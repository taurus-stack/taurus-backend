#!/bin/bash
# Function: 日志文件清理脚本
# Args: log_dir - 日志目录，keep_days - 保留天数，file_pattern - 文件模式
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 中危
# Official built-in script v1.0.0

set -euo pipefail

log_dir=${log_dir:-/var/log}
keep_days=${keep_days:-30}
file_pattern=${file_pattern:-*.log}

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_warn()  { echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

if [ ! -d "$log_dir" ]; then
    log_error "目录不存在: ${log_dir}"
    exit 1
fi

log_info "开始清理日志，目录: ${log_dir}，保留天数: ${keep_days}天，模式: ${file_pattern}"

before_size=$(du -sh "$log_dir" 2>/dev/null | cut -f1)

deleted_count=0
deleted_size=0

while IFS= read -r -d '' file; do
    file_size=$(stat -c%s "$file" 2>/dev/null || echo 0)
    rm -f "$file"
    deleted_count=$((deleted_count + 1))
    deleted_size=$((deleted_size + file_size))
    log_info "删除: ${file}"
done < <(find "$log_dir" -type f -name "$file_pattern" -mtime +"$keep_days" -print0 2>/dev/null)

after_size=$(du -sh "$log_dir" 2>/dev/null | cut -f1)
deleted_size_mb=$(echo "scale=2; $deleted_size / 1024 / 1024" | bc 2>/dev/null || echo "0")

log_info "清理完成，删除文件数: ${deleted_count}，释放空间: ${deleted_size_mb}MB"
log_info "清理前目录大小: ${before_size}，清理后: ${after_size}"

exit 0
