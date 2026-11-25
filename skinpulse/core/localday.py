"""本地日界（settings.timezone）与 UTC 之间的换算。库里一律存 UTC aware；按「哪一天」算东西时
必须先按本地时区切日界，否则日期会差一天（CLAUDE.md 坑 8）。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def local_today(tz: str, now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz)).date()


def local_day_bounds(day: date, tz: str) -> tuple[datetime, datetime]:
    """[当天 00:00, 次日 00:00)，以 UTC aware 返回。"""
    z = ZoneInfo(tz)
    start = datetime.combine(day, time(0, 0), tzinfo=z)
    end = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=z)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def last_due_brief(tz: str, hm: tuple[int, int], now: datetime) -> tuple[date, datetime]:
    """最近一个已到点（<= now）的每日简报：返回 (它判定的日子, 到点时刻)。

    D 那份简报在 D+1 的 send_at 判定。今天的 send_at 还没到，就是昨天那次——send_at 在下午或晚上时，
    刚过零点的「最近一次」在昨天。"""
    z = ZoneInfo(tz)
    today = now.astimezone(z).date()
    for due_date in (today, today - timedelta(days=1)):
        due = datetime.combine(due_date, time(*hm), tzinfo=z)
        if due <= now:
            break
    return due_date - timedelta(days=1), due


def parse_day(s: str) -> date:
    return date.fromisoformat(s.strip())
