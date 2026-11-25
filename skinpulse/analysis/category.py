"""饰品品类：从市场英文名（`item.hash_name`）推出大类和第二层（品类筛选 intent 2026-10-09）。

两层：
- 大类（`group`）：步枪、狙击枪、手枪、微冲、重型、刀、手套、印花……顺序就是看板上的顺序；
- 第二层（`sub`）：只有枪、刀、手套有，是英文的武器名 / 刀型 / 手套类型（AK-47、Karambit、
  Sport Gloves），当作稳定的键。显示用的中文名由调用方从悠悠中文名里取（`sub_label`），这里不写死。

StatTrak™、纪念品不单独分：先去掉前缀再判断，StatTrak™ AK-47 就在「步枪 › AK-47」里。
认不出来的（新武器、工具、名字缺失）一律进「其他」，不报错——补一行常量即可。纯函数，不做 IO。
"""
from __future__ import annotations

import re
from typing import NamedTuple

from skinpulse.core.names import split_item_name

# 看板上的顺序：枪按大类，刀、手套紧随其后，再是非枪械，最后「其他」。
GROUPS: tuple[tuple[str, str], ...] = (
    ("rifle", "步枪"),
    ("sniper", "狙击枪"),
    ("pistol", "手枪"),
    ("smg", "微冲"),
    ("heavy", "重型"),
    ("knife", "刀"),
    ("gloves", "手套"),
    ("sticker", "印花"),
    ("music_kit", "音乐盒"),
    ("graffiti", "涂鸦"),
    ("charm", "挂件"),
    ("patch", "布章"),
    ("agent", "探员"),
    ("container", "武器箱 / 胶囊"),
    ("other", "其他"),
)
GROUP_LABELS = dict(GROUPS)

_WEAPONS: dict[str, tuple[str, ...]] = {
    "rifle": ("AK-47", "M4A4", "M4A1-S", "FAMAS", "Galil AR", "AUG", "SG 553"),
    "sniper": ("AWP", "SSG 08", "SCAR-20", "G3SG1"),
    "pistol": ("Glock-18", "USP-S", "P2000", "P250", "Five-SeveN", "Tec-9", "CZ75-Auto",
               "Desert Eagle", "R8 Revolver", "Dual Berettas"),
    "smg": ("MP9", "MAC-10", "MP7", "MP5-SD", "UMP-45", "P90", "PP-Bizon"),
    "heavy": ("Nova", "XM1014", "Sawed-Off", "MAG-7", "M249", "Negev"),
}
WEAPON_GROUP = {w: g for g, ws in _WEAPONS.items() for w in ws}

# 「印花 | ……」这类：` | ` 前面那段直接决定大类
_PREFIX_GROUP = {
    "Sticker": "sticker",
    "Music Kit": "music_kit",
    "Sealed Graffiti": "graffiti",
    "Graffiti": "graffiti",            # 未封装的涂鸦（2026-11-23 补：以前落进了探员）
    "Charm": "charm",
    "Sticker Slab": "charm",           # 印花封装：CS2 里是挂件的一种
    "Autograph Capsule": "container",  # 「签名胶囊 | 战队 | 赛事」：不以 Capsule 结尾，正则认不出
    "Patch": "patch",
}
_MARKS = re.compile(r"^(★\s*)?(StatTrak™\s*|Souvenir\s*)?")
_GLOVES = re.compile(r"Gloves|Hand Wraps")
_CONTAINER = re.compile(r"(Case|Capsule|Package|Pack)$")
_WEAR = re.compile(r"\((Factory New|Minimal Wear|Field-Tested|Well-Worn|Battle-Scarred)\)$")


class Category(NamedTuple):
    group: str
    sub: str | None


def categorize(hash_name: str | None) -> Category:
    s = (hash_name or "").strip()
    if not s:
        return Category("other", None)
    star = s.startswith("★")
    s = _MARKS.sub("", s, count=1).strip()
    head = s.split(" | ", 1)[0].strip()
    if star:
        return Category("gloves" if _GLOVES.search(head) else "knife", head)
    if head in WEAPON_GROUP:
        return Category(WEAPON_GROUP[head], head)
    if head in _PREFIX_GROUP and " | " in s:
        return Category(_PREFIX_GROUP[head], None)
    if _CONTAINER.search(s):
        return Category("container", None)
    # 探员：「名字 | 阵营」，没有磨损括号；武器、印花这些上面已经认走了
    if " | " in s and not _WEAR.search(s):
        return Category("agent", None)
    return Category("other", None)


_CN_MARK = re.compile(r"[（(]\s*(纪念品|★|StatTrak™)[^（）()]*[）)]")


def sub_label(commodity_name: str | None, sub: str | None) -> str | None:
    """第二层的显示名：悠悠中文名 ` | ` 前面那段（「AK-47」「爪子刀」「运动手套」），去掉 ★、
    StatTrak™、纪念品标记。中文名缺失就用英文键。"""
    if sub is None:
        return None
    head = split_item_name(commodity_name or "").title.split(" | ", 1)[0]
    head = _CN_MARK.sub("", head).replace("★", "").replace("StatTrak™", "").strip()
    return head or sub
