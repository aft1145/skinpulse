"""把悠悠的 commodity_name 拆成看板要的几个字段（看板改版 spec §5「饰品简称」）。

悠悠的中文名形如「廓尔喀刀（★ StatTrak™） | 人工染色 (久经沙场)」：武器名后面的括号里是
★（刀和手套）和 StatTrak™，皮肤名后面的括号是磨损（涂鸦则是颜色）。看板把它们拆开：标题
「廓尔喀刀 | 人工染色」，★ 画成金色小星，StatTrak™ 和磨损进副标题。

只拆认识的东西：不认识的前缀标记（比如纪念品）原样留在标题里；没有「 | 」的名字（纪念包、
胶囊）不动结尾括号——宁可不拆，也不拆错。纯函数，不做 IO。
"""
from __future__ import annotations

import re
from typing import NamedTuple

_MARK = re.compile(r"\s*[（(]\s*(★\s*StatTrak™|★|StatTrak™)\s*[）)]")
_TAG = re.compile(r"\s*[（(]([^（）()]+)[）)]\s*$")
_BAR = re.compile(r"\s*\|\s*")


class ItemName(NamedTuple):
    title: str
    star: bool
    stattrak: bool
    tag: str | None


def split_item_name(name: str) -> ItemName:
    s = (name or "").strip()
    bar = s.find("|")
    star = stattrak = False
    m = _MARK.search(s)
    if m and (bar == -1 or m.start() < bar):
        mark = m.group(1)
        star, stattrak = "★" in mark, "StatTrak™" in mark
        s = s[:m.start()] + s[m.end():]
    tag = None
    t = _TAG.search(s)
    if t and "|" in s[:t.start()]:
        tag = t.group(1).strip()
        s = s[:t.start()]
    title = _BAR.sub(" | ", s.strip())
    return ItemName(title, star, stattrak, tag)
