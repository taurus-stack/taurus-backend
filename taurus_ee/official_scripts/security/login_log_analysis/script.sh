#!/bin/bash
# Function: 用户登录日志分析脚本
# Args: days - 分析天数，top_n - Top N IP
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 低危
# Official built-in script v1.0.0

set -euo pipefail

days=${days:-7}
top_n=${top_n:-10}

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

log_info "========== 用户登录日志分析 =========="
log_info "分析最近 ${days} 天的登录日志"

log_file=""
if [ -f /var/log/secure ]; then
    log_file="/var/log/secure"
elif [ -f /var/log/auth.log ]; then
    log_file="/var/log/auth.log"
fi

if [ -z "$log_file" ]; then
    log_error "未找到认证日志文件"
    exit 1
fi

log_info "日志文件: ${log_file}"

since_date=$(date -d "${days} days ago" '+%Y-%m-%d' 2>/dev/null || date -v-${days}d '+%Y-%m-%d' 2>/dev/null || "")

log_info "--- 登录成功统计 ---"
success_count=$(grep -c "Accepted" "$log_file" 2>/dev/null || echo 0)
log_info "成功登录次数: ${success_count}"

log_info "--- 登录失败统计 ---"
failed_count=$(grep -c "Failed password" "$log_file" 2>/dev/null || echo 0)
log_info "失败登录次数: ${failed_count}"

log_info "--- 登录成功Top ${top_n} IP ---"
grep "Accepted" "$log_file" 2>/dev/null     | grep -oP 'from \K[0-9.]+'     | sort | uniq -c | sort -rn | head -"$top_n"     | awk '{printf "  %-15s %s次\n", $2, $1}' || echo "  无数据"

log_info "--- 登录失败Top ${top_n} IP ---"
grep "Failed password" "$log_file" 2>/dev/null     | grep -oP 'from \K[0-9.]+'     | sort | uniq -c | sort -rn | head -"$top_n"     | awk '{printf "  %-15s %s次\n", $2, $1}' || echo "  无数据"

log_info "--- 当前在线用户 ---"
who -u

log_info "========== 分析完成 =========="
exit 0
