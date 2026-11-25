from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from skinpulse.db.models import SystemStatus
from skinpulse.db.session import utcnow


def _row(session: Session, adapter: str) -> SystemStatus:
    row = session.get(SystemStatus, adapter)
    if row is None:
        row = SystemStatus(adapter=adapter, error_counts_json="{}")
        session.add(row)
    return row


def mark_ok(session: Session, adapter: str) -> None:
    row = _row(session, adapter)
    row.last_ok_at = utcnow()
    row.paused_until = None
    row.updated_at = utcnow()
    session.commit()


def mark_error(session: Session, adapter: str, code: str, paused_until: datetime | None) -> None:
    row = _row(session, adapter)
    counts = json.loads(row.error_counts_json or "{}")
    counts[code] = counts.get(code, 0) + 1
    row.error_counts_json = json.dumps(counts)
    row.last_error_code = code
    row.last_error_at = utcnow()
    row.paused_until = paused_until
    row.updated_at = utcnow()
    session.commit()


def get_status(session: Session, adapter: str) -> SystemStatus | None:
    return session.get(SystemStatus, adapter)


def is_paused(session: Session, adapter: str, now: datetime | None = None) -> bool:
    row = session.get(SystemStatus, adapter)
    if row is None or row.paused_until is None:
        return False
    return row.paused_until > (now or utcnow())  # UtcDateTime 保证两侧都是 aware
