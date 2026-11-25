from __future__ import annotations

import base64
import json
import secrets
import string

from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad, unpad

# 线上环境公钥：取自悠悠官网前端（umi.js `jt==="online"` 分支）。同一把公钥也出现在 Steamauto 的 uuyoupinapi 里。
YOUPIN_RSA_PUBLIC_KEY = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAv9BDdhCDahZNFuJeesx3gzoQfD7pE0AeWiNBZlc21ph6kU9zd58X/1warV3C1VIX0vMAmhOcj5u86i+L2Lb2V68dX2Nb70MIDeW6Ibe8d0nF8D30tPsM7kaAyvxkY6ECM6RHGNhV4RrzkHmf5DeR9bybQGE0A9jcjuxszD1wsW/n19eeom7MroHqlRorp5LLNR8bSbmhTw6M/RQ/Fm3lKjKcvs1QNVyBNimrbD+ZVPE/KHSZLQ1jdF6tppvFnGxgJU9NFmxGFU0hx6cZiQHkhOQfGDFkElxgtj8gFJ1narTwYbvfe5nGSiznv/EUJSjTHxzX1TEkex0+5j4vSANt1QIDAQAB\n"
    "-----END PUBLIC KEY-----"
)
_ALNUM = string.ascii_letters + string.digits


def random_aes_key() -> bytes:
    return "".join(secrets.choice(_ALNUM) for _ in range(16)).encode("ascii")


class UkCipher:
    def __init__(self, aes_key: bytes) -> None:
        if len(aes_key) != 16:
            raise ValueError("aes_key must be 16 bytes")
        self.aes_key = aes_key

    def encrypt(self, plaintext: str) -> str:
        aes = AES.new(self.aes_key, AES.MODE_ECB)
        return base64.b64encode(aes.encrypt(pad(plaintext.encode("utf-8"), AES.block_size))).decode("ascii")

    def decrypt(self, b64: str) -> str:
        aes = AES.new(self.aes_key, AES.MODE_ECB)
        return unpad(aes.decrypt(base64.b64decode(b64)), AES.block_size).decode("utf-8")

    def encrypted_aes_key(self) -> str:
        rsa = PKCS1_v1_5.new(RSA.import_key(YOUPIN_RSA_PUBLIC_KEY))
        return base64.b64encode(rsa.encrypt(self.aes_key)).decode("ascii")


def build_device_w2_payload(cipher: UkCipher, iud: str) -> dict:
    return {
        "encryptedData": cipher.encrypt(json.dumps({"iud": iud}, separators=(",", ":"))),
        "encryptedAesKey": cipher.encrypted_aes_key(),
    }


def parse_device_w2_response(cipher: UkCipher, body: str) -> tuple[str, str]:
    data = json.loads(cipher.decrypt(body.strip().strip('"')))
    return data["u"], data.get("deviceUk", "")
