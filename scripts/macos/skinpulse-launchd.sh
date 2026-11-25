#!/bin/bash
# launchd 实际执行的程序：由 launchagent.sh install 写进 plist，不需要手动跑。见 spec §11.1、ADR-0010。
set -eu

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

# 接着电源时阻止系统睡眠（屏幕照常熄灭；用电池或合盖时不起作用）。-w 盯着 $$：
# 下一行 exec 之后 $$ 就是服务进程本身，服务一退出，caffeinate 跟着退出。
/usr/bin/caffeinate -s -w $$ &

# exec 而不是 uv run：launchd 盯的 pid 就是 python，停止时的 SIGTERM 直接送到服务；
# launchd 的 PATH 里也不需要有 uv。代价是拉新代码后要照常 uv sync。
# 看板系统页据此显示「开机自启 · 崩溃 30 秒内拉起 · 接电源防睡眠」
export SKINPULSE_SUPERVISOR=launchd
exec ./.venv/bin/python -m skinpulse run --console-level WARNING
