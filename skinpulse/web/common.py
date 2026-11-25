"""看板接口共用的小工具（没有依赖，谁都能 import，不会形成循环导入）。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal


def iso_z(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _num(d: Decimal | None) -> float | None:
    return None if d is None else float(d)
