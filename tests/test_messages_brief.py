import re
from datetime import date
from decimal import Decimal as D

import pytest

from skinpulse.analysis.brief import TopRow, TopTable
from skinpulse.core.messages import (
    BRIEF_HTML_BYTE_BUDGET, BRIEF_PRIORITY, SELL_UNKNOWN, build_brief_message, build_quiet_brief_message,
    short_item_name,
)

DAY = date(2026, 9, 22)


def row(no, rank=2, *, name=None, price="48", top="50", open_rank=2, open_top="50", sell="52",
        new=False, rank_changed=False, top1_changed=False):
    return TopRow(no, name or f"item {no}", D(price), rank, open_rank, D(top) if top else None,
                  D(open_top) if open_top else None, D(sell) if sell else None, new, rank_changed, top1_changed)


def tbl(*rows):
    return TopTable(list(rows), orders_with_data=10)


def msg(*rows, **kw):
    return build_brief_message(DAY, tbl(*rows), top_rank=2, **kw)


def test_title_counts_rows_and_changes():
    m = msg(row("A", new=True, open_rank=None, open_top=None), row("B"), row("C", rank_changed=True, open_rank=3))
    assert m.title == "SkinPulse 简报 09-22：前 2 共 3 张，2 张有变动"
    assert m.priority == BRIEF_PRIORITY


def test_plain_text_lines():
    m = msg(row("A", rank=1, price="48", top="48", new=True, open_rank=None, open_top=None),
            row("B", rank=2, price="30", top="31", open_rank=3, rank_changed=True),
            row("C", rank=2, price="20", top="248", open_top="250", sell=None, top1_changed=True),
            row("D", rank=2, price="10", top="11", open_top="11"))
    assert m.body.splitlines() == [
        "第1名 | item A | 求购 48 | 榜首 我 | 在售 52 | 🆕新进",
        "第2名 | item B | 求购 30 | 榜首 31 | 在售 52 | 名次 3→2",
        f"第2名 | item C | 求购 20 | 榜首 248 | 在售 {SELL_UNKNOWN} | 榜首 250→248",
        "第2名 | item D | 求购 10 | 榜首 11 | 在售 52",
    ]


def test_several_changes_on_one_row_are_all_shown():
    m = msg(row("A", new=True, open_rank=5, open_top="250", top="248", top1_changed=True))
    assert m.body.endswith("🆕新进，榜首 250→248")


def test_missing_sell_price_is_never_zero():
    m = msg(row("A", sell=None), row("B", sell="0"))
    assert f"在售 {SELL_UNKNOWN}" in m.body and "在售 0" not in m.body
    assert m.body.count(f"在售 {SELL_UNKNOWN}") == 2        # 0 = 没有数据（CLAUDE.md 坑 3）
    assert not re.search(r">0<", m.html)


def test_html_table_escapes_and_bolds_changed_rows():
    m = msg(row("A", name="<b>&x", new=True, open_rank=None, open_top=None), row("B"))
    h = m.html
    assert "<table" in h and h.count("<tr") == 3
    for col in ("名次", "饰品", "求购", "榜首", "在售", "变动"):
        assert f"<th>{col}</th>" in h
    assert "&lt;b&gt;&amp;x" in h and "<b>&x" not in h
    rows = re.findall(r"<tr[^>]*>.*?</tr>", h, re.S)
    assert "bold" in rows[1] and "bold" not in rows[2]
    assert "🆕新进" in rows[1]


def test_names_are_shortened_in_both_bodies():
    m = msg(row("A", name="沙漠之鹰（StatTrak™） | 后发制人 (崭新出厂)"))
    assert "沙漠之鹰(ST) | 后发制人 (崭新)" in m.body
    assert "沙漠之鹰(ST) | 后发制人 (崭新)" in m.html


def test_plain_text_is_cut_on_whole_rows():
    many = [row(f"N{i:02d}", name=f"很长很长的饰品名字 {i:02d}") for i in range(80)]
    full = msg(*many, budget=10**9).body
    cut = msg(*many, budget=600).body
    assert len(cut.encode("utf-8")) <= 600
    assert "见看板 /brief" not in full and "N79" not in cut
    kept = [line for line in cut.splitlines() if line.startswith("第")]
    assert all(line in full.splitlines() for line in kept)
    assert f"另有 {80 - len(kept)} 张未列出" in cut


def test_html_is_cut_on_whole_rows():
    many = [row(f"N{i:02d}", name=f"很长很长的饰品名字 {i:02d}") for i in range(80)]
    full = msg(*many, html_budget=10**9).html
    cut = msg(*many, html_budget=2000).html
    assert len(cut.encode("utf-8")) <= 2000 and cut.count("</table>") == 1
    kept = cut.count("<tr") - 1
    assert 0 < kept < 80 and f"另有 {80 - kept} 张未列出" in cut
    assert "未列出" not in full
    assert BRIEF_HTML_BYTE_BUDGET == 16000
    assert len(msg(*many).html.encode("utf-8")) <= BRIEF_HTML_BYTE_BUDGET


def test_quiet_message_follows_top_rank():
    m = build_quiet_brief_message(DAY, 7, 3)
    assert m.title == "SkinPulse：近 7 天前 3 名单没有变动"
    assert m.body == "近 7 天前 3 名单没有变动（新进 / 名次 / 榜首价），监控在正常运行（统计到 09-22）。"
    assert m.html is None


NOTE = "在售：09-22 23:50 悠悠价格快照"


def test_sell_note_is_the_last_line_of_both_bodies():
    m = msg(row("A"), sell_note=NOTE)
    assert m.body.splitlines() == ["第2名 | item A | 求购 48 | 榜首 50 | 在售 52", NOTE]
    assert m.html.endswith(f"</table><p>{NOTE}</p>")


def test_sell_note_is_escaped_in_html():
    m = msg(row("A"), sell_note="在售：<x>&")
    assert m.html.endswith("</table><p>在售：&lt;x&gt;&amp;</p>")
    assert m.body.endswith("在售：<x>&")


def test_sell_note_survives_truncation():
    many = [row(f"N{i:02d}", name=f"很长很长的饰品名字 {i:02d}") for i in range(80)]
    cut = msg(*many, budget=600, html_budget=2000, sell_note=NOTE)
    assert len(cut.body.encode("utf-8")) <= 600 and len(cut.html.encode("utf-8")) <= 2000
    lines = cut.body.splitlines()
    assert lines[-1] == NOTE and "未列出" in lines[-2]
    assert cut.html.endswith(f"<p>{NOTE}</p>") and "未列出" in cut.html
    full = msg(*many, budget=10**9, html_budget=10**9, sell_note=NOTE)
    assert full.body.splitlines()[-1] == NOTE and "未列出" not in full.body


@pytest.mark.parametrize("name, short", [
    ("AK-47 | 红线 (崭新出厂)", "AK-47 | 红线 (崭新)"),
    ("AK-47 | 红线 (略有磨损)", "AK-47 | 红线 (略磨)"),
    ("AK-47 | 红线 (久经沙场)", "AK-47 | 红线 (久经)"),
    ("AK-47 | 红线 (破损不堪)", "AK-47 | 红线 (破损)"),
    ("AK-47 | 红线 (战痕累累)", "AK-47 | 红线 (战痕)"),
    ("沙漠之鹰（StatTrak™） | 后发制人 (崭新出厂)", "沙漠之鹰(ST) | 后发制人 (崭新)"),
    ("廓尔喀刀（★ StatTrak™）", "廓尔喀刀(★ ST)"),
    ("印花 | 某战队（全息）", "印花 | 某战队(全息)"),
    ("音乐盒 | 某某", "音乐盒 | 某某"),
])
def test_short_item_name(name, short):
    assert short_item_name(name) == short
