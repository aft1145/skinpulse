"""`POST /api/settings`：看板系统页的参数设置（看板 spec §4.6、§5；三期）。

- 只收 `[polling]`、`[alerts]`、`[brief]`、`[buff]`、`[dashboard]`（只开放 `EDITABLE_KEYS` 里的项），别的组（悠悠请求头、端口……）一律 422：那些改错了
  服务会连不上悠悠或者看板打不开，只在 settings.toml 里改。组里不认识的字段也 422，免得拼错了
  被悄悄忽略。
- 返回新设置和「改了哪些项」；校验失败时逐项给出字段和中文说明，前端按字段标红。
- 通过校验才落地：换 rt.settings、推给正在跑的 Monitor、写回 settings.toml、按新值重排调度。
"""
from __future__ import annotations

import logging

from pydantic import ValidationError

from skinpulse.config import Settings, save_settings

log = logging.getLogger(__name__)

EDITABLE = ("polling", "alerts", "brief", "buff", "dashboard")
# 组里只开放这几项（别的项改错了连不上平台，只在 settings.toml 里改）。不在这里的组全部开放
EDITABLE_KEYS = {"buff": {"round_interval_min", "request_gap_s", "queue_per_round", "queue_max_pages", "pause_hours"}}


class SettingsError(Exception):
    def __init__(self, errors: list[dict]) -> None:
        super().__init__("invalid settings")
        self.errors = errors


def _message(err: dict, merged: dict) -> tuple[str, str]:
    """pydantic 的一条错误 → (字段, 中文说明)。"""
    loc = [str(x) for x in err.get("loc", ())]
    field = ".".join(loc)
    kind, ctx = err.get("type", ""), err.get("ctx") or {}
    if loc == ["alerts"] and "exit_rank" in str(err.get("msg", "")):
        return "alerts.exit_rank", f"要大于「升到第几名推送」（{merged['alerts'].get('enter_rank')}）"
    if kind == "greater_than_equal":
        return field, f"不能小于 {ctx.get('ge')}"
    if kind == "less_than_equal":
        return field, f"不能大于 {ctx.get('le')}"
    if kind in ("int_parsing", "float_parsing", "int_from_float", "int_type", "float_type"):
        return field, "要填数字" if kind != "int_from_float" else "要填整数"
    if kind in ("bool_parsing", "bool_type"):
        return field, "要选开或关"
    if loc and loc[-1] in ("send_at", "snapshot_at"):
        return field, "格式是 HH:MM，比如 08:00"
    return field, str(err.get("msg", "不合要求"))


def apply_patch(rt, sched, patch) -> dict:
    if not isinstance(patch, dict) or not patch:
        raise SettingsError([{"field": "", "message": "要提交一个对象，比如 {\"polling\": {…}}"}])
    errors = []
    for section, values in patch.items():
        if section not in EDITABLE:
            errors.append({"field": section, "message": "这一组不能在看板上改，只能改 settings.toml"})
            continue
        if not isinstance(values, dict):
            errors.append({"field": section, "message": "要提交一个对象"})
            continue
        known = type(getattr(rt.settings, section)).model_fields
        errors += [{"field": f"{section}.{k}", "message": "不存在这一项"} for k in values if k not in known]
        allowed = EDITABLE_KEYS.get(section)
        if allowed is not None:
            errors += [{"field": f"{section}.{k}", "message": "这一项不能在看板上改，只能改 settings.toml"}
                       for k in values if k in known and k not in allowed]
    if errors:
        raise SettingsError(errors)

    before = rt.settings.model_dump(mode="json")
    merged = rt.settings.model_dump(mode="json")
    for section, values in patch.items():
        merged[section].update(values)
    try:
        new = Settings.model_validate(merged)
    except ValidationError as e:
        raise SettingsError([dict(zip(("field", "message"), _message(err, merged)))
                             for err in e.errors()]) from None

    after = new.model_dump(mode="json")
    changed = [f"{sec}.{k}" for sec in EDITABLE for k in after[sec] if after[sec][k] != before[sec][k]]
    rt.settings = new
    rt.monitor.apply_settings(new)
    save_settings(new, rt.settings_path)
    if sched is not None:
        from skinpulse.scheduler import reschedule
        reschedule(sched, rt, brief=any(c.startswith("brief.") for c in changed))
        if any(c.startswith("buff.") for c in changed):
            from skinpulse.scheduler import sync_buff_job
            sync_buff_job(sched, rt)
    if any(c.startswith("buff.") for c in changed):
        # BuffClient 建的时候拿走了一份 [buff]（请求间隔、请求头）：换成新的，不用重启（评审 2026-11-24）
        from skinpulse.runtime import load_buff_accounts
        try:
            load_buff_accounts(rt)
        except Exception as e:  # noqa: BLE001 —— 设置已经存好；下次加号或重启时会再装一次
            log.warning("reloading BUFF accounts after a settings change failed (%s)", type(e).__name__)
    return {"settings": after, "changed": changed}
