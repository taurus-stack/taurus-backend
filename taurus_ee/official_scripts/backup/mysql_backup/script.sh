#!/bin/bash
# Function: MySQL数据库备份脚本
# Args: mysql_host, mysql_port, mysql_user, mysql_password, database, backup_dir, keep_days
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 中危
# Official built-in script v1.0.0

set -euo pipefail

mysql_host=${mysql_host:-127.0.0.1}
mysql_port=${mysql_port:-3306}
mysql_user=${mysql_user:-root}
mysql_password=${mysql_password:-}
database=${database:-}
backup_dir=${backup_dir:-/data/backup/mysql}
keep_days=${keep_days:-7}

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_warn()  { echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

mkdir -p "$backup_dir"

date_str=$(date '+%Y%m%d_%H%M%S')
if [ -n "$database" ]; then
    backup_file="${backup_dir}/${database}_${date_str}.sql.gz"
    dump_opts="$database"
else
    backup_file="${backup_dir}/all_databases_${date_str}.sql.gz"
    dump_opts="--all-databases"
fi

log_info "开始MySQL备份，主机: ${mysql_host}:${mysql_port}"
log_info "备份文件: ${backup_file}"

MYSQL_DUMP="mysqldump -h${mysql_host} -P${mysql_port} -u${mysql_user}"
if [ -n "$mysql_password" ]; then
    MYSQL_DUMP="${MYSQL_DUMP} -p${mysql_password}"
fi

start_time=$(date +%s)

if ${MYSQL_DUMP} --single-transaction --routines --triggers $dump_opts 2>/tmp/mysqldump_err.log | gzip > "$backup_file"; then
    end_time=$(date +%s)
    duration=$((end_time - start_time))
    file_size=$(du -h "$backup_file" | cut -f1)
    log_info "备份成功，文件大小: ${file_size}，耗时: ${duration}秒"
else
    log_error "备份失败: $(cat /tmp/mysqldump_err.log)"
    rm -f "$backup_file"
    exit 1
fi

log_info "清理 ${keep_days} 天前的旧备份..."
find "$backup_dir" -type f -name "*.sql.gz" -mtime +"$keep_days" -delete
deleted_count=$(find "$backup_dir" -type f -name "*.sql.gz" -mtime +"$keep_days" 2>/dev/null | wc -l)
log_info "清理完成"

log_info "MySQL备份完成"
exit 0
