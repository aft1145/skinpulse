"""把悠悠的 commodity_name 拆成看板要的几个字段（看板改版 spec §5「饰品简称」）。"""
from __future__ import annotations

import pytest

from skinpulse.core.names import ItemName, split_item_name


@pytest.mark.parametrize("raw, expected", [
    ("廓尔喀刀（★ StatTrak™） | 人工染色 (久经沙场)", ItemName("廓尔喀刀 | 人工染色", True, True, "久经沙场")),
    ("暗影双匕（★） | 北方森林 (崭新出厂)", ItemName("暗影双匕 | 北方森林", True, False, "崭新出厂")),
    ("九头蛇手套（★） | 表面淬火 (略有磨损)", ItemName("九头蛇手套 | 表面淬火", True, False, "略有磨损")),
    ("CZ75（StatTrak™） | 短趾雕 (崭新出厂)", ItemName("CZ75 | 短趾雕", False, True, "崭新出厂")),
    ("AWP | 复古流行 (破损不堪)", ItemName("AWP | 复古流行", False, False, "破损不堪")),
    # 音乐盒没有磨损；名字里的 * 和 - 原样保留
    ("音乐盒（StatTrak™） | Sean Murray - A*D*8", ItemName("音乐盒 | Sean Murray - A*D*8", False, True, None)),
    # 涂鸦结尾的括号是颜色，不是磨损，同样拆成 tag
    ("封装的涂鸦 | 傻瓜 (藕色)", ItemName("封装的涂鸦 | 傻瓜", False, False, "藕色")),
    # 半角括号一样认
    ("M4A1 消音型(StatTrak™) | 次时代 (崭新出厂)", ItemName("M4A1 消音型 | 次时代", False, True, "崭新出厂")),
])
def test_split_item_name(raw, expected):
    assert split_item_name(raw) == expected


def test_unrecognized_marks_stay_in_the_title():
    """纪念品等不认识的前缀标记原样留在标题里，宁可不拆也不拆错。"""
    got = split_item_name("AWP（纪念品） | 地狱之门 (略有磨损)")
    assert got.title == "AWP（纪念品） | 地狱之门"
    assert (got.star, got.stattrak, got.tag) == (False, False, "略有磨损")


def test_names_without_a_skin_part_are_left_alone():
    """没有「 | 」的（纪念包、胶囊）不拆结尾括号：那里的括号不是磨损。"""
    assert split_item_name("2013年 DreamHack 纪念包") == ItemName("2013年 DreamHack 纪念包", False, False, None)
    assert split_item_name("") == ItemName("", False, False, None)
