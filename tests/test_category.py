"""饰品品类（品类筛选 intent 2026-10-09）。名字都取自 2026-10-09 库里在册单的真实英文名 / 中文名。"""
from __future__ import annotations

import pytest

from skinpulse.analysis.category import GROUP_LABELS, GROUPS, Category, categorize, sub_label


@pytest.mark.parametrize("hash_name, expected", [
    ("AK-47 | Slate (Factory New)", Category("rifle", "AK-47")),
    ("M4A1-S | Nitro (Field-Tested)", Category("rifle", "M4A1-S")),
    ("AWP | Pit Viper (Minimal Wear)", Category("sniper", "AWP")),
    ("SSG 08 | Dezastre (Factory New)", Category("sniper", "SSG 08")),
    ("R8 Revolver | Crazy 8 (Factory New)", Category("pistol", "R8 Revolver")),
    ("Desert Eagle | Light Rail (Factory New)", Category("pistol", "Desert Eagle")),
    ("PP-Bizon | Runic (Field-Tested)", Category("smg", "PP-Bizon")),
    ("Sawed-Off | Kiss♥Love (Field-Tested)", Category("heavy", "Sawed-Off")),
    # StatTrak™、纪念品不单独分
    ("StatTrak™ Dual Berettas | Flora Carnivora (Field-Tested)", Category("pistol", "Dual Berettas")),
    ("Souvenir FAMAS | Colony (Well-Worn)", Category("rifle", "FAMAS")),
    # ★：刀和手套，第二层是刀型 / 手套类型；StatTrak™ 在 ★ 后面
    ("★ Karambit | Doppler (Factory New)", Category("knife", "Karambit")),
    ("★ StatTrak™ Kukri Knife | Case Hardened (Field-Tested)", Category("knife", "Kukri Knife")),
    ("★ Karambit", Category("knife", "Karambit")),
    ("★ Sport Gloves | Vice (Field-Tested)", Category("gloves", "Sport Gloves")),
    ("★ Hand Wraps | Slaughter (Minimal Wear)", Category("gloves", "Hand Wraps")),
    # 前缀决定大类；印花有两个 ` | `
    ("Sticker | Clown Nose", Category("sticker", None)),
    ("Sticker | magixx (Gold) | Austin 2025", Category("sticker", None)),
    ("StatTrak™ Music Kit | Jonathan Young, Starship Velociraptor", Category("music_kit", None)),
    ("Music Kit | The Verkkars, EZ4ENCE", Category("music_kit", None)),
    ("Sealed Graffiti | Sheriff (Battle Green)", Category("graffiti", None)),
    # 2026-11-23：这三种以前落进了「探员」的兜底（探员 4406 个，真探员只有约 60 个）
    ("Graffiti | Loser (Blood Red)", Category("graffiti", None)),
    ("Sticker Slab | NiKo | Copenhagen 2024", Category("charm", None)),
    ("Autograph Capsule | Cloud9 G2A | Cologne 2015", Category("container", None)),
    ("Charm | Lil' Crass", Category("charm", None)),
    ("Patch | Metal Distinguished Master Guardian", Category("patch", None)),
    # 探员：有 ` | `、没有磨损、前面不是武器
    ("'Two Times' McCoy | TACP Cavalry", Category("agent", None)),
    ("Dragomir | Sabre Footsoldier", Category("agent", None)),
    # 箱子、胶囊、纪念包、布章包
    ("Spectrum Case", Category("container", None)),
    ("eSports 2013 Winter Case", Category("container", None)),
    ("Sticker Capsule", Category("container", None)),
    ("ESL One Cologne 2014 Cobblestone Souvenir Package", Category("container", None)),
    ("CS:GO Patch Pack", Category("container", None)),
    # 认不出来的进「其他」，不报错
    ("Zeus x27 | Tosai (Factory New)", Category("other", None)),
    ("StatTrak™ Swap Tool", Category("other", None)),
    ("", Category("other", None)),
    (None, Category("other", None)),
])
def test_categorize(hash_name, expected):
    assert categorize(hash_name) == expected


def test_every_group_has_a_label_and_other_is_last():
    assert [k for k, _ in GROUPS][-1] == "other"
    assert len(GROUP_LABELS) == len(GROUPS)
    for name in ("AK-47 | Slate (Factory New)", "★ Karambit", "Sticker | Clown Nose", "Spectrum Case", ""):
        assert categorize(name).group in GROUP_LABELS


@pytest.mark.parametrize("cn, sub, expected", [
    ("AK-47 | 墨岩 (崭新出厂)", "AK-47", "AK-47"),
    ("双持贝瑞塔（StatTrak™） | 瓜瓜 (久经沙场)", "Dual Berettas", "双持贝瑞塔"),
    ("廓尔喀刀（★ StatTrak™） | 人工染色 (久经沙场)", "Kukri Knife", "廓尔喀刀"),
    ("运动手套（★） | 迈阿密风云 (久经沙场)", "Sport Gloves", "运动手套"),
    ("法玛斯（纪念品） | 殖民侵略者 (破损不堪)", "FAMAS", "法玛斯"),
    ("R8 左轮手枪 | 疯狂老八 (崭新出厂)", "R8 Revolver", "R8 左轮手枪"),
    # 中文名缺失就用英文键；没有第二层就是 None
    ("", "AWP", "AWP"),
    (None, "AWP", "AWP"),
    ("印花 | 小丑鼻子", None, None),
])
def test_sub_label(cn, sub, expected):
    assert sub_label(cn, sub) == expected
