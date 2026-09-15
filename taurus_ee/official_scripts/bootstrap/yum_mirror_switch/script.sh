#!/bin/bash
# Function: YUM源切换为国内镜像
# Args: mirror - 镜像站选择
# Supported: CentOS 7+/RHEL 7+
# Risk Level: 中危
# Official built-in script v1.0.0

set -euo pipefail

mirror=${mirror:-aliyun}

log_info()  { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >&2; }

log_info "开始切换YUM源到 ${mirror} 镜像"

if [ ! -f /etc/redhat-release ]; then
    log_error "当前系统不是CentOS/RHEL系列"
    exit 1
fi

os_version=$(cat /etc/redhat-release | grep -oE '[0-9]+' | head -1)
log_info "系统版本: CentOS ${os_version}"

backup_dir="/etc/yum.repos.d/backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$backup_dir"
cp /etc/yum.repos.d/*.repo "$backup_dir/" 2>/dev/null || true
log_info "原有repo文件已备份到: ${backup_dir}"

case $mirror in
    aliyun)
        base_url="https://mirrors.aliyun.com"
        ;;
    tsinghua)
        base_url="https://mirrors.tuna.tsinghua.edu.cn"
        ;;
    huawei)
        base_url="https://mirrors.huaweicloud.com"
        ;;
    *)
        log_error "不支持的镜像站: ${mirror}"
        exit 1
        ;;
esac

cat > /etc/yum.repos.d/CentOS-Base.repo <<EOF
[base]
name=CentOS-\$releasever - Base - ${mirror}
baseurl=${base_url}/centos/\$releasever/os/\$basearch/
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-${os_version}

[updates]
name=CentOS-\$releasever - Updates - ${mirror}
baseurl=${base_url}/centos/\$releasever/updates/\$basearch/
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-${os_version}

[extras]
name=CentOS-\$releasever - Extras - ${mirror}
baseurl=${base_url}/centos/\$releasever/extras/\$basearch/
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-${os_version}

[epel]
name=EPEL - ${mirror}
baseurl=${base_url}/epel/\$releasever/\$basearch/
enabled=1
gpgcheck=0
EOF

yum clean all
yum makecache

log_info "YUM源切换完成"
exit 0
