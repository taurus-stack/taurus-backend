#!/bin/bash
# Function: 系统安全基线检查脚本
# Supported: CentOS 7+/Ubuntu 18.04+
# Risk Level: 低危
# Official built-in script v1.0.0

set -euo pipefail

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_warn()  { echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

warn_count=0

check_item() {
    local name=$1
    local status=$2
    local detail=$3
    if [ "$status" = "pass" ]; then
        log_info "[PASS] ${name}: ${detail}"
    else
        log_warn "[WARN] ${name}: ${detail}"
        warn_count=$((warn_count + 1))
    fi
}

log_info "========== 系统安全基线检查开始 =========="

log_info "--- 密码策略 ---"
if [ -f /etc/login.defs ]; then
    pass_max_days=$(grep "^PASS_MAX_DAYS" /etc/login.defs | awk '{print $2}')
    pass_min_len=$(grep "^PASS_MIN_LEN" /etc/login.defs | awk '{print $2}')
    if [ "$pass_max_days" -le 90 ] 2>/dev/null; then
        check_item "密码有效期" "pass" "${pass_max_days}天"
    else
        check_item "密码有效期" "warn" "${pass_max_days}天，建议设置为90天以内"
    fi
fi

log_info "--- SSH安全配置 ---"
if [ -f /etc/ssh/sshd_config ]; then
    permit_root=$(grep -i "^PermitRootLogin" /etc/ssh/sshd_config | awk '{print $2}')
    password_auth=$(grep -i "^PasswordAuthentication" /etc/ssh/sshd_config | awk '{print $2}')
    if [ "$permit_root" = "no" ]; then
        check_item "禁止root远程登录" "pass" "已禁止"
    else
        check_item "禁止root远程登录" "warn" "未禁止，建议关闭root远程登录"
    fi
fi

log_info "--- 防火墙状态 ---"
if command -v firewall-cmd >/dev/null 2>&1; then
    if firewall-cmd --state 2>/dev/null | grep -q running; then
        check_item "firewalld防火墙" "pass" "运行中"
    else
        check_item "firewalld防火墙" "warn" "未运行"
    fi
elif command -v ufw >/dev/null 2>&1; then
    if ufw status | grep -q "Status: active"; then
        check_item "ufw防火墙" "pass" "运行中"
    else
        check_item "ufw防火墙" "warn" "未运行"
    fi
else
    check_item "防火墙" "warn" "未检测到防火墙"
fi

log_info "--- SELinux/AppArmor ---"
if command -v getenforce >/dev/null 2>&1; then
    selinux_status=$(getenforce)
    if [ "$selinux_status" = "Enforcing" ]; then
        check_item "SELinux" "pass" "强制模式"
    else
        check_item "SELinux" "warn" "当前状态: ${selinux_status}"
    fi
fi

log_info "--- 空密码用户检查 ---"
empty_pass_users=$(awk -F: '$2 == "" {print $1}' /etc/shadow 2>/dev/null || true)
if [ -z "$empty_pass_users" ]; then
    check_item "空密码用户" "pass" "无空密码用户"
else
    check_item "空密码用户" "warn" "存在空密码用户: ${empty_pass_users}"
fi

log_info "--- 监听端口 ---"
log_info "对外开放端口:"
ss -tlnp 2>/dev/null | grep LISTEN | awk '{print "  " $4}' | head -20

log_info "========== 检查完成，共发现 ${warn_count} 个警告项 =========="

if [ "$warn_count" -gt 0 ]; then
    exit 1
else
    exit 0
fi
