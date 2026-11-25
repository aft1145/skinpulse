"""中英文 README 同步（公开仓库装修 spec §4）：二级标题数量相同、图片相同、互相链接。
漂了就失败——改一边时同一个提交把另一边也改了。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ZH = (ROOT / "README.md").read_text(encoding="utf-8")
EN = (ROOT / "README.en.md").read_text(encoding="utf-8")

IMG = re.compile(r'(?:!\[[^\]]*\]\(|<img [^>]*src="|srcset=")([^)"\s]+)')


def _h2(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("## ")]


def _images(text: str) -> set[str]:
    return {m for m in IMG.findall(text) if not m.startswith("http")}


def test_same_number_of_sections():
    assert len(_h2(ZH)) == len(_h2(EN)), (_h2(ZH), _h2(EN))


def test_same_images_and_they_exist():
    assert _images(ZH) == _images(EN)
    for rel in _images(ZH):
        assert (ROOT / rel).exists(), rel


def test_they_link_to_each_other():
    assert 'href="README.en.md"' in ZH
    assert 'href="README.md"' in EN
