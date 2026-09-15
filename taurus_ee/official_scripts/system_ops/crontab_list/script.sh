#!/bin/bash
# Function: Crontab任务清单脚本
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 低危
# Official built-in script v1.0.0

set -euo pipefail

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }

log_info "========== 系统Crontab任务清单 =========="

log_info "--- 系统级crontab (/etc/crontab) ---"
if [ -f /etc/crontab ]; then
    grep -v "^#" /etc/crontab | grep -v "^$"
else
    log_info "无"
fi

log_info "--- cron.d目录 ---"
if [ -d /etc/cron.d ]; then
    for f in /etc/cron.d/*; do
        if [ -f "$f" ]; then
            log_info "  $(basename "$f"):"
            grep -v "^#" "$f" | grep -v "^$" | sed 's/^/    /'
        fi
    done
fi

log_info "--- 各用户crontab ---"
for user in $(cut -d: -f1 /etc/passwd); do
    crontab_content=$(crontab -u "$user" -l 2>/dev/null || true)
    if [ -n "$crontab_content" ]; then
        log_info "用户 ${user}:"
        echo "$crontab_content" | grep -v "^#" | grep -v "^$" | sed 's/^/  /'
    fi
done

log_info "--- cron.hourly/daily/weekly/monthly ---"
for period in hourly daily weekly monthly; do
    dir="/etc/cron.${period}"
    if [ -d "$dir" ]; then
        scripts=$(ls -1 "$dir" 2>/dev/null || echo "")
        if [ -n "$scripts" ]; then
            log_info "cron.${period}:"
            echo "$scripts" | sed 's/^/  /'
        fi
    fi
done

log_info "========== 清单输出完成 =========="
exit 0
