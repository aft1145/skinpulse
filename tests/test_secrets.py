from pathlib import Path

from skinpulse.secrets import KEY_YOUPIN_TOKEN, SecretStore


def test_env_fallback_roundtrip(tmp_path: Path):
    store = SecretStore(tmp_path / ".env", use_keyring=False)
    assert store.get(KEY_YOUPIN_TOKEN) is None
    store.set(KEY_YOUPIN_TOKEN, "abc=123")
    assert store.get(KEY_YOUPIN_TOKEN) == "abc=123"
    # 文件里是 KEY=VALUE 形式，键大写
    assert "YOUPIN_TOKEN=abc=123" in (tmp_path / ".env").read_text(encoding="utf-8")
    store.delete(KEY_YOUPIN_TOKEN)
    assert store.get(KEY_YOUPIN_TOKEN) is None


def test_env_preserves_other_lines(tmp_path: Path):
    p = tmp_path / ".env"
    p.write_text("OTHER=1\n", encoding="utf-8")
    store = SecretStore(p, use_keyring=False)
    store.set("x", "y")
    text = p.read_text(encoding="utf-8")
    assert "OTHER=1" in text and "X=y" in text


def test_keyring_failure_falls_back(tmp_path: Path, monkeypatch):
    import skinpulse.secrets as sec

    class Boom:
        def get_password(self, *a): raise RuntimeError("no backend")
        def set_password(self, *a): raise RuntimeError("no backend")
        def delete_password(self, *a): raise RuntimeError("no backend")

    monkeypatch.setattr(sec, "keyring", Boom())
    store = SecretStore(tmp_path / ".env", use_keyring=True)
    store.set("k", "v")
    assert store.get("k") == "v"


def test_stale_env_entry_after_keyring_set(tmp_path: Path, monkeypatch):
    """Regression test: stale .env entry should be purged after successful keyring write."""
    import skinpulse.secrets as sec

    # Step 1: keyring unavailable, set falls back to .env
    class FailKeyring:
        def get_password(self, *a): raise RuntimeError("no backend")
        def set_password(self, *a): raise RuntimeError("no backend")
        def delete_password(self, *a): raise RuntimeError("no backend")

    monkeypatch.setattr(sec, "keyring", FailKeyring())
    store = SecretStore(tmp_path / ".env", use_keyring=True)
    store.set(KEY_YOUPIN_TOKEN, "v1")
    assert store.get(KEY_YOUPIN_TOKEN) == "v1"

    # Step 2: keyring becomes available
    class WorkingKeyring:
        def __init__(self):
            self.data = {}
        def get_password(self, service, key):
            return self.data.get((service, key))
        def set_password(self, service, key, value):
            self.data[(service, key)] = value
        def delete_password(self, service, key):
            if (service, key) in self.data:
                del self.data[(service, key)]

    working_kr = WorkingKeyring()
    monkeypatch.setattr(sec, "keyring", working_kr)

    # Step 3: set with working keyring—should also purge .env entry
    store.set(KEY_YOUPIN_TOKEN, "v2")
    assert working_kr.get_password("skinpulse", KEY_YOUPIN_TOKEN) == "v2"

    # Step 4: verify .env entry was removed (regression check)
    env_content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "YOUPIN_TOKEN" not in env_content

    # Step 5: keyring becomes unavailable again
    monkeypatch.setattr(sec, "keyring", FailKeyring())

    # Step 6: get() should return None (stale v1 must not resurface)
    assert store.get(KEY_YOUPIN_TOKEN) is None


def test_youpin_token_key_keeps_the_old_key_for_account_one():
    from skinpulse.secrets import KEY_YOUPIN_TOKEN, youpin_token_key
    assert youpin_token_key(1) == KEY_YOUPIN_TOKEN == "youpin_token"
    assert youpin_token_key(2) == "youpin_token:2"


def test_env_fallback_round_trips_a_per_account_key(tmp_path):
    from skinpulse.secrets import SecretStore, youpin_token_key
    store = SecretStore(tmp_path / ".env", use_keyring=False)
    store.set(youpin_token_key(3), "abc=def")
    assert store.get(youpin_token_key(3)) == "abc=def"
    assert store.get(youpin_token_key(1)) is None
