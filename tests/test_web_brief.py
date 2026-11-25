import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from skinpulse.db.models import BriefLog
from skinpulse.db.session import make_engine, make_session_factory, upgrade_db
from skinpulse.runtime import Runtime
from skinpulse.services.monitor import Monitor
from skinpulse.web.app import create_app


# 本地 2026-09-23 01:00（Asia/Shanghai）：send_at 00:00 已过，最近一份该有记录的是 09-22。
NOW = datetime(2026, 9, 22, 17, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch):
    import skinpulse.web.brief_page as bp
    monkeypatch.setattr(bp, "utcnow", lambda: NOW, raising=False)


def _client(tmp_path, settings, extra=()):
    db = tmp_path / "w.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    sent = datetime(2026, 9, 22, 16, 0, 5, tzinfo=timezone.utc)
    with Session() as s:
        s.add(BriefLog(date="2026-09-22", decision="sent", reasons_json='{"summary": "1 张单进入前 2"}',
                       title="SkinPulse 日报 09-22：1 张进入前 2", body="【昨天进入前 2】\n· item <A>",
                       created_at=sent, sent_at=sent))
        s.add(BriefLog(date="2026-09-21", decision="skipped",
                       reasons_json='{"summary": "没有单进入前 2（距上次发出 1 天）"}',
                       title=None, body=None, created_at=sent, sent_at=None))
        for d in extra:
            s.add(BriefLog(date=d, decision="no_data", reasons_json='{"summary": "x"}', title=None, body=None,
                           created_at=sent, sent_at=None))
        s.commit()
    rt = Runtime(settings, None, None, Session, client=None, ntfy=None,
                 monitor=Monitor(Session, None, None, settings, sleep=lambda s: None))
    return TestClient(create_app(rt), base_url=f"http://127.0.0.1:{settings.web.port}")


def test_api_brief_lists_newest_first(tmp_path, settings):
    data = _client(tmp_path, settings).get("/api/brief").json()
    assert [r["date"] for r in data["rows"]] == ["2026-09-22", "2026-09-21"]
    first = data["rows"][0]
    assert first["label"] == "已发送" and first["summary"] == "1 张单进入前 2"
    assert first["sent_at"] == "2026-09-22T16:00:05Z"   # UTC，前端按时区显示
    assert first["kind"] == "sent" and data["rows"][1]["kind"] == "not_sent"
    assert data["rows"][1]["label"].startswith("未发")


def test_days_without_a_record_are_shown_as_gaps(tmp_path, settings):
    """00:00 服务没在跑的那天没有 brief_log 行，页面上不能直接消失；最早那条之前不补。"""
    rows = _client(tmp_path, settings, extra=["2026-09-18"]).get("/api/brief").json()["rows"]
    assert [r["date"] for r in rows] == ["2026-09-22", "2026-09-21", "2026-09-20", "2026-09-19", "2026-09-18"]
    gap = rows[2]
    assert gap["label"] == "无记录" and gap["summary"] == "00:00 服务未运行，当天无判定"
    assert gap["body"] is None and gap["sent_at"] is None
    assert rows[-1]["label"] != "无记录"                      # 最早那条之前不补


def test_labels_follow_brief_top_rank(tmp_path, settings):
    """简报的「前 N」是 brief.top_rank，和实时告警的 enter_rank 分开（ADR-0008）。"""
    settings.brief.top_rank = 3
    settings.alerts.enter_rank = 1
    c = _client(tmp_path, settings)
    data = c.get("/api/brief").json()
    assert data["top_rank"] == 3 and "enter_rank" not in data
    assert data["rows"][1]["label"] == "未发（前 3 名无变动）"


def test_recent_days_without_a_record_are_shown_down_to_the_newest_row(tmp_path, settings, monkeypatch):
    """服务最近几个晚上都没在跑：从最近一份该有记录的那天（send_at 00:00 时就是本地昨天）往回补，
    直到最新那条记录。说明里的时间跟 send_at 走。"""
    import skinpulse.web.brief_page as bp

    monkeypatch.setattr(bp, "utcnow", lambda: datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc))  # 本地 09-26 01:00
    settings.brief.send_at = "00:00"
    rows = _client(tmp_path, settings).get("/api/brief").json()["rows"]
    assert [r["date"] for r in rows] == ["2026-09-25", "2026-09-24", "2026-09-23", "2026-09-22", "2026-09-21"]
    assert [r["label"] for r in rows[:3]] == ["无记录"] * 3
    assert rows[0]["summary"] == "00:00 服务未运行，当天无判定"


def test_gap_text_follows_send_at_and_a_brief_not_yet_due_is_no_gap(tmp_path, settings, monkeypatch):
    """send_at 20:00、现在本地 09-23 10:00：09-22 那份要今晚 20:00 才判，不算缺；09-21 那份昨晚就该有。"""
    import skinpulse.web.brief_page as bp

    monkeypatch.setattr(bp, "utcnow", lambda: datetime(2026, 9, 23, 2, 0, tzinfo=timezone.utc))
    settings.brief.send_at = "20:00"
    rows = _client(tmp_path, settings, extra=["2026-09-19"]).get("/api/brief").json()["rows"]
    assert [r["date"] for r in rows] == ["2026-09-22", "2026-09-21", "2026-09-20", "2026-09-19"]
    assert rows[2]["summary"] == "20:00 服务未运行，当天无判定"


def test_no_gap_rows_when_the_brief_is_disabled(tmp_path, settings, monkeypatch):
    import skinpulse.web.brief_page as bp

    monkeypatch.setattr(bp, "utcnow", lambda: datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc))
    settings.brief.enabled = False
    rows = _client(tmp_path, settings, extra=["2026-09-18"]).get("/api/brief").json()["rows"]
    assert [r["date"] for r in rows] == ["2026-09-22", "2026-09-21", "2026-09-18"]


def test_gap_rows_are_bounded_by_limit(tmp_path, settings, monkeypatch):
    """停了很久：页面只列最近 limit（30）天，不会无限补下去。"""
    import skinpulse.web.brief_page as bp

    monkeypatch.setattr(bp, "utcnow", lambda: datetime(2026, 12, 1, 17, 0, tzinfo=timezone.utc))  # 本地 12-02 01:00
    rows = _client(tmp_path, settings).get("/api/brief").json()["rows"]
    assert len(rows) == 30 and all(r["label"] == "无记录" for r in rows)
    assert rows[0]["date"] == "2026-12-01" and rows[-1]["date"] == "2026-11-02"


# ---- 看板三期：按表格展示、补发、30 天条数 ----

def _client_with(tmp_path, settings, *rows):
    db = tmp_path / "w.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    with Session() as s:
        for r in rows:
            s.add(r)
        s.commit()
    rt = Runtime(settings, None, None, Session, client=None, ntfy=None,
                 monitor=Monitor(Session, None, None, settings, sleep=lambda s: None))
    return TestClient(create_app(rt), base_url=f"http://127.0.0.1:{settings.web.port}")


def _log(date, decision="sent", sent_at=None, body=None, rows_json=None, summary="s"):
    created = datetime(2026, 9, 22, 16, 0, tzinfo=timezone.utc)
    return BriefLog(date=date, decision=decision, reasons_json=json.dumps({"summary": summary}),
                    title="t" if body else None, body=body, rows_json=rows_json, created_at=created,
                    sent_at=sent_at)


STORED = json.dumps({"sell_note": "在售：09-21 23:50 悠悠价格快照", "rows": [
    {"rank": 1, "name": "XM1014 | 幽独 (略有磨损)", "my_price": 10.0, "top1_price": 10.0, "sell_price": 13.2,
     "is_new": True, "open_rank": None, "open_top1": None, "rank_changed": False, "top1_changed": False},
    {"rank": 2, "name": "廓尔喀刀（★ StatTrak™） | 人工染色 (久经沙场)", "my_price": 158.0, "top1_price": 341.0,
     "sell_price": None, "is_new": False, "open_rank": 3, "open_top1": 329.0, "rank_changed": True, "top1_changed": True},
]})


def test_stored_table_is_returned_as_rows(tmp_path, settings):
    c = _client_with(tmp_path, settings, _log("2026-09-21", sent_at=datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc),
                                              body="whatever", rows_json=STORED))
    r = c.get("/api/brief").json()["rows"][-1]
    assert r["body"] is None and r["note"] == "在售：09-21 23:50 悠悠价格快照"
    first, second = r["table"]
    # 和推送、老记录一样用简称：磨损写两个字
    assert first == {"rank": 1, "title": "XM1014 | 幽独", "star": False, "stattrak": False, "tag": "略磨",
                     "my_price": 10.0, "top1_price": None, "is_me": True, "sell_price": 13.2, "changes": ["新进"]}
    assert (second["title"], second["star"], second["stattrak"]) == ("廓尔喀刀 | 人工染色", True, True)
    assert second["sell_price"] is None and second["changes"] == ["名次 3 → 2", "榜首 329 → 341"]


OLD_BODY = ("第1名 | XM1014 | 幽独 (略磨) | 求购 10 | 榜首 我 | 在售 13.2 | 🆕新进\n"
            "第2名 | 廓尔喀刀(★ ST) | 人工染色 (久经) | 求购 158 | 榜首 341 | 在售 — | 🆕新进，榜首 329→341\n"
            "在售：09-21 23:50 悠悠价格快照")


def test_old_records_are_parsed_back_into_a_table(tmp_path, settings):
    c = _client_with(tmp_path, settings, _log("2026-09-21", sent_at=datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc),
                                              body=OLD_BODY))
    r = c.get("/api/brief").json()["rows"][-1]
    assert r["body"] is None and r["note"] == "在售：09-21 23:50 悠悠价格快照"
    a, b = r["table"]
    assert (a["title"], a["tag"], a["is_me"], a["top1_price"], a["sell_price"], a["changes"]) == \
        ("XM1014 | 幽独", "略磨", True, None, 13.2, ["新进"])
    assert (b["title"], b["star"], b["stattrak"], b["top1_price"], b["sell_price"], b["changes"]) == \
        ("廓尔喀刀 | 人工染色", True, True, 341.0, None, ["新进", "榜首 329 → 341"])


def test_text_that_does_not_parse_is_shown_as_is(tmp_path, settings):
    body = "【昨天进入前 2】\n· 沙漠之鹰（StatTrak™） | 后发制人 (崭新出厂)"
    c = _client_with(tmp_path, settings, _log("2026-09-21", sent_at=datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc),
                                              body=body))
    r = c.get("/api/brief").json()["rows"][-1]
    assert r["table"] is None and r["body"] == body


def test_late_sends_are_marked(tmp_path, settings):
    """09-20 那份该在本地 09-21 00:00 发，09-22 10:46 才补上：kind 是 late。"""
    c = _client_with(tmp_path, settings,
                     _log("2026-09-21", sent_at=datetime(2026, 9, 21, 16, 0, 2, tzinfo=timezone.utc)),
                     _log("2026-09-20", sent_at=datetime(2026, 9, 22, 2, 46, tzinfo=timezone.utc)))
    kinds = {r["date"]: r["kind"] for r in c.get("/api/brief").json()["rows"]}
    assert kinds["2026-09-21"] == "sent" and kinds["2026-09-20"] == "late"


def test_summary_counts_briefs_sent_in_the_last_30_days(tmp_path, settings):
    sent = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)
    c = _client_with(tmp_path, settings, _log("2026-09-21", sent_at=sent), _log("2026-09-20", decision="skipped"),
                     _log("2026-09-19", decision="quiet_sent", sent_at=sent), _log("2026-08-01", sent_at=sent))
    j = c.get("/api/brief").json()
    assert j["summary"] == {"sent_30d": 2}
    assert j["next_at"] == "2026-09-23T16:00:00Z"            # 本地 09-24 00:00：09-23 那份
