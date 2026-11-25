from datetime import date, datetime, timezone

import pytest

from skinpulse.core.localday import local_day_bounds, local_today, parse_day


def test_day_bounds_follow_the_local_timezone():
    start, end = local_day_bounds(date(2026, 9, 22), "Asia/Shanghai")
    assert start == datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 22, 16, 0, tzinfo=timezone.utc)


def test_local_today_crosses_midnight_before_utc_does():
    assert local_today("Asia/Shanghai", datetime(2026, 9, 22, 17, 0, tzinfo=timezone.utc)) == date(2026, 9, 23)


def test_parse_day():
    assert parse_day(" 2026-09-22 ") == date(2026, 9, 22)
    with pytest.raises(ValueError):
        parse_day("09-22")
