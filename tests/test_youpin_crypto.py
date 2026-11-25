import base64
import json

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from skinpulse.adapters.youpin.crypto import (
    UkCipher, build_device_w2_payload, parse_device_w2_response, random_aes_key,
)


def test_random_key_is_16_alnum_bytes():
    k = random_aes_key()
    assert len(k) == 16 and k.isalnum()


def test_aes_roundtrip():
    c = UkCipher(b"0123456789abcdef")
    assert c.decrypt(c.encrypt('{"iud":"x"}')) == '{"iud":"x"}'


def test_encrypted_aes_key_is_rsa2048_block():
    c = UkCipher(b"0123456789abcdef")
    assert len(base64.b64decode(c.encrypted_aes_key())) == 256


def test_payload_and_response_parse():
    c = UkCipher(b"0123456789abcdef")
    payload = build_device_w2_payload(c, "11111111-2222-3333-4444-555555555555")
    assert set(payload) == {"encryptedData", "encryptedAesKey"}
    assert json.loads(c.decrypt(payload["encryptedData"])) == {"iud": "11111111-2222-3333-4444-555555555555"}
    # 模拟服务端：用同一 AES key 加密 {"u":..., "deviceUk":...}
    aes = AES.new(b"0123456789abcdef", AES.MODE_ECB)
    body = base64.b64encode(aes.encrypt(pad(json.dumps({"u": "UK" * 32 + "X", "deviceUk": "D" * 65}).encode(), 16))).decode()
    uk, duk = parse_device_w2_response(c, body)
    assert uk.startswith("UK") and len(uk) == 65 and duk == "D" * 65
