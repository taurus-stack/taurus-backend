# ============================================================
# Taurus 官方 Shell 脚本公共函数库
# 在 Loader 中会自动注入到所有 Shell 类型官方脚本的 shebang 之后
# 脚本正文可以直接使用下面的工具函数
# ============================================================

if [ -z "${TAURUS_COMMON_LOADED:-}" ]; then
export TAURUS_COMMON_LOADED=1

# ---------- 日志函数 ----------
log_info()  { echo "[INFO]  $(date '+%Y-%m-%d %H:%M:%S') $*"; }
log_warn()  { echo "[WARN]  $(date '+%Y-%m-%d %H:%M:%S') $*" 1>&2; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $*" 1>&2; }
log_ok()    { echo "[OK]    $(date '+%Y-%m-%d %H:%M:%S') $*"; }
log_debug() {
    [ "${DEBUG:-0}" = "1" ] && echo "[DEBUG] $(date '+%Y-%m-%d %H:%M:%S') $*" || true
}

# ---------- 退出辅助 ----------
die() {
    log_error "$*"
    exit 1
}

# ---------- 权限检查 ----------
require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        die "本脚本需要 root 权限运行，请使用 sudo 或以 root 身份执行"
    fi
}

# ---------- 操作系统探测 ----------
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS_NAME="${ID:-unknown}"
        OS_VERSION="${VERSION_ID:-unknown}"
    else
        OS_NAME="$(uname -s | tr '[:upper:]' '[:lower:]')"
        OS_VERSION="$(uname -r)"
    fi
}

# ---------- 数字范围校验 ----------
is_number() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
        *) return 0 ;;
    esac
}

# ---------- 命令存在性 ----------
cmd_exists() {
    command -v "$1" >/dev/null 2>&1
}

require_cmd() {
    cmd_exists "$1" || die "依赖命令缺失: $1，请先安装"
}

fi # TAURUS_COMMON_LOADED