#!/bin/bash
#
# Taurus Supervisor 一键安装脚本
#
# 用法（从服务端下载后自动执行）:
#   curl -fsSL https://taurus.example.com/api/taurus/supervisor/install_script/ | bash -s -- --token tao_xxx
#   curl -fsSL https://taurus.example.com/api/taurus/supervisor/install_script/ | bash -s -- --token tao_xxx --auto-install
#   wget -qO- https://taurus.example.com/api/taurus/supervisor/install_script/ | bash -s -- --token tao_xxx --auto-install
#
# 也可以手动下载后执行:
#   ./install.sh --server https://taurus.example.com --token tao_xxx --auto-install
#

set -euo pipefail

SCRIPT_VERSION="1.0.0"

SERVER_URL="__TAURUS_SERVER_URL__"
SERVER_URL_INJECTED=false
TOKEN=""
EXTRA_INFO="{}"
SUPERVISOR_VERSION="1.0.0"
AUTO_INSTALL=false
FORCE_INSTALL=false

CURRENT_USER=$(whoami)
IS_ROOT=false
if [[ "$CURRENT_USER" == "root" ]]; then
    IS_ROOT=true
    KEY_DIR="/etc/taurus-supervisor"
    BASE_DIR="/opt/taurus"
    SUPERVISOR_DIR="/opt/taurus/supervisor"
    VERSIONS_DIR="/opt/taurus/versions"
else
    KEY_DIR="$HOME/.taurus-supervisor"
    BASE_DIR="$HOME/taurus"
    SUPERVISOR_DIR="$HOME/taurus/supervisor"
    VERSIONS_DIR="$HOME/taurus/versions"
fi

TMP_DIR=""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${CYAN}[STEP]${NC} $1"; }

cleanup() {
    if [[ -n "$TMP_DIR" ]] && [[ -d "$TMP_DIR" ]]; then
        rm -rf "$TMP_DIR"
    fi
}
trap cleanup EXIT

require_arg() {
    if [[ $# -lt 2 ]] || [[ -z "$2" ]]; then
        log_error "$1 需要一个参数值"
        exit 1
    fi
}

validate_url() {
    local url="$1"
    if [[ ! "$url" =~ ^https?:// ]]; then
        log_error "无效的服务端地址: $url (必须以 http:// 或 https:// 开头)"
        exit 1
    fi
}

check_installed() {
    local config_file="$KEY_DIR/config.json"
    if [[ -f "$config_file" ]]; then
        local existing_id
        existing_id=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('host_id',''))" "$config_file" 2>/dev/null || true)
        if [[ -n "$existing_id" ]]; then
            if [[ "$FORCE_INSTALL" != "true" ]]; then
                log_warn "检测到已存在的 Supervisor 安装 (host_id: $existing_id)"
                log_warn "配置文件: $config_file"
                log_warn ""
                log_warn "如需重新安装，请使用 --force 参数:"
                log_warn "  ... | bash -s -- --token <TOKEN> --force --auto-install"
                log_warn ""
                log_warn "或先卸载: $SUPERVISOR_DIR/bin/taurus-supervisor uninstall (如有)"
                return 1
            else
                log_info "检测到已存在的安装 (host_id: $existing_id)，--force 模式将覆盖安装"
            fi
        fi
    fi
    return 0
}

check_connectivity() {
    log_info "检查服务端连通性..."
    if ! curl -sf --max-time 10 "${SERVER_URL}/api/taurus/supervisor/install_script/" > /dev/null 2>&1; then
        log_error "无法连接到服务端: $SERVER_URL"
        log_error "请检查:"
        log_error "  1. 服务端地址是否正确"
        log_error "  2. 网络是否可达"
        log_error "  3. 防火墙规则是否放行"
        exit 1
    fi
    log_info "服务端连通正常"
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --server)
            require_arg "--server" "${2:-}"
            SERVER_URL="$2"
            shift 2
            ;;
        --token)
            require_arg "--token" "${2:-}"
            TOKEN="$2"
            shift 2
            ;;
        --extra-info)
            require_arg "--extra-info" "${2:-}"
            EXTRA_INFO="$2"
            shift 2
            ;;
        --version)
            require_arg "--version" "${2:-}"
            SUPERVISOR_VERSION="$2"
            shift 2
            ;;
        --install-dir)
            require_arg "--install-dir" "${2:-}"
            BASE_DIR="$2"
            SUPERVISOR_DIR="$2/supervisor"
            VERSIONS_DIR="$2/versions"
            shift 2
            ;;
        --auto-install)
            AUTO_INSTALL=true
            shift
            ;;
        --force)
            FORCE_INSTALL=true
            shift
            ;;
        --help|-h)
            echo "Taurus Supervisor 安装脚本 v${SCRIPT_VERSION}"
            echo ""
            echo "用法:"
            echo "  curl -fsSL <SERVER>/api/taurus/supervisor/install_script/ | bash -s -- --token <TOKEN>"
            echo "  ./install.sh --server <URL> --token <TOKEN> [选项]"
            echo ""
            echo "选项:"
            echo "  --server <URL>      Taurus 服务器地址 (通过 curl 下载时自动注入)"
            echo "  --token <TOKEN>     注册令牌 (必需)"
            echo "  --extra-info <JSON> 额外信息 (可选)"
            echo "  --version <VER>     Supervisor 版本号 (默认: 1.0.0)"
            echo "  --install-dir <DIR> 安装目录 (root默认:/opt/taurus, 普通用户默认:~/taurus)"
            echo "  --auto-install      自动下载、安装、配置并启动 Supervisor（一键安装）"
            echo "  --force             强制覆盖已存在的安装"
            echo "  --help              显示帮助"
            echo ""
            echo "示例:"
            echo "  一键安装:   curl -fsSL <SERVER>/api/taurus/supervisor/install_script/ | bash -s -- --token tao_abc123 --auto-install"
            echo "  强制重装:   curl -fsSL <SERVER>/api/taurus/supervisor/install_script/ | bash -s -- --token tao_abc123 --force --auto-install"
            echo "  仅注册:     curl -fsSL <SERVER>/api/taurus/supervisor/install_script/ | bash -s -- --token tao_abc123"
            exit 0
            ;;
        *)
            log_error "未知参数: $1"
            log_error "使用 --help 查看帮助信息"
            exit 1
            ;;
    esac
done

echo ""
echo "=========================================="
echo "  Taurus Installer v${SCRIPT_VERSION}"
echo "=========================================="
echo ""

if [[ -z "$SERVER_URL" ]] || [[ "$SERVER_URL_INJECTED" != "true" && "$SERVER_URL" == "__TAURUS_SERVER_URL__" ]]; then
    log_error "服务端地址未指定。请通过 curl 从服务端下载脚本，或使用 --server 参数指定"
    exit 1
fi

validate_url "$SERVER_URL"

if [[ -z "$TOKEN" ]]; then
    log_error "请指定 --token 参数"
    echo ""
    echo "获取令牌:"
    echo "  1. 登录 Taurus 管理后台"
    echo "  2. 创建注册令牌"
    echo "  3. 复制令牌值（以 tao_ 开头）"
    echo ""
    echo "然后执行:"
    echo "  curl -fsSL ${SERVER_URL}/api/taurus/supervisor/install_script/ | bash -s -- --token <YOUR_TOKEN> --auto-install"
    exit 1
fi

if ! command -v curl &> /dev/null; then
    log_error "curl 未安装，请先安装 curl"
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    log_error "python3 未安装，请先安装 Python 3"
    exit 1
fi

if ! command -v jq &> /dev/null; then
    log_warn "jq 未安装，将使用 python3 解析 JSON"
    JSON_PARSER="python3"
else
    JSON_PARSER="jq"
fi

check_connectivity

log_step "收集主机信息..."

HOSTNAME=$(hostname)
if [[ -n "${TAURUS_HOST_IP:-}" ]]; then
    IP_ADDR="$TAURUS_HOST_IP"
    log_info "  使用 TAURUS_HOST_IP 覆盖: $IP_ADDR"
else
    IP_ADDR=$(hostname -I 2>/dev/null | awk '{print $1}' || hostname -i 2>/dev/null | awk '{print $1}' || echo "unknown")
fi
OS_TYPE=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
OS_VERSION=$(cat /etc/os-release 2>/dev/null | grep '^PRETTY_NAME=' | cut -d'"' -f2 || uname -r)

log_info "  主机名: $HOSTNAME"
log_info "  IP 地址: $IP_ADDR"
log_info "  操作系统: ${OS_TYPE} (${OS_VERSION})"
log_info "  架构: ${ARCH}"

if ! check_installed; then
    exit 1
fi

# --force 模式下如果本地已有有效配置，跳过注册直接复用
SKIP_REGISTER=false
if [[ "$FORCE_INSTALL" == "true" ]]; then
    local_config_file="$KEY_DIR/config.json"
    if [[ -f "$local_config_file" ]]; then
        EXISTING_HOST_ID=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('host_id',''))" "$local_config_file" 2>/dev/null || true)
        EXISTING_SERVER_URL=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('server_url',''))" "$local_config_file" 2>/dev/null || true)
        if [[ -n "$EXISTING_HOST_ID" ]] && [[ "$EXISTING_SERVER_URL" == "$SERVER_URL" ]]; then
            log_info "--force 模式：复用本地已有的 host_id: $EXISTING_HOST_ID，跳过注册"
            HOST_ID="$EXISTING_HOST_ID"
            SIGNING_SECRET=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('request_signing_secret',''))" "$local_config_file" 2>/dev/null || true)
            HB_SERVER_URL=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('heartbeat',{}).get('server_url',''))" "$local_config_file" 2>/dev/null || true)
            HB_INTERVAL=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('heartbeat',{}).get('interval',30))" "$local_config_file" 2>/dev/null || true)
            HB_TIMEOUT=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('heartbeat',{}).get('timeout',10))" "$local_config_file" 2>/dev/null || true)
            SKIP_REGISTER=true
        fi
    fi
fi

if [[ "$SKIP_REGISTER" != "true" ]]; then
    log_step "发送注册请求到 ${SERVER_URL} ..."

    REGISTER_URL="${SERVER_URL}/api/taurus/supervisor/register/"

    REQUEST_BODY=$(python3 << PYEOF
import json, sys
body = {
    'token': r'''${TOKEN}''',
    'host_info': {
        'hostname': r'''${HOSTNAME}''',
        'ip': r'''${IP_ADDR}''',
        'port': 22,
        'os': r'''${OS_TYPE}''',
        'arch': r'''${ARCH}''',
        'os_version': r'''${OS_VERSION}''',
        'extra_info': json.loads(r'''${EXTRA_INFO}''') if r'''${EXTRA_INFO}''' else {},
    },
    'supervisor_version': r'''${SUPERVISOR_VERSION}''',
}
print(json.dumps(body))
PYEOF
)

    RESPONSE=$(curl -s -w "\n%{http_code}" --max-time 30 -X POST "$REGISTER_URL" \
        -H "Content-Type: application/json" \
        -H "User-Agent: taurus-installer/${SCRIPT_VERSION}" \
        -d "$REQUEST_BODY" 2>/dev/null) || true

    HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
    BODY=$(echo "$RESPONSE" | sed '$d')

    if [[ -z "$HTTP_CODE" ]] || [[ -z "$BODY" ]]; then
        log_error "注册请求无响应，请检查网络和服务端状态"
        exit 1
    fi

    if [[ "$HTTP_CODE" != "200" ]]; then
        log_error "注册失败 (HTTP ${HTTP_CODE})"
        log_error "响应: $(echo "$BODY" | head -c 500)"
        exit 1
    fi

    if [[ "$JSON_PARSER" == "jq" ]]; then
        HOST_ID=$(echo "$BODY" | jq -r '.data.host_id // empty')
        STATUS=$(echo "$BODY" | jq -r '.data.status // empty')
        STATUS_DISPLAY=$(echo "$BODY" | jq -r '.data.status_display // empty')
        MESSAGE=$(echo "$BODY" | jq -r '.data.message // empty')
        HB_SERVER_URL=$(echo "$BODY" | jq -r '.data.heartbeat.server_url // empty')
        HB_INTERVAL=$(echo "$BODY" | jq -r '.data.heartbeat.interval // 30')
        HB_TIMEOUT=$(echo "$BODY" | jq -r '.data.heartbeat.timeout // 10')
        SIGNING_SECRET=$(echo "$BODY" | jq -r '.data.signing_secret // empty')
    else
        HOST_ID=$(echo "$BODY" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    print(d.get('data',{}).get('host_id',''))
except Exception as e:
    print('', file=sys.stderr)
")
        STATUS=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data'].get('status',''))")
        STATUS_DISPLAY=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data'].get('status_display',''))")
        MESSAGE=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data'].get('message',''))")
        HB_SERVER_URL=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); hb=d['data'].get('heartbeat',{}); print(hb.get('server_url',''))")
        HB_INTERVAL=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); hb=d['data'].get('heartbeat',{}); print(hb.get('interval',30))")
        HB_TIMEOUT=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); hb=d['data'].get('heartbeat',{}); print(hb.get('timeout',10))")
        SIGNING_SECRET=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); v=d['data'].get('signing_secret'); print(v if v else '')")
    fi

    if [[ -z "$HOST_ID" ]]; then
        log_error "注册响应解析失败，无法获取 host_id"
        log_error "原始响应: $(echo "$BODY" | head -c 300)"
        exit 1
    fi

    log_info "注册成功!"
    log_info "  主机 ID: $HOST_ID"
    log_info "  状态: ${STATUS_DISPLAY:-$STATUS}"
    [[ -n "$MESSAGE" ]] && log_info "  消息: $MESSAGE"

    case "${STATUS:-}" in
        0)
            log_warn "主机等待管理员审批，审批通过后方可正常使用"
            log_info "请通知管理员在 Taurus 后台批准主机: $HOST_ID"
            ;;
        1)
            log_info "主机已自动批准，可以正常使用"
            ;;
    esac
fi

mkdir -p "$KEY_DIR"
chmod 700 "$KEY_DIR"

log_step "保存配置文件..."
python3 << PYEOF > "$KEY_DIR/config.json"
import json, sys, datetime
config = {
    'host_id': r'''${HOST_ID}''',
    'server_url': r'''${SERVER_URL}''',
    'version': r'''${SUPERVISOR_VERSION}''',
    'registered_at': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
    'installer_version': r'''${SCRIPT_VERSION}''',
}
signing_secret = r'''${SIGNING_SECRET:-}'''
if signing_secret:
    config['request_signing_secret'] = signing_secret
hb = {}
if r'''${HB_SERVER_URL}''':
    hb['server_url'] = r'''${HB_SERVER_URL}'''
if r'''${HB_INTERVAL}''':
    try:
        hb['interval'] = int(r'''${HB_INTERVAL}''')
    except ValueError:
        pass
if r'''${HB_TIMEOUT}''':
    try:
        hb['timeout'] = int(r'''${HB_TIMEOUT}''')
    except ValueError:
        pass
if hb:
    config['heartbeat'] = hb
print(json.dumps(config, indent=2))
PYEOF

chmod 600 "$KEY_DIR/config.json"

if [[ "$FORCE_INSTALL" == "true" ]]; then
    STATE_FILE="$BASE_DIR/data/state.json"
    if [[ -f "$STATE_FILE" ]]; then
        log_info "更新 state.json 中的 host_id..."
        python3 << PYEOF > /dev/null
import json
try:
    with open(r'''${STATE_FILE}''', 'r') as f:
        state = json.load(f)
    state['host_id'] = r'''${HOST_ID}'''
    with open(r'''${STATE_FILE}''', 'w') as f:
        json.dump(state, f, indent=2)
    print("state.json updated successfully")
except Exception as e:
    print(f"Error updating state.json: {e}", file=sys.stderr)
PYEOF
        log_info "  ✓ state.json 已更新"
    fi

    SYSTEMD_SERVICE_FILE=""
    if [[ "$IS_ROOT" == "true" ]]; then
        SYSTEMD_SERVICE_FILE="/etc/systemd/system/taurus-supervisor.service"
    else
        SYSTEMD_SERVICE_FILE="$HOME/.config/systemd/user/taurus-supervisor.service"
    fi

    if [[ -f "$SYSTEMD_SERVICE_FILE" ]]; then
        log_info "更新 systemd 服务文件中的 HOST_ID..."
        sed -i "s/^Environment=HOST_ID=.*/Environment=HOST_ID=${HOST_ID}/" "$SYSTEMD_SERVICE_FILE"
        if [[ -n "$SIGNING_SECRET" ]]; then
            sed -i "s/^Environment=REQUEST_SIGNING_SECRET=.*/Environment=REQUEST_SIGNING_SECRET=${SIGNING_SECRET}/" "$SYSTEMD_SERVICE_FILE"
        fi
        sed -i "s/^Environment=SERVER_URL=.*/Environment=SERVER_URL=${SERVER_URL}/" "$SYSTEMD_SERVICE_FILE"
        log_info "  ✓ systemd 服务文件已更新"

        if command -v systemctl &> /dev/null; then
            if [[ "$IS_ROOT" == "true" ]]; then
                systemctl daemon-reload
            else
                systemctl --user daemon-reload
            fi
            log_info "  ✓ systemd 配置已重新加载"
        fi
    fi
fi

if [[ -n "$HB_SERVER_URL" ]]; then
    log_info "心跳配置已保存:"
    log_info "    服务器: ${HB_SERVER_URL}"
    log_info "    间隔: ${HB_INTERVAL}s"
    log_info "    超时: ${HB_TIMEOUT}s"
fi

case "${STATUS:-}" in
    0)
        log_warn "主机等待管理员审批，审批通过后方可正常使用"
        log_info "请通知管理员在 Taurus 后台批准主机: $HOST_ID"
        ;;
    1)
        log_info "主机已自动批准，可以正常使用"
        ;;
esac

log_info "配置文件: $KEY_DIR/config.json"
log_info "注册完成!"

if [[ "$AUTO_INSTALL" != "true" ]]; then
    log_info ""
    log_info "如需自动安装 Supervisor，请添加 --auto-install 参数:"
    log_info "  curl -fsSL ${SERVER_URL}/api/taurus/supervisor/install_script/ | bash -s -- --token \$TOKEN --auto-install"
    exit 0
fi

echo ""
echo "=========================================="
echo "  开始自动安装 Taurus Supervisor"
echo "=========================================="
log_info "当前用户: $CURRENT_USER"
log_info "安装模式: $([ "$IS_ROOT" = true ] && echo '系统级 (root)' || echo '用户级 (普通用户)')"
log_info "基础目录: $BASE_DIR"
log_info "Supervisor 目录: $SUPERVISOR_DIR"
log_info "版本目录: $VERSIONS_DIR"
log_info "配置目录: $KEY_DIR"
echo "=========================================="

PLATFORM="linux"
ARCH=$(uname -m)

log_step "创建目录结构..."
mkdir -p "$BASE_DIR"
mkdir -p "$SUPERVISOR_DIR/bin"
mkdir -p "$VERSIONS_DIR"
mkdir -p "$BASE_DIR/data"
if [[ "$IS_ROOT" == "true" ]]; then
    mkdir -p /var/log/taurus-supervisor
fi

# --force 模式下如果二进制文件已存在且正在运行，先停止
if [[ "$FORCE_INSTALL" == "true" ]] && [[ -x "$SUPERVISOR_DIR/bin/taurus-supervisor" ]]; then
    if pgrep -f "$SUPERVISOR_DIR/bin/taurus-supervisor" > /dev/null 2>&1; then
        log_info "检测到 Supervisor 正在运行，先停止进程..."
        pkill -f "$SUPERVISOR_DIR/bin/taurus-supervisor" 2>/dev/null || true
        sleep 2
        if pgrep -f "$SUPERVISOR_DIR/bin/taurus-supervisor" > /dev/null 2>&1; then
            log_warn "进程未正常退出，强制终止..."
            pkill -9 -f "$SUPERVISOR_DIR/bin/taurus-supervisor" 2>/dev/null || true
            sleep 1
        fi
        log_info "Supervisor 进程已停止"
    fi
fi

log_step "下载并安装 Taurus Supervisor..."
SUPERVISOR_URL="${SERVER_URL}/api/taurus/supervisor/download/?package_type=supervisor&platform=${PLATFORM}&arch=${ARCH}"

TMP_DIR=$(mktemp -d)
PACKAGE_FILE="${TMP_DIR}/taurus-supervisor.tar.gz"

HTTP_CODE=$(curl -s -w "%{http_code}" --retry 3 --retry-delay 5 --max-time 120 \
    -o "$PACKAGE_FILE" -D "${TMP_DIR}/headers.txt" "$SUPERVISOR_URL") || HTTP_CODE="000"

if [[ "$HTTP_CODE" != "200" ]]; then
    log_error "Supervisor 下载失败 (HTTP ${HTTP_CODE})"
    log_error "下载地址: $SUPERVISOR_URL"
    log_error "请检查:"
    log_error "  1. 服务端是否有对应平台/架构的二进制包"
    log_error "  2. 网络连接是否正常"
    exit 1
fi

# 检查 Content-Type，防止服务端返回 JSON 错误信息
CONTENT_TYPE=$(grep -i 'content-type' "${TMP_DIR}/headers.txt" 2>/dev/null | sed 's/.*: //' | tr -d '\r\n' || true)
if [[ -n "$CONTENT_TYPE" ]] && echo "$CONTENT_TYPE" | grep -qi 'application/json'; then
    log_error "Supervisor 下载失败: 服务端返回了 JSON 响应而非二进制文件"
    log_error "Content-Type: $CONTENT_TYPE"
    log_error "下载地址: $SUPERVISOR_URL"
    log_error "请检查服务端是否有对应平台/架构的二进制包"
    exit 1
fi

# 检查最小文件大小（压缩包至少 1KB）
FILE_SIZE=$(stat -c%s "$PACKAGE_FILE" 2>/dev/null || stat -f%z "$PACKAGE_FILE" 2>/dev/null || echo "0")
if [[ "$FILE_SIZE" -lt 1024 ]]; then
    log_error "Supervisor 下载失败: 文件过小 (${FILE_SIZE} bytes)，可能不是有效的压缩包"
    log_error "下载地址: $SUPERVISOR_URL"
    exit 1
fi

EXPECTED_SHA256=$(grep -i 'x-checksum-sha256' "${TMP_DIR}/headers.txt" 2>/dev/null | sed 's/.*: //' | tr -d '\r\n' || true)
if [[ -n "$EXPECTED_SHA256" ]]; then
    ACTUAL_SHA256=$(sha256sum "$PACKAGE_FILE" | awk '{print $1}')
    if [[ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]]; then
        log_error "Supervisor 校验失败!"
        log_error "  期望: $EXPECTED_SHA256"
        log_error "  实际: $ACTUAL_SHA256"
        exit 1
    fi
    log_info "SHA256 校验通过: ${ACTUAL_SHA256:0:16}..."
else
    log_warn "服务端未返回校验和，跳过完整性校验"
fi

log_info "Supervisor 包下载成功 (${FILE_SIZE} bytes)"

# 解压安装包
log_info "解压安装包..."
EXTRACT_DIR="${TMP_DIR}/extracted"
mkdir -p "$EXTRACT_DIR"
if ! tar xzf "$PACKAGE_FILE" -C "$EXTRACT_DIR" 2>/dev/null; then
    log_error "解压失败，文件可能不是有效的 tar.gz 压缩包"
    exit 1
fi

# 复制二进制文件
SUPERVISOR_BIN=$(find "$EXTRACT_DIR" -name "taurus-supervisor" -type f 2>/dev/null | head -1)
if [[ -z "$SUPERVISOR_BIN" ]]; then
    log_error "解压后未找到 taurus-supervisor 二进制文件"
    exit 1
fi

cp "$SUPERVISOR_BIN" "$SUPERVISOR_DIR/bin/taurus-supervisor"
chmod +x "$SUPERVISOR_DIR/bin/taurus-supervisor"
log_info "Supervisor 已安装: $SUPERVISOR_DIR/bin/taurus-supervisor"

# 复制 taurus-pm 二进制文件
PM_BIN=$(find "$EXTRACT_DIR" -name "taurus-pm" -type f 2>/dev/null | head -1)
if [[ -n "$PM_BIN" ]]; then
    cp "$PM_BIN" "$SUPERVISOR_DIR/bin/taurus-pm"
    chmod +x "$SUPERVISOR_DIR/bin/taurus-pm"
    log_info "taurus-pm 已安装: $SUPERVISOR_DIR/bin/taurus-pm"
else
    log_warn "解压后未找到 taurus-pm 二进制文件"
fi

# 复制模板文件（可选）
if [[ -d "$EXTRACT_DIR/templates" ]]; then
    mkdir -p "$SUPERVISOR_DIR/templates"
    cp -r "$EXTRACT_DIR/templates/"* "$SUPERVISOR_DIR/templates/" 2>/dev/null || true
    log_info "模板文件已复制: $SUPERVISOR_DIR/templates/"
fi

# 复制管理脚本（可选）
if [[ -d "$EXTRACT_DIR/scripts" ]]; then
    mkdir -p "$BASE_DIR/scripts"
    cp -r "$EXTRACT_DIR/scripts/"* "$BASE_DIR/scripts/" 2>/dev/null || true
    chmod +x "$BASE_DIR/scripts/"*.sh 2>/dev/null || true
    log_info "管理脚本已复制: $BASE_DIR/scripts/"
fi

rm -rf "$TMP_DIR"
TMP_DIR=""

log_step "生成状态文件..."

cat > "$BASE_DIR/data/state.json" << EOF
{
  "current_version": "${SUPERVISOR_VERSION}",
  "previous_version": null,
  "upgrade_in_progress": false,
  "upgrade_target_version": null,
  "last_successful_version": "${SUPERVISOR_VERSION}",
  "upgrade_history": [],
  "programs": {}
}
EOF

chmod 600 "$BASE_DIR/data/state.json"
log_info "状态文件已生成: $BASE_DIR/data/state.json"

if [[ "$IS_ROOT" == "true" ]]; then
    log_step "配置系统级服务 (systemd)..."

    mkdir -p /etc/taurus-supervisor
    cat > /etc/taurus-supervisor/supervisor.env << EOF
# Taurus Supervisor 配置
BASE_DIR=${BASE_DIR}
SERVER_URL=${SERVER_URL}
HOST_ID=${HOST_ID}
HEALTH_CHECK_INTERVAL=30
SUPERVISOR_HEARTBEAT_INTERVAL=30
EOF

    if [[ -n "$SIGNING_SECRET" ]]; then
        echo "REQUEST_SIGNING_SECRET=${SIGNING_SECRET}" >> /etc/taurus-supervisor/supervisor.env
    fi

    chmod 600 /etc/taurus-supervisor/supervisor.env
    log_info "环境变量文件已生成: /etc/taurus-supervisor/supervisor.env"

    cat > /etc/systemd/system/taurus-supervisor.service << EOF
[Unit]
Description=Taurus Supervisor - 通用程序管理守护进程
After=network.target
Wants=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${BASE_DIR}
ExecStart=${SUPERVISOR_DIR}/bin/taurus-supervisor
Restart=always
RestartSec=3
EnvironmentFile=/etc/taurus-supervisor/supervisor.env

StandardOutput=journal
StandardError=journal
SyslogIdentifier=taurus-supervisor

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=${BASE_DIR} /var/log/taurus-supervisor

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    log_info "systemd 服务单元已创建: /etc/systemd/system/taurus-supervisor.service"

    log_step "启动 Taurus Supervisor 服务..."
    systemctl enable taurus-supervisor
    systemctl start taurus-supervisor

    sleep 3

    if systemctl is-active --quiet taurus-supervisor; then
        log_info "Taurus Supervisor 服务已启动并运行正常"
    else
        log_warn "服务启动失败，请检查日志:"
        log_warn "  journalctl -u taurus-supervisor -n 50 --no-pager"
    fi

else
    log_step "配置用户级服务..."

    if command -v systemctl &> /dev/null && systemctl --user is-system-running &> /dev/null 2>&1; then
        log_info "检测到 systemd --user 支持，配置用户服务..."

        mkdir -p "$HOME/.config/systemd/user"

        cat > "$HOME/.config/systemd/user/taurus-supervisor.service" << EOF
[Unit]
Description=Taurus Supervisor - 通用程序管理守护进程
After=network.target

[Service]
Type=simple
WorkingDirectory=${BASE_DIR}
ExecStart=${SUPERVISOR_DIR}/bin/taurus-supervisor
Restart=always
RestartSec=3
Environment=BASE_DIR=${BASE_DIR}
Environment=SERVER_URL=${SERVER_URL}
Environment=HOST_ID=${HOST_ID}
EOF

        if [[ -n "$SIGNING_SECRET" ]]; then
            echo "Environment=REQUEST_SIGNING_SECRET=${SIGNING_SECRET}" >> "$HOME/.config/systemd/user/taurus-supervisor.service"
        fi

        cat >> "$HOME/.config/systemd/user/taurus-supervisor.service" << EOF

[Install]
WantedBy=default.target
EOF

        systemctl --user daemon-reload
        systemctl --user enable taurus-supervisor
        systemctl --user start taurus-supervisor

        sleep 3

        if systemctl --user is-active --quiet taurus-supervisor; then
            log_info "Taurus Supervisor 用户服务已启动并运行正常"
        else
            log_warn "用户服务启动失败，回退到后台进程模式"
            nohup "$SUPERVISOR_DIR/bin/taurus-supervisor" > "$BASE_DIR/supervisor.log" 2>&1 &
            echo $! > "$BASE_DIR/supervisor.pid"
            log_info "Taurus Supervisor 已以后台进程模式启动 (PID: $!)"
        fi

    else
        log_info "systemd --user 不可用，使用后台进程模式..."

        cat > "$BASE_DIR/start-supervisor.sh" << EOF
#!/bin/bash
set -e

SUPERVISOR_DIR="${SUPERVISOR_DIR}"
BASE_DIR="${BASE_DIR}"

cd "\$BASE_DIR"

export BASE_DIR="\$BASE_DIR"
export SERVER_URL="${SERVER_URL}"
export HOST_ID="${HOST_ID}"
EOF

        if [[ -n "$SIGNING_SECRET" ]]; then
            echo "export REQUEST_SIGNING_SECRET=${SIGNING_SECRET}" >> "$BASE_DIR/start-supervisor.sh"
        fi

        cat >> "$BASE_DIR/start-supervisor.sh" << EOF

echo "[INFO] 启动 Taurus Supervisor..."
exec "\$SUPERVISOR_DIR/bin/taurus-supervisor"
EOF
        chmod +x "$BASE_DIR/start-supervisor.sh"

        nohup "$BASE_DIR/start-supervisor.sh" > "$BASE_DIR/supervisor.log" 2>&1 &
        PID=$!
        echo $PID > "$BASE_DIR/supervisor.pid"

        sleep 3

        if kill -0 $PID 2>/dev/null; then
            log_info "Taurus Supervisor 已以后台进程模式启动 (PID: $PID)"
        else
            log_error "Supervisor 启动失败，请查看日志: tail -f $BASE_DIR/supervisor.log"
            exit 1
        fi
    fi
fi

echo ""
echo "=========================================="
echo "  Taurus Supervisor 安装完成!"
echo "=========================================="
echo ""
echo "基础目录:    $BASE_DIR"
echo "Supervisor:  $SUPERVISOR_DIR/bin/taurus-supervisor"
echo "taurus-pm:   $SUPERVISOR_DIR/bin/taurus-pm"
echo "配置文件:    $KEY_DIR/config.json"
echo "版本:        $SUPERVISOR_VERSION"
echo ""
echo "taurus-pm 进程管理工具:"
echo "  列出程序:   taurus-pm list"
echo "  启动程序:   taurus-pm start <program>"
echo "  停止程序:   taurus-pm stop <program>"
echo "  查看状态:   taurus-pm status"
echo ""

if [[ "$IS_ROOT" == "true" ]]; then
    echo "服务管理命令:"
    echo "  查看状态: sudo systemctl status taurus-supervisor"
    echo "  查看日志: sudo journalctl -u taurus-supervisor -f"
    echo "  重启服务: sudo systemctl restart taurus-supervisor"
    echo "  停止服务: sudo systemctl stop taurus-supervisor"
else
    if command -v systemctl &> /dev/null && systemctl --user is-system-running &> /dev/null 2>&1; then
        echo "服务管理命令:"
        echo "  查看状态: systemctl --user status taurus-supervisor"
        echo "  查看日志: journalctl --user -u taurus-supervisor -f"
        echo "  重启服务: systemctl --user restart taurus-supervisor"
        echo "  停止服务: systemctl --user stop taurus-supervisor"
    else
        echo "进程管理命令:"
        echo "  查看状态: ps aux | grep taurus-supervisor"
        echo "  查看日志: tail -f $BASE_DIR/supervisor.log"
        echo "  停止服务: kill \$(cat $BASE_DIR/supervisor.pid)"
    fi
fi
echo ""