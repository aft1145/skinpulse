from __future__ import annotations

from pathlib import Path

import keyring  # 模块级引用，便于测试替换

SERVICE = "skinpulse"
KEY_YOUPIN_TOKEN = "youpin_token"
KEY_YOUPIN_PHONE = "youpin_phone"
KEY_NTFY_TOPIC = "ntfy_topic"
KEY_NTFY_TOKEN = "ntfy_token"
KEY_STEAMDT = "steamdt_api_key"
KEY_PUSHPLUS = "pushplus_token"
KEY_BUFF_COOKIE = "buff_cookie"


def youpin_token_key(account_id: int) -> str:
    """每个悠悠号的 token 在钥匙串里的 key（多账号 spec §3）。号 1 沿用老 key，老用户什么都不用搬。"""
    return KEY_YOUPIN_TOKEN if account_id == 1 else f"{KEY_YOUPIN_TOKEN}:{account_id}"


def buff_cookie_key(account_id: int) -> str:
    """BUFF 号的 session cookie 在钥匙串里的 key（BUFF spec §2）。"""
    return f"{KEY_BUFF_COOKIE}:{account_id}"


class SecretStore:
    def __init__(self, env_path: Path, use_keyring: bool = True) -> None:
        self.env_path = env_path
        self.use_keyring = use_keyring

    # ---- keyring ----
    def _kr_get(self, key: str) -> str | None:
        try:
            return keyring.get_password(SERVICE, key)
        except Exception:
            return None

    def _kr_set(self, key: str, value: str) -> bool:
        try:
            keyring.set_password(SERVICE, key, value)
            return True
        except Exception:
            return False

    def _kr_delete(self, key: str) -> None:
        try:
            keyring.delete_password(SERVICE, key)
        except Exception:
            pass

    # ---- .env ----
    def _env_read(self) -> dict[str, str]:
        if not self.env_path.exists():
            return {}
        out: dict[str, str] = {}
        for line in self.env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v
        return out

    def _env_write(self, data: dict[str, str]) -> None:
        self.env_path.parent.mkdir(parents=True, exist_ok=True)
        self.env_path.write_text("".join(f"{k}={v}\n" for k, v in data.items()), encoding="utf-8")

    # ---- public ----
    def get(self, key: str) -> str | None:
        if self.use_keyring:
            v = self._kr_get(key)
            if v is not None:
                return v
        return self._env_read().get(key.upper())

    def set(self, key: str, value: str) -> None:
        if self.use_keyring and self._kr_set(key, value):
            # On successful keyring write, also purge any stale .env entry
            data = self._env_read()
            if key.upper() in data:
                del data[key.upper()]
                self._env_write(data)
            return
        data = self._env_read()
        data[key.upper()] = value
        self._env_write(data)

    def delete(self, key: str) -> None:
        if self.use_keyring:
            self._kr_delete(key)
        data = self._env_read()
        if key.upper() in data:
            del data[key.upper()]
            self._env_write(data)
