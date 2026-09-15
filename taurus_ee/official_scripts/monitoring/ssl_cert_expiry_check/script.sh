#!/bin/bash
# Function: SSL证书到期检测脚本
# Args: domains - 域名列表，warn_days - 告警天数
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 低危
# Official built-in script v1.0.0

set -euo pipefail

domains=${domains:-www.example.com}
warn_days=${warn_days:-30}

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_warn()  { echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

log_info "开始SSL证书到期检测，告警阈值: ${warn_days}天"

warn_count=0
IFS=',' read -ra DOMAIN_ARRAY <<< "$domains"

for domain in "${DOMAIN_ARRAY[@]}"; do
    domain=$(echo "$domain" | xargs)
    log_info "检测域名: ${domain}"
    
    expire_date=$(echo | openssl s_client -servername "$domain" -connect "${domain}:443" 2>/dev/null         | openssl x509 -noout -enddate 2>/dev/null         | cut -d= -f2 || true)
    
    if [ -z "$expire_date" ]; then
        log_warn "无法获取 ${domain} 的证书信息"
        warn_count=$((warn_count + 1))
        continue
    fi
    
    expire_ts=$(date -d "$expire_date" +%s 2>/dev/null || date -j -f "%b %d %H:%M:%S %Y %Z" "$expire_date" +%s 2>/dev/null || echo 0)
    now_ts=$(date +%s)
    remain_days=$(( (expire_ts - now_ts) / 86400 ))
    
    log_info "${domain} 证书到期时间: ${expire_date} (剩余 ${remain_days} 天)"
    
    if [ "$remain_days" -le "$warn_days" ]; then
        log_warn "${domain} 证书即将到期，剩余 ${remain_days} 天"
        warn_count=$((warn_count + 1))
    fi
done

if [ "$warn_count" -gt 0 ]; then
    log_error "检测完成，${warn_count} 个域名证书需要关注"
    exit 1
else
    log_info "检测完成，所有证书正常"
    exit 0
fi
