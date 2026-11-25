"""从悠悠 token（JWT）里读到期时间。只解 payload、不验签；token 本身绝不外传。"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

from skinpulse.adapters.youpin.token import jwt_expiry


def _jwt(payload: dict) -> str:
    def seg(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{seg({'typ': 'JWT', 'alg': 'HS256'})}.{seg(payload)}.c2lnbmF0dXJl"


def test_reads_exp_as_utc():
    assert jwt_expiry(_jwt({"exp": 1791187308, "Id": 1})) == datetime.fromtimestamp(1791187308, tz=timezone.utc)


def test_payload_needing_padding_decodes():
    # 长度不是 4 的倍数的段，要补 = 才能解
    for extra in ("", "x", "xy", "xyz"):
        assert jwt_expiry(_jwt({"exp": 1791187308, "p": extra})) is not None


def test_anything_unreadable_is_none():
    for bad in (None, "", "not-a-jwt", "a.b.c", _jwt({"no_exp": 1}), _jwt({"exp": "soon"}), "Bearer x.y.z"):
        assert jwt_expiry(bad) is None
