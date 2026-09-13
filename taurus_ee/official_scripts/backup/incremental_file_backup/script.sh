#!/bin/bash
# Function: 文件目录增量备份脚本
# Args: source_dir - 源目录，backup_dir - 备份目录，keep_count - 保留数量
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 低危
# Official built-in script v1.0.0

set -euo pipefail

source_dir=${source_dir:-/data}
backup_dir=${backup_dir:-/backup}
keep_count=${keep_count:-7}

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

if [ ! -d "$source_dir" ]; then
    log_error "源目录不存在: ${source_dir}"
    exit 1
fi

log_info "开始增量备份"
log_info "源目录: ${source_dir}"
log_info "备份目录: ${backup_dir}"

if ! command -v rsync >/dev/null 2>&1; then
    log_error "rsync未安装"
    exit 1
fi

date_str=$(date '+%Y%m%d_%H%M%S')
target_dir="${backup_dir}/backup_${date_str}"
latest_link="${backup_dir}/latest"

mkdir -p "$backup_dir"

link_dest=""
if [ -L "$latest_link" ] && [ -d "$latest_link" ]; then
    link_dest="--link-dest=$(readlink -f "$latest_link")"
    log_info "使用硬链接增量: ${link_dest}"
fi

start_time=$(date +%s)

rsync -av --delete $link_dest "$source_dir/" "$target_dir/" 2>&1 | tail -5

end_time=$(date +%s)
duration=$((end_time - start_time))

rm -f "$latest_link"
ln -s "$target_dir" "$latest_link"

backup_size=$(du -sh "$target_dir" | cut -f1)
log_info "备份完成，大小: ${backup_size}，耗时: ${duration}秒"

log_info "清理旧备份，保留最近 ${keep_count} 个..."
cd "$backup_dir"
backup_list=$(ls -1d backup_* 2>/dev/null | sort -r)
if [ -n "$backup_list" ]; then
    echo "$backup_list" | tail -n +$((keep_count + 1)) | while read -r old_backup; do
        log_info "删除旧备份: ${old_backup}"
        rm -rf "$old_backup"
    done
fi

log_info "备份任务完成"
exit 0
