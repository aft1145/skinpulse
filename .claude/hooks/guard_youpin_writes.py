"""PreToolUse hook: refuse edits that put a YouPin or BUFF write endpoint into code.

SkinPulse is read-only against YouPin and BUFF (CLAUDE.md 工程约定, BUFF spec §3). The
endpoints that place, change or cancel a buy order, list or buy an item, or confirm a
trade must never appear outside documentation. The patterns are assembled from parts so
this file does not trip itself. (The file keeps its old name: renaming a hook the running
session already points at would block every edit until the session reloads.)

Exit 2 blocks the tool call and shows stderr to Claude.
"""
import json
import re
import sys
from pathlib import Path

FORBIDDEN = re.compile(
    r"(save|update|delete)" + "Purchase" + "Order"
    # BUFF：求购 / 出售单的增删改、直接购买、订单确认 / 发货 / 取消
    + r"|/api/market/(buy" + r"_order|sell" + r"_order)/(create|cancel|change|update|preview|delist)"
    + r"|/api/market/goods/" + r"buy\b"
    + r"|bill" + r"_order/(batch/)?(confirm|deliver|cancel|pay)"
)
EXEMPT_SUFFIXES = {".md"}


def new_text(tool_input: dict) -> str:
    parts = [tool_input.get("content") or "", tool_input.get("new_string") or ""]
    parts += [e.get("new_string") or "" for e in tool_input.get("edits") or []]
    return "\n".join(parts)


def main() -> int:
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    tool_input = payload.get("tool_input") or {}
    path = tool_input.get("file_path") or ""
    if Path(path).suffix.lower() in EXEMPT_SUFFIXES:
        return 0
    hit = FORBIDDEN.search(new_text(tool_input))
    if not hit:
        return 0
    sys.stderr.reconfigure(encoding="utf-8")
    print(f"已拦截：{path} 的改动里出现了写接口 {hit.group(0)}。"
          "SkinPulse 对悠悠、BUFF 只读（CLAUDE.md 工程约定），下单、改价、撤单、购买、确认交易的接口一个都不能进代码。",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
