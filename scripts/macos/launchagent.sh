#!/bin/bash
# SkinPulse 的 macOS 开机自启（用户级 LaunchAgent，不需要管理员）。见 spec §11.1、ADR-0010。
#
#   scripts/macos/launchagent.sh install     生成 plist 并启动；重复执行无害（会先卸载旧的）
#   scripts/macos/launchagent.sh status      launchd 状态 + launchd.log 最后 20 行
#   scripts/macos/launchagent.sh restart     重启（git pull + uv sync 之后用）
#   scripts/macos/launchagent.sh stop        停掉；下次登录会自动恢复
#   scripts/macos/launchagent.sh start       stop 之后重新启动
#   scripts/macos/launchagent.sh uninstall   停掉并删除 plist，以后登录也不再启动
#   scripts/macos/launchagent.sh render F    只把 plist 写到文件 F（测试和排查用）
#
# 登录即起（RunAtLoad）、崩溃或退出后 30 秒内自动拉起（KeepAlive + ThrottleInterval）。
# 服务的完整日志在 data/logs/skinpulse.jsonl；launchd.log 只有警告、报错和 traceback。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
LABEL="local.skinpulse"
DOMAIN="gui/$(id -u)"
SERVICE="${DOMAIN}/${LABEL}"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
WRAPPER="${ROOT}/scripts/macos/skinpulse-launchd.sh"
LOG="${ROOT}/data/logs/launchd.log"

die() {
    echo "launchagent.sh: $*" >&2
    exit 1
}

is_loaded() {
    launchctl print "${SERVICE}" >/dev/null 2>&1
}

# bootout 是异步的：紧接着 bootstrap 可能报 "5: Input/output error"，所以等它真的卸载完。
wait_unloaded() {
    local i
    for i in $(seq 1 150); do
        is_loaded || return 0
        sleep 0.2
    done
    die "等了 30 秒，${SERVICE} 还没卸载完，稍后再试"
}

unload_if_loaded() {
    if is_loaded; then
        launchctl bootout "${SERVICE}" || true
        wait_unloaded
    fi
}

# 用 plutil 逐项写入：每个值都是单独的参数，仓库路径里有空格或 & 也不用手动转义。
render() {
    local out="$1"
    rm -f "$out"
    plutil -create xml1 "$out"
    plutil -insert Label -string "${LABEL}" "$out"
    plutil -insert ProgramArguments -array "$out"
    plutil -insert ProgramArguments.0 -string /bin/bash "$out"  # 不依赖脚本的可执行位
    plutil -insert ProgramArguments.1 -string "${WRAPPER}" "$out"
    plutil -insert WorkingDirectory -string "${ROOT}" "$out"
    plutil -insert RunAtLoad -bool true "$out"
    plutil -insert KeepAlive -bool true "$out"
    plutil -insert ThrottleInterval -integer 30 "$out"
    plutil -insert StandardOutPath -string "${LOG}" "$out"
    plutil -insert StandardErrorPath -string "${LOG}" "$out"
    plutil -lint "$out" >/dev/null
}

cmd_install() {
    [ -x "${ROOT}/.venv/bin/python" ] || die "找不到 ${ROOT}/.venv/bin/python：先在仓库根目录跑 uv sync"
    [ -f "${ROOT}/settings.toml" ] || die "找不到 ${ROOT}/settings.toml：先把配置放好（见 docs/guide.md）"
    mkdir -p "${ROOT}/data/logs" "$(dirname "${PLIST}")"  # launchd 不会替你建日志目录
    # 先写到临时文件，完整写好才放进 LaunchAgents：写到一半失败的 plist 下次登录会被 launchd 照着启动
    PLIST_TMP="$(mktemp -t skinpulse-plist)"
    trap 'rm -f "${PLIST_TMP:-}"' EXIT  # EXIT 时函数的 local 已经没了，所以用全局变量
    render "${PLIST_TMP}"
    mv -f "${PLIST_TMP}" "${PLIST}"
    launchctl enable "${SERVICE}"  # 以前被 launchctl disable 过的话，不 enable 就 bootstrap 不上
    unload_if_loaded
    launchctl bootstrap "${DOMAIN}" "${PLIST}"
    echo "已安装并启动：${SERVICE}"
    echo "plist：${PLIST}"
    echo "如果终端里还开着手动跑的 skinpulse run，这边会每 30 秒重试一次，等它退出后接管。"
    echo "看状态：scripts/macos/launchagent.sh status"
}

cmd_uninstall() {
    unload_if_loaded
    rm -f "${PLIST}"
    echo "已停止并删除 ${PLIST}，以后登录不会再自动启动。"
}

cmd_start() {
    [ -f "${PLIST}" ] || die "还没安装：先跑 scripts/macos/launchagent.sh install"
    if is_loaded; then
        launchctl kickstart "${SERVICE}"
    else
        launchctl bootstrap "${DOMAIN}" "${PLIST}"
    fi
    echo "已启动：${SERVICE}"
}

cmd_stop() {
    if is_loaded; then
        launchctl bootout "${SERVICE}" || true
        wait_unloaded
        echo "已停止：${SERVICE}（下次登录会自动恢复；现在就要恢复用 start）"
    else
        echo "${SERVICE} 没在运行。"
    fi
}

cmd_restart() {
    is_loaded || die "${SERVICE} 没在运行：用 start（或 install）"
    launchctl kickstart -k "${SERVICE}"
    echo "已重启：${SERVICE}"
}

cmd_status() {
    if is_loaded; then
        echo "${SERVICE} 已加载："
        launchctl print "${SERVICE}" | grep -E '^[[:space:]]*(state|pid|runs|last exit code) = ' || true
    else
        echo "${SERVICE} 未加载（没 install，或者被 stop 了）。"
    fi
    if [ -f "${LOG}" ]; then
        echo
        echo "== ${LOG}（最后 20 行）=="
        tail -n 20 "${LOG}"
    fi
}

case "${1:-}" in
    install) cmd_install ;;
    uninstall) cmd_uninstall ;;
    start) cmd_start ;;
    stop) cmd_stop ;;
    restart) cmd_restart ;;
    status) cmd_status ;;
    render)
        [ -n "${2:-}" ] || die "用法：launchagent.sh render <输出文件>"
        render "$2"
        ;;
    *)
        sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
        exit 2
        ;;
esac
