import contextlib
import logging
from types import SimpleNamespace

import httpx
import pytest
import respx
from sqlalchemy import select
from typer.testing import CliRunner

from skinpulse.cli import app
from skinpulse.secrets import KEY_YOUPIN_TOKEN
from tests.conftest import APP, PC, device_w2_responder


def test_init_config_creates_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "settings.example.toml").write_text('data_dir = "data"\n', encoding="utf-8")
    r = CliRunner().invoke(app, ["init-config"])
    assert r.exit_code == 0 and (tmp_path / "settings.toml").exists()


def test_help_lists_commands():
    r = CliRunner().invoke(app, ["--help"])
    for cmd in ("run", "login", "set-token", "probe-token", "sync", "round", "dump", "init-config",
                "notify-test", "pushplus-token", "steamdt-key", "steamdt-items", "market-check",
                "brief", "price-snapshot", "valuation", "backtest", "demo", "supervise", "service", "pnl"):
        assert cmd in r.output


def test_old_ntfy_test_name_still_works():
    """推送早就不只 ntfy 一条通道了，命令改名叫 notify-test；旧名字保留，老文档里的命令照样能跑。"""
    r = CliRunner().invoke(app, ["ntfy-test", "--help"])
    assert r.exit_code == 0


# ---- also-fix: the SMS code prompt must hide input; the phone prompt must not ----


def test_login_sms_code_prompt_hides_input(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    settings_path = tmp_path / "settings.toml"
    settings_path.write_text(f'data_dir = "{data_dir.as_posix()}"\n', encoding="utf-8")

    # build_runtime constructs its own SecretStore (use_keyring=True default) with no
    # way to inject one, so the only interception point to keep this test off the
    # developer's real OS keychain is faking out the `keyring` module itself.
    import skinpulse.secrets as sec

    class _NoKeyring:
        def get_password(self, *a):
            raise RuntimeError("keyring disabled in tests")

        def set_password(self, *a):
            raise RuntimeError("keyring disabled in tests")

        def delete_password(self, *a):
            raise RuntimeError("keyring disabled in tests")

    monkeypatch.setattr(sec, "keyring", _NoKeyring())

    from skinpulse.adapters.youpin import auth as auth_mod

    monkeypatch.setattr(auth_mod, "send_sms_code", lambda client, phone: "sid-123")
    monkeypatch.setattr(auth_mod, "sms_sign_in", lambda client, phone, code, sid: "tok-abc")

    from skinpulse.services import account as account_mod

    monkeypatch.setattr(account_mod, "sync_my_orders", lambda session, client: 0)

    import skinpulse.cli as cli_mod

    calls: dict[str, bool] = {}

    def fake_prompt(text, hide_input=False, **kwargs):
        calls[text] = hide_input
        return "13800000000" if text == "手机号" else "123456"

    monkeypatch.setattr(cli_mod.typer, "prompt", fake_prompt)

    root = logging.getLogger()
    prev_handlers = list(root.handlers)
    prev_level = root.level
    try:
        r = CliRunner().invoke(app, ["login", "--settings", str(settings_path)])
    finally:
        for h in root.handlers:
            if h not in prev_handlers:
                h.close()
        root.handlers = prev_handlers
        root.setLevel(prev_level)

    assert r.exit_code == 0, r.output
    assert calls["手机号"] is False
    assert calls["短信验证码"] is True


# ---- final-review finding 5: the most diagnostic startup log line must reach the log file ----


def test_recover_pending_warning_reaches_log_file(tmp_path, monkeypatch):
    # 清扫的那行 "recovered N dangling pending notification(s)" 是卡住的通知被回收时最有
    # 诊断价值的一条日志，必须落进日志文件。它曾经因为跑在 setup_logging 之前而只到 stderr。
    # 清扫现在归 build_scheduler（见 runtime.py 里的注释），而 _rt 会先配好日志再返回，
    # 所以这里按真实的 run 路径走一遍：_rt -> build_scheduler。
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    settings_path = tmp_path / "settings.toml"
    settings_path.write_text(f'data_dir = "{data_dir.as_posix()}"\n', encoding="utf-8")

    from skinpulse.db.models import AlertStateRow, NotificationLog
    from skinpulse.db.session import make_engine, make_session_factory, upgrade_db, utcnow

    db = data_dir / "skinpulse.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    with Session() as s:
        s.add(AlertStateRow(purchase_no="P1", state="ALERTING", streak=1, last_rank=2, updated_at=utcnow()))
        s.add(NotificationLog(fingerprint="P1", event="ENTER_TOP2", rank=2, payload_json="{}",
                              created_at=utcnow(), status="pending"))
        s.commit()

    import skinpulse.secrets as sec

    class _NoKeyring:
        def get_password(self, *a):
            raise RuntimeError("keyring disabled in tests")

        def set_password(self, *a):
            raise RuntimeError("keyring disabled in tests")

        def delete_password(self, *a):
            raise RuntimeError("keyring disabled in tests")

    monkeypatch.setattr(sec, "keyring", _NoKeyring())

    import skinpulse.cli as cli_mod
    from skinpulse.scheduler import build_scheduler

    root = logging.getLogger()
    prev_handlers = list(root.handlers)
    prev_level = root.level
    try:
        rt = cli_mod._rt(settings_path)   # 配日志 + 构造 runtime（不清扫）
        build_scheduler(rt)               # run 命令紧接着做的事：清扫 + 排班
    finally:
        for h in root.handlers:
            if h not in prev_handlers:
                h.close()
        root.handlers = prev_handlers
        root.setLevel(prev_level)

    log_file = data_dir / "logs" / "skinpulse.jsonl"
    assert log_file.exists()
    log_text = log_file.read_text(encoding="utf-8")
    assert "recovered" in log_text
    assert "dangling pending notification" in log_text


def test_one_shot_command_does_not_disturb_an_in_flight_notification(tmp_path, monkeypatch):
    """后台服务跑着的时候，一条只读命令不许碰 notification_log / alert_state。

    实测过的事故（2026-09-21）：服务写下 12 行 pending 的同一秒，另一个进程跑了条只读命令，
    那 12 行立刻被标成 failed、告警状态被重置。那次服务自己重走一遍补上了，但时序稍微错开
    就会变成告警被静默吞掉，或者同一条推两遍。"""
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    settings_path = tmp_path / "settings.toml"
    settings_path.write_text(f'data_dir = "{data_dir.as_posix()}"\n', encoding="utf-8")

    from skinpulse.db.models import AlertStateRow, NotificationLog
    from skinpulse.db.session import make_engine, make_session_factory, upgrade_db, utcnow

    db = data_dir / "skinpulse.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    with Session() as s:
        s.add(AlertStateRow(purchase_no="P1", state="ALERTING", streak=1, last_rank=2, updated_at=utcnow()))
        s.add(NotificationLog(fingerprint="P1", event="ENTER_TOP2", rank=2, payload_json="{}",
                              created_at=utcnow(), status="pending"))
        s.commit()

    import skinpulse.secrets as sec

    class _NoKeyring:
        def get_password(self, *a):
            raise RuntimeError("keyring disabled in tests")

        def set_password(self, *a):
            raise RuntimeError("keyring disabled in tests")

        def delete_password(self, *a):
            raise RuntimeError("keyring disabled in tests")

    monkeypatch.setattr(sec, "keyring", _NoKeyring())

    root = logging.getLogger()
    prev_handlers = list(root.handlers)
    prev_level = root.level
    try:
        r = CliRunner().invoke(app, ["round", "--settings", str(settings_path)])
    finally:
        for h in root.handlers:
            if h not in prev_handlers:
                h.close()
        root.handlers = prev_handlers
        root.setLevel(prev_level)

    assert r.exit_code == 0, r.output
    with Session() as s:
        assert s.scalar(select(NotificationLog).where(NotificationLog.fingerprint == "P1")).status == "pending"
        assert s.get(AlertStateRow, "P1").state == "ALERTING"


# ---- set-token: validate-before-save workaround for login's slider CAPTCHA ----
#
# _rt() is monkeypatched directly (rather than routing through a real build_runtime())
# so these tests use a genuinely fake client and fake secret store, per the task brief:
# no real keyring, no real network, no real data/ directory ever gets touched.


class _FakeSecretStore:
    """Stand-in for SecretStore: just records what would have been saved."""

    def __init__(self) -> None:
        self.saved: dict[str, str] = {}

    def set(self, key: str, value: str) -> None:
        self.saved[key] = value


class _FakeYoupinClient:
    """Stand-in for YoupinClient: get_user_info() either returns a canned UserInfo or raises
    a canned error, and records whether/what `.token` was set to at validation time.

    `errors` is a queue of exceptions to raise on successive calls, in order (used to
    simulate "fails N times then succeeds" or "fails on every attempt"); once the queue is
    exhausted, later calls fall back to `error` (a single exception, raised every time - the
    original single-attempt shape) or otherwise return `user_info`."""

    def __init__(self, user_info=None, error: Exception | None = None,
                 errors: list[Exception] | None = None) -> None:
        self.token: str | None = None
        self._user_info = user_info
        self._error = error
        self._errors = list(errors) if errors is not None else None
        self.validate_called = False
        self.call_count = 0
        self.token_at_validation: str | None = None

    def get_user_info(self):
        self.validate_called = True
        self.call_count += 1
        self.token_at_validation = self.token
        if self._errors:
            raise self._errors.pop(0)
        if self._error is not None:
            raise self._error
        return self._user_info


def _fake_rt(client, secrets):
    """Minimal duck-typed Runtime: only the attributes set-token/login actually touch."""
    return SimpleNamespace(client=client, secrets=secrets,
                           session_factory=lambda: contextlib.nullcontext(object()))


def _user_info(user_id=99999999, nickname="小明"):
    from skinpulse.adapters.youpin.models import UserInfo

    return UserInfo(user_id, nickname)


def test_set_token_success_validates_then_saves_and_syncs(monkeypatch):
    import skinpulse.cli as cli_mod

    client = _FakeYoupinClient(user_info=_user_info())
    secrets = _FakeSecretStore()
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _fake_rt(client, secrets))
    monkeypatch.setattr(cli_mod.typer, "prompt", lambda text, hide_input=False, **kw: "  Bearer abc123  ")

    from skinpulse.services import account as account_mod

    monkeypatch.setattr(account_mod, "sync_my_orders", lambda session, c: 7)

    r = CliRunner().invoke(app, ["set-token"])

    assert r.exit_code == 0, r.output
    # validated (get_user_info called with the *normalized* token) before it was saved
    assert client.validate_called is True
    assert client.token_at_validation == "abc123"
    assert secrets.saved[KEY_YOUPIN_TOKEN] == "abc123"
    assert "小明" in r.output and "99999999" in r.output
    assert "7" in r.output


def test_set_token_login_required_saves_nothing_and_reports_clearly(monkeypatch):
    import skinpulse.cli as cli_mod
    from skinpulse.adapters.youpin.errors import LoginRequired

    client = _FakeYoupinClient(error=LoginRequired("84101", "登录信息已失效"))
    secrets = _FakeSecretStore()
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _fake_rt(client, secrets))
    monkeypatch.setattr(cli_mod.typer, "prompt", lambda text, hide_input=False, **kw: "sometoken")

    r = CliRunner().invoke(app, ["set-token"])

    assert r.exit_code != 0
    assert secrets.saved == {}
    assert client.validate_called is True
    # a clear, unambiguous message - not a bare traceback
    assert ("无效" in r.output) or ("过期" in r.output)


def test_set_token_other_youpin_error_reports_could_not_verify_and_saves_nothing(monkeypatch):
    """Any YoupinError other than LoginRequired (and other than a RateLimited that still has
    retries left) must be reported as "could not verify right now" - explicitly distinct
    from "your token is wrong" - naming the error code, with nothing saved and no raw
    traceback reaching the user. Non-RateLimited errors are not retried: exactly one call."""
    import skinpulse.cli as cli_mod
    from skinpulse.adapters.youpin.errors import RiskControl

    client = _FakeYoupinClient(error=RiskControl("84104", "风控"))
    secrets = _FakeSecretStore()
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _fake_rt(client, secrets))
    monkeypatch.setattr(cli_mod.typer, "prompt", lambda text, hide_input=False, **kw: "sometoken")

    r = CliRunner().invoke(app, ["set-token"])

    assert r.exit_code != 0
    assert secrets.saved == {}
    assert client.call_count == 1  # not retried - only RateLimited gets retried
    assert "84104" in r.output  # names the error code
    assert "无效" not in r.output and "过期" not in r.output  # not confused with a bad token
    assert ("无法验证" in r.output) or ("暂时" in r.output)


# ---- set-token: RateLimited (YouPin's empty-response-under-throttling case) gets retried
# a few times before being classified, instead of crashing or being mistaken for a bad token ----


def test_set_token_retries_rate_limited_then_saves_and_reports_success(monkeypatch):
    import skinpulse.cli as cli_mod
    from skinpulse.adapters.youpin.errors import RateLimited

    client = _FakeYoupinClient(
        user_info=_user_info(),
        errors=[RateLimited("200", "empty response"), RateLimited("200", "empty response")],
    )
    secrets = _FakeSecretStore()
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _fake_rt(client, secrets))
    monkeypatch.setattr(cli_mod.typer, "prompt", lambda text, hide_input=False, **kw: "sometoken")
    monkeypatch.setattr(cli_mod.time, "sleep", lambda s: None)  # no real sleeping in tests

    from skinpulse.services import account as account_mod

    monkeypatch.setattr(account_mod, "sync_my_orders", lambda session, c: 5)

    r = CliRunner().invoke(app, ["set-token"])

    assert r.exit_code == 0, r.output
    assert client.call_count == 3  # 2 failures + 1 success = 3 attempts total
    assert secrets.saved[KEY_YOUPIN_TOKEN] == "sometoken"
    assert "小明" in r.output and "99999999" in r.output
    assert "重试" in r.output  # told on-screen that it's retrying, and why


def test_set_token_rate_limited_every_attempt_reports_could_not_verify_cleanly(monkeypatch):
    import skinpulse.cli as cli_mod
    from skinpulse.adapters.youpin.errors import RateLimited

    client = _FakeYoupinClient(errors=[RateLimited("200", "empty response") for _ in range(5)])
    secrets = _FakeSecretStore()
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _fake_rt(client, secrets))
    monkeypatch.setattr(cli_mod.typer, "prompt", lambda text, hide_input=False, **kw: "sometoken")
    monkeypatch.setattr(cli_mod.time, "sleep", lambda s: None)

    r = CliRunner().invoke(app, ["set-token"])

    assert r.exit_code != 0
    assert secrets.saved == {}
    assert client.call_count == 3  # exactly _VALIDATE_ATTEMPTS, then gives up
    assert "无效" not in r.output and "过期" not in r.output  # not "your token is wrong"
    assert ("无法验证" in r.output) or ("暂时" in r.output)  # "could not verify" instead
    assert "Traceback" not in r.output
    assert r.exception is None or isinstance(r.exception, SystemExit)


# ---- set-token --from-file: read the token from a file instead of a hidden, unreviewable
# prompt - the token is a ~300+ character JWT and hide_input=True gives no way to tell
# whether a paste landed intact ----


def test_set_token_from_file_reads_normalizes_and_warns_to_delete(monkeypatch, tmp_path):
    import skinpulse.cli as cli_mod

    client = _FakeYoupinClient(user_info=_user_info())
    secrets = _FakeSecretStore()
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _fake_rt(client, secrets))

    token_file = tmp_path / "token.txt"
    token_file.write_text('  "Bearer abc123"  \n', encoding="utf-8")

    from skinpulse.services import account as account_mod

    monkeypatch.setattr(account_mod, "sync_my_orders", lambda session, c: 1)

    r = CliRunner().invoke(app, ["set-token", "--from-file", str(token_file)])

    assert r.exit_code == 0, r.output
    assert client.token_at_validation == "abc123"
    assert secrets.saved[KEY_YOUPIN_TOKEN] == "abc123"
    # warned once to delete the file - it still holds a live credential
    assert "删除" in r.output


def test_set_token_from_file_missing_file_rejected_and_saves_nothing(monkeypatch, tmp_path):
    import skinpulse.cli as cli_mod

    client = _FakeYoupinClient(user_info=_user_info())
    secrets = _FakeSecretStore()
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _fake_rt(client, secrets))

    missing = tmp_path / "does-not-exist.txt"

    r = CliRunner().invoke(app, ["set-token", "--from-file", str(missing)])

    assert r.exit_code != 0
    assert secrets.saved == {}
    assert client.validate_called is False


def test_set_token_from_file_empty_file_rejected_and_saves_nothing(monkeypatch, tmp_path):
    import skinpulse.cli as cli_mod

    client = _FakeYoupinClient(user_info=_user_info())
    secrets = _FakeSecretStore()
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _fake_rt(client, secrets))

    token_file = tmp_path / "token.txt"
    token_file.write_text("   \n", encoding="utf-8")  # blank after normalization

    r = CliRunner().invoke(app, ["set-token", "--from-file", str(token_file)])

    assert r.exit_code != 0
    assert secrets.saved == {}
    assert client.validate_called is False


def test_set_token_from_file_interior_space_rejected_and_saves_nothing(monkeypatch, tmp_path):
    """A token with an interior space (or newline) is a realistic paste artifact - e.g.
    copying a wrapped value out of devtools - and must be rejected with a clear message,
    not silently sent on to the server."""
    import skinpulse.cli as cli_mod

    client = _FakeYoupinClient(user_info=_user_info())
    secrets = _FakeSecretStore()
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _fake_rt(client, secrets))

    token_file = tmp_path / "token.txt"
    token_file.write_text("abc 123\n", encoding="utf-8")

    r = CliRunner().invoke(app, ["set-token", "--from-file", str(token_file)])

    assert r.exit_code != 0
    assert secrets.saved == {}
    assert client.validate_called is False
    assert ("空白" in r.output) or ("空格" in r.output)


def test_set_token_from_file_never_leaks_token_to_stdout_or_log_records(monkeypatch, tmp_path, caplog):
    import skinpulse.cli as cli_mod

    SECRET = "SECRET-TOKEN-from-file-must-not-leak-1a2b3c4d"
    token_file = tmp_path / "token.txt"
    token_file.write_text(SECRET + "\n", encoding="utf-8")

    client = _FakeYoupinClient(user_info=_user_info())
    secrets = _FakeSecretStore()
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _fake_rt(client, secrets))

    from skinpulse.services import account as account_mod

    monkeypatch.setattr(account_mod, "sync_my_orders", lambda session, c: 2)

    with caplog.at_level(logging.DEBUG):
        r = CliRunner().invoke(app, ["set-token", "--from-file", str(token_file)])

    assert r.exit_code == 0, r.output
    # sanity: it really was processed and saved, so a leak check here isn't vacuous
    assert secrets.saved[KEY_YOUPIN_TOKEN] == SECRET
    assert SECRET not in r.output
    for record in caplog.records:
        assert SECRET not in record.getMessage()
        assert SECRET not in str(record.args or "")


def test_set_token_rejects_empty_after_normalization_without_validating(monkeypatch):
    import skinpulse.cli as cli_mod

    client = _FakeYoupinClient(user_info=_user_info())
    secrets = _FakeSecretStore()
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _fake_rt(client, secrets))
    # strips to "" once whitespace and the quote pair are peeled off
    monkeypatch.setattr(cli_mod.typer, "prompt", lambda text, hide_input=False, **kw: '   ""  ')

    r = CliRunner().invoke(app, ["set-token"])

    assert r.exit_code != 0
    assert secrets.saved == {}
    assert client.validate_called is False


@pytest.mark.parametrize("raw", ["Bearer abc", "bearer abc", '"abc"', "  abc  "])
def test_set_token_normalizes_various_pastes_to_bare_token(monkeypatch, raw):
    import skinpulse.cli as cli_mod

    client = _FakeYoupinClient(user_info=_user_info())
    secrets = _FakeSecretStore()
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _fake_rt(client, secrets))
    monkeypatch.setattr(cli_mod.typer, "prompt", lambda text, hide_input=False, **kw: raw)

    from skinpulse.services import account as account_mod

    monkeypatch.setattr(account_mod, "sync_my_orders", lambda session, c: 0)

    r = CliRunner().invoke(app, ["set-token"])

    assert r.exit_code == 0, r.output
    assert secrets.saved[KEY_YOUPIN_TOKEN] == "abc"


def test_set_token_prompt_hides_input(monkeypatch):
    import skinpulse.cli as cli_mod

    client = _FakeYoupinClient(user_info=_user_info())
    secrets = _FakeSecretStore()
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _fake_rt(client, secrets))

    calls: dict[str, bool] = {}

    def fake_prompt(text, hide_input=False, **kwargs):
        calls[text] = hide_input
        return "abc123"

    monkeypatch.setattr(cli_mod.typer, "prompt", fake_prompt)

    from skinpulse.services import account as account_mod

    monkeypatch.setattr(account_mod, "sync_my_orders", lambda session, c: 0)

    r = CliRunner().invoke(app, ["set-token"])

    assert r.exit_code == 0, r.output
    assert calls["Token"] is True


def test_set_token_raw_value_never_reaches_stdout_or_log_records(monkeypatch, caplog):
    """The pasted token must never appear in captured stdout or in any log record - success
    path (echoes nickname/userId, not the token) and it must survive even if get_user_info
    itself logs something, since that's out of this command's control."""
    import skinpulse.cli as cli_mod

    SECRET = "SECRET-TOKEN-must-not-leak-9f8e7d6c5b4a3210"

    client = _FakeYoupinClient(user_info=_user_info())
    secrets = _FakeSecretStore()
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _fake_rt(client, secrets))
    monkeypatch.setattr(cli_mod.typer, "prompt", lambda text, hide_input=False, **kw: SECRET)

    from skinpulse.services import account as account_mod

    monkeypatch.setattr(account_mod, "sync_my_orders", lambda session, c: 3)

    with caplog.at_level(logging.DEBUG):
        r = CliRunner().invoke(app, ["set-token"])

    assert r.exit_code == 0, r.output
    # sanity: it really was processed and saved, so a leak here would be a real leak
    assert secrets.saved[KEY_YOUPIN_TOKEN] == SECRET
    assert SECRET not in r.output
    for record in caplog.records:
        assert SECRET not in record.getMessage()
        assert SECRET not in str(record.args or "")


def test_login_slider_captcha_hints_at_set_token_and_saves_nothing(monkeypatch):
    import skinpulse.cli as cli_mod
    from skinpulse.adapters.youpin.errors import SliderCaptchaRequired

    client = _FakeYoupinClient()
    secrets = _FakeSecretStore()
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _fake_rt(client, secrets))
    monkeypatch.setattr(cli_mod.typer, "prompt", lambda text, hide_input=False, **kw: "13800000000")

    from skinpulse.adapters.youpin import auth as auth_mod

    def _boom(client, phone):
        raise SliderCaptchaRequired("1110205", "")

    monkeypatch.setattr(auth_mod, "send_sms_code", _boom)

    r = CliRunner().invoke(app, ["login"])

    assert r.exit_code != 0
    assert "set-token" in r.output
    assert secrets.saved == {}


# ---- probe-token: read-only diagnostic against a few endpoints, never saves anything ----
#
# Unlike the set-token tests above (which use a hand-rolled _FakeYoupinClient double), these use
# a real YoupinClient with its HTTP transport mocked via respx - probe-token's whole point is to
# report the *actual shape* of a response, which a hand-rolled double can't stand in for.


def _reject_or(with_token_response: httpx.Response):
    """respx side_effect: returns the canned 84101 "not logged in" body when the request has no
    Authorization header, and `with_token_response` once it does - the same distinction a real
    server draws between "no token" and "some token, however it responds"."""
    def responder(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization"):
            return with_token_response
        return httpx.Response(200, json={"code": 84101, "msg": "登录信息已失效"})
    return responder


@respx.mock
def test_probe_token_prints_per_endpoint_and_saves_nothing(monkeypatch, youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    respx.get(f"{PC}/api/youpin/bff/user/Account/getUserInfoForApp").mock(
        side_effect=_reject_or(httpx.Response(
            200, content=b"not-json-because-encrypted",
            headers={"content-type": "text/plain", "sk": "some-signature"})))
    respx.get(f"{APP}/api/user/Account/getUserInfo").mock(
        side_effect=_reject_or(httpx.Response(200, json={"Code": 0, "Data": {"UserId": 1, "NickName": "x"}})))
    respx.post(f"{PC}/api/youpin/bff/trade/purchase/order/searchPurchaseOrderList").mock(
        side_effect=_reject_or(httpx.Response(200, json={"code": 0, "msg": "", "data": {"list": []}})))

    import skinpulse.cli as cli_mod

    secrets = _FakeSecretStore()
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _fake_rt(youpin_client, secrets))
    SECRET = "paste-me-not-9f8e7d6c"
    monkeypatch.setattr(cli_mod.typer, "prompt", lambda text, hide_input=False, **kw: f"  Bearer {SECRET}  ")

    r = CliRunner().invoke(app, ["probe-token"])

    assert r.exit_code == 0, r.output
    assert SECRET not in r.output
    assert secrets.saved == {}  # probe-token must never call secrets.set, on any outcome
    assert "getUserInfoForApp" in r.output
    # relabeled so a non-JSON/sk result on this one endpoint isn't mistaken for a probe
    # failure or a bad token - see client.PATH_USER_INFO's comment for the full story.
    assert "sk 加密通道" in r.output
    assert "getUserInfo" in r.output
    assert "searchPurchaseOrderList" in r.output
    assert "84101" in r.output  # the no-token baseline shows up
    assert "sk头=有" in r.output  # the mystery endpoint's sk header got flagged
    assert "结论" in r.output  # one-line verdict at the end


@respx.mock
def test_probe_token_prompt_hides_input(monkeypatch, youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    rejected = httpx.Response(200, json={"code": 84101, "msg": "登录信息已失效"})
    respx.get(f"{PC}/api/youpin/bff/user/Account/getUserInfoForApp").mock(return_value=rejected)
    respx.get(f"{APP}/api/user/Account/getUserInfo").mock(return_value=rejected)
    respx.post(f"{PC}/api/youpin/bff/trade/purchase/order/searchPurchaseOrderList").mock(
        return_value=rejected)

    import skinpulse.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _fake_rt(youpin_client, _FakeSecretStore()))

    calls: dict[str, bool] = {}

    def fake_prompt(text, hide_input=False, **kwargs):
        calls[text] = hide_input
        return "some-token"

    monkeypatch.setattr(cli_mod.typer, "prompt", fake_prompt)

    r = CliRunner().invoke(app, ["probe-token"])

    assert r.exit_code == 0, r.output
    assert calls["Token"] is True


def test_probe_token_rejects_empty_after_normalization_without_probing(monkeypatch, youpin_client):
    import skinpulse.cli as cli_mod

    secrets = _FakeSecretStore()
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _fake_rt(youpin_client, secrets))
    # strips to "" once whitespace and the quote pair are peeled off - same normalization as set-token
    monkeypatch.setattr(cli_mod.typer, "prompt", lambda text, hide_input=False, **kw: '   ""  ')

    r = CliRunner().invoke(app, ["probe-token"])

    assert r.exit_code != 0
    assert secrets.saved == {}


@respx.mock
def test_probe_token_never_leaks_token_to_stdout_or_log_records(monkeypatch, youpin_client, caplog):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    rejected = httpx.Response(200, json={"code": 84101, "msg": "登录信息已失效"})
    respx.get(f"{PC}/api/youpin/bff/user/Account/getUserInfoForApp").mock(return_value=rejected)
    respx.get(f"{APP}/api/user/Account/getUserInfo").mock(return_value=rejected)
    respx.post(f"{PC}/api/youpin/bff/trade/purchase/order/searchPurchaseOrderList").mock(
        return_value=rejected)

    import skinpulse.cli as cli_mod

    SECRET = "SECRET-TOKEN-must-not-leak-probe-9f8e7d6c5b4a"
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _fake_rt(youpin_client, _FakeSecretStore()))
    monkeypatch.setattr(cli_mod.typer, "prompt", lambda text, hide_input=False, **kw: SECRET)

    with caplog.at_level(logging.DEBUG):
        r = CliRunner().invoke(app, ["probe-token"])

    assert r.exit_code == 0, r.output
    assert SECRET not in r.output
    for record in caplog.records:
        assert SECRET not in record.getMessage()
        assert SECRET not in str(record.args or "")


@respx.mock
def test_probe_token_distinguishes_rejected_token_from_unexpected_shape(monkeypatch, youpin_client):
    """The whole point of the command: a token that's simply bad looks like a clean 84101 on
    every endpoint, with the same baseline; a token the server *did* accept but answered
    unexpectedly shows up as a non-84101/non-JSON result on at least one endpoint, while the
    no-token baseline stays identical (all 84101) in both cases."""
    import skinpulse.cli as cli_mod

    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    ui_route = respx.get(f"{PC}/api/youpin/bff/user/Account/getUserInfoForApp")
    legacy_route = respx.get(f"{APP}/api/user/Account/getUserInfo")
    search_route = respx.post(f"{PC}/api/youpin/bff/trade/purchase/order/searchPurchaseOrderList")

    rejected = httpx.Response(200, json={"code": 84101, "msg": "登录信息已失效"})
    ui_route.mock(return_value=rejected)
    legacy_route.mock(return_value=rejected)
    search_route.mock(return_value=rejected)

    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _fake_rt(youpin_client, _FakeSecretStore()))
    monkeypatch.setattr(cli_mod.typer, "prompt", lambda text, hide_input=False, **kw: "bad-token")

    r_bad = CliRunner().invoke(app, ["probe-token"])
    assert r_bad.exit_code == 0, r_bad.output
    assert "bad-token" not in r_bad.output
    assert "三个接口不带 token 时全部按预期返回 84101/84103" in r_bad.output
    assert "3 个明确按未登录拒绝" in r_bad.output
    assert "0 个响应形状异常、无法判断" in r_bad.output

    # Same baseline shape (still 84101 with no token - these routes ignore headers entirely),
    # but this time the token itself gets a genuinely different answer on one endpoint: not
    # 84101/84103, and not JSON at all - the "token probably works, response just looks odd" case.
    def ui_responder(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization"):
            return httpx.Response(200, content=b"\x01\x02encrypted-ish",
                                  headers={"content-type": "application/octet-stream", "sk": "sig"})
        return rejected
    ui_route.mock(side_effect=ui_responder)
    monkeypatch.setattr(cli_mod.typer, "prompt", lambda text, hide_input=False, **kw: "maybe-good-token")

    r_good = CliRunner().invoke(app, ["probe-token"])
    assert r_good.exit_code == 0, r_good.output
    assert "maybe-good-token" not in r_good.output
    assert "三个接口不带 token 时全部按预期返回 84101/84103" in r_good.output  # baseline unchanged
    assert "2 个明确按未登录拒绝" in r_good.output  # legacy + search still reject
    assert "1 个响应形状异常、无法判断" in r_good.output  # getUserInfoForApp no longer a clean reject


# ---- _normalize_token 单元测试：devtools 里连名字一起复制走 ----

_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJJZCI6MTF9.c2ln"


@pytest.mark.parametrize("raw", [
    _JWT,
    "  " + _JWT + "  ",
    '"' + _JWT + '"',
    "Bearer " + _JWT,
    "bearer " + _JWT,
    "uu_token=" + _JWT,              # Application -> Cookies 那栏复制出来的样子
    "uu_token = " + _JWT,
    "uu_token=Bearer " + _JWT,
    '"uu_token=' + _JWT + '"',
    "authorization: " + _JWT,        # Headers 那栏复制出来的样子
    "authorization:" + _JWT,
    "authorization: Bearer " + _JWT,
    "LOGIN_TOKEN=" + _JWT,
])
def test_normalize_token_strips_whatever_came_along_with_the_paste(raw):
    from skinpulse.cli import _normalize_token

    assert _normalize_token(raw) == _JWT


@pytest.mark.parametrize("raw", [
    "eyJhbGciOiJIUzI1NiJ9==.eyJJZCI6MTF9.c2ln",  # 段尾带 base64 填充的 token，不能被从中间劈开
    "foo=barbaz",                                 # 不是 JWT，名字前缀规则不该生效
    "随便什么东西",
])
def test_normalize_token_leaves_anything_that_is_not_a_name_prefix_alone(raw):
    """名字前缀只在 `=`/`:` 后面紧跟着 `eyJ` 时才切。少了这个前瞻，一个段尾带 `=` 填充的
    token 会被当成"名字=值"从中间切断，静悄悄地把有效凭据毁掉。"""
    from skinpulse.cli import _normalize_token

    assert _normalize_token(raw) == raw



# ---- pushplus-token ----


def _pp_rt(secrets):
    from skinpulse.config import Settings

    return SimpleNamespace(secrets=secrets, settings=Settings())


def _run_pushplus(monkeypatch, typed, publish):
    """跑一次 pushplus-token：`typed` 是用户在隐藏输入框里「输进去」的内容，
    `publish` 替换 PushPlusClient.publish。返回 (CliRunner 结果, 存下来的东西, 发出去的请求数)。"""
    import skinpulse.adapters.pushplus.client as pp_mod
    import skinpulse.cli as cli_mod

    secrets = _FakeSecretStore()
    calls = []
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _pp_rt(secrets))
    monkeypatch.setattr(cli_mod.typer, "prompt", lambda text, hide_input=False, **kw: typed)

    def fake_publish(self, msg):
        calls.append(self.token)
        return publish(self, msg)

    monkeypatch.setattr(pp_mod.PushPlusClient, "publish", fake_publish)
    r = CliRunner().invoke(app, ["pushplus-token"])
    return r, secrets.saved, calls


def test_pushplus_token_rejects_a_control_character_without_sending_anything(monkeypatch):
    """Windows 上在不回显的输入框里按 Ctrl+V，有时进来的是控制字符 SYN（即 Ctrl+V 本身）
    而不是剪贴板内容。发出去只会拿到 903，而 903 的字面意思会把人引去官网反复核对一个
    其实没问题的 token——2026-09-23 真实发生过。"""
    r, saved, calls = _run_pushplus(monkeypatch, "abc" + chr(22) + "def", lambda self, msg: "sc")

    assert r.exit_code == 1
    assert "控制字符" in r.output and "--from-file" in r.output
    assert saved == {} and calls == []


def test_pushplus_token_903_reports_length_but_never_the_token(monkeypatch):
    from skinpulse.adapters.pushplus.client import PushPlusAuthError

    def reject(self, msg):
        raise PushPlusAuthError("903", "用户令牌不正确", "无效的用户token")

    token = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
    r, saved, _ = _run_pushplus(monkeypatch, token, reject)

    assert r.exit_code == 1 and saved == {}
    assert "32 位" in r.output
    assert token not in r.output


def test_pushplus_token_saves_only_after_a_successful_send(monkeypatch):
    from skinpulse.secrets import KEY_PUSHPLUS

    r, saved, calls = _run_pushplus(monkeypatch, "  tok-ok  ", lambda self, msg: "short-code-1")

    assert r.exit_code == 0, r.output
    assert saved == {KEY_PUSHPLUS: "tok-ok"}      # 首尾空白去掉了
    assert calls == ["tok-ok"]
    assert "short-code-1" in r.output
    assert "亲眼" in r.output                      # 提醒：返回成功不等于送到了微信



def test_token_file_with_a_bom_reads_clean(tmp_path):
    """PowerShell 5.1 的 Out-File -Encoding utf8 会在文件开头写一个 BOM。它不可见也不算空白，
    不去掉就会拼进 token——然后被当成「控制字符」拒掉，或者发出去拿一个 903。"""
    from skinpulse.cli import _read_token_from_file

    f = tmp_path / "pushplus_token.txt"
    f.write_bytes(bytes([0xEF, 0xBB, 0xBF]) + b"abc123")
    assert _read_token_from_file(f) == "abc123"


def test_brief_command_previews_without_sending(tmp_path, settings, monkeypatch):
    from skinpulse import cli
    from skinpulse.db.models import BriefLog
    from skinpulse.db.session import make_engine, make_session_factory, upgrade_db

    db = tmp_path / "b.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    rt = SimpleNamespace(settings=settings, session_factory=Session, ntfy=None)
    monkeypatch.setattr(cli, "_rt", lambda _settings: rt)
    r = CliRunner().invoke(app, ["brief", "--date", "2026-09-22"])
    assert r.exit_code == 0, r.output
    assert "no_data" in r.output and "预览" in r.output
    with Session() as s:
        assert s.query(BriefLog).count() == 0
    assert CliRunner().invoke(app, ["brief", "--date", "09-22"]).exit_code != 0


def test_brief_send_on_existing_record_does_not_fake_a_resend(tmp_path, settings, monkeypatch):
    """`run_daily_brief` 在没有 --force 时遇到当天已有记录会原样把它返回、什么都不做；CLI 不该
    照常打印 decision/送达提示，那会看起来像是这次真的重新判定、重新推送了一遍。"""
    from datetime import datetime, timezone

    from skinpulse import cli
    from skinpulse.db.models import BriefLog
    from skinpulse.db.session import make_engine, make_session_factory, upgrade_db

    db = tmp_path / "b_existing.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    with Session() as s:
        s.add(BriefLog(date="2026-09-22", decision="sent", reasons_json="{}", title="t", body="b",
                       created_at=datetime(2026, 9, 22, 16, 0, 5, tzinfo=timezone.utc), sent_at=None))
        s.commit()

    class FakeNtfy:
        def __init__(self):
            self.calls = 0

        def publish(self, msg):
            self.calls += 1
            return []

    ntfy = FakeNtfy()
    rt = SimpleNamespace(settings=settings, session_factory=Session, ntfy=ntfy)
    monkeypatch.setattr(cli, "_rt", lambda _settings: rt)
    r = CliRunner().invoke(app, ["brief", "--date", "2026-09-22", "--send"])
    assert r.exit_code == 0, r.output
    assert "已有记录" in r.output and "未重发" in r.output and "--force" in r.output
    assert ntfy.calls == 0


def test_brief_force_without_send_only_notes_it_has_no_effect(tmp_path, settings, monkeypatch):
    from skinpulse import cli
    from skinpulse.db.session import make_engine, make_session_factory, upgrade_db

    db = tmp_path / "b_force_preview.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    rt = SimpleNamespace(settings=settings, session_factory=Session, ntfy=None)
    monkeypatch.setattr(cli, "_rt", lambda _settings: rt)
    r = CliRunner().invoke(app, ["brief", "--date", "2026-09-22", "--force"])
    assert r.exit_code == 0, r.output
    assert "--force 只在配合 --send 时才有效" in r.output
    assert "预览" in r.output


def test_price_snapshot_has_no_date_option_and_always_snapshots_today(tmp_path, settings, monkeypatch):
    """price/batch 只给当前价：--date 2026-09-20 会把今天的价格记成 09-20 的历史，所以不提供这个选项。"""
    from datetime import date

    from skinpulse import cli
    from skinpulse.services import price_snapshot as ps

    seen = {}
    monkeypatch.setattr(cli, "_rt", lambda _settings: SimpleNamespace(settings=settings,
                                                                     session_factory=lambda: _NullSession()))
    monkeypatch.setattr(cli, "_steamdt_client", lambda rt: object())
    monkeypatch.setattr(ps, "take_price_snapshot", lambda s, c, *, day, **kw: seen.setdefault("day", day) or "ok")
    monkeypatch.setattr("skinpulse.core.localday.local_today", lambda tz, now=None: date(2026, 9, 23))
    r = CliRunner().invoke(app, ["price-snapshot"])
    assert r.exit_code == 0, r.output
    assert seen["day"] == date(2026, 9, 23)
    r = CliRunner().invoke(app, ["price-snapshot", "--date", "2026-09-20"])
    assert r.exit_code != 0 and "--date" in r.output
    assert "--date" not in CliRunner().invoke(app, ["price-snapshot", "--help"]).output


class _NullSession:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_brief_preview_prints_the_plain_text_table(tmp_path, settings, monkeypatch):
    """预览打印纯文本全文（ntfy 与 brief_log.body 用的那份），不打印给 PushPlus 的 html。"""
    from datetime import datetime, timezone
    from decimal import Decimal

    from skinpulse import cli
    from skinpulse.db.models import MyBuyOrder, RankSnapshot
    from skinpulse.db.session import make_engine, make_session_factory, upgrade_db

    db = tmp_path / "b_table.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    at = datetime(2026, 9, 22, 1, 0, tzinfo=timezone.utc)
    with Session() as s:
        s.add(MyBuyOrder(purchase_no="A", template_id=1, commodity_name="AK-47 | 红线 (久经沙场)",
                         price=Decimal("48"), qty=1, remaining_qty=1, type_id=-1, queue_key="1|ANY|-1|ANY|ANY",
                         synced_at=at, active=True))
        s.add(RankSnapshot(purchase_no="A", ts=at, rank=1, beyond=None, top1_price=Decimal("48"),
                           top2_price=None, top1_qty=None, sample_size=0))
        s.commit()
    rt = SimpleNamespace(settings=settings, session_factory=Session, ntfy=None)
    monkeypatch.setattr(cli, "_rt", lambda _settings: rt)
    r = CliRunner().invoke(app, ["brief", "--date", "2026-09-22"])
    assert r.exit_code == 0, r.output
    assert "标题：SkinPulse 简报 09-22：前 2 共 1 张，1 张有变动" in r.output
    assert "第1名 | AK-47 | 红线 (久经) | 求购 48 | 榜首 我 | 在售 — | 🆕新进" in r.output
    assert "<table" not in r.output


def test_console_that_cannot_encode_a_character_does_not_crash_the_cli():
    # 中文 Windows 控制台是 GBK：饰品名里的「™」（StatTrak™）编不进去，
    # 不处理的话 `brief` 预览会在 typer.echo 里抛 UnicodeEncodeError。
    import io

    from skinpulse.cli import tolerate_console_encoding

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="gbk", newline="\n")
    tolerate_console_encoding(stream)
    stream.write("沙漠之鹰（StatTrak™）\n")
    stream.flush()
    assert raw.getvalue().decode("gbk") == "沙漠之鹰（StatTrak?）\n"
    tolerate_console_encoding(object())  # 不是 TextIOWrapper（比如被测试框架替换）就什么都不做


# ---- run：单实例锁、--console-level、SIGTERM（spec §11 / §11.1）----


def _run_settings(tmp_path, port: int = 8766):
    data_dir = tmp_path / "data"
    p = tmp_path / "settings.toml"
    p.write_text(f'data_dir = "{data_dir.as_posix()}"\n\n[web]\nport = {port}\n', encoding="utf-8")
    return p, data_dir / "skinpulse.run.lock"


class _FakeScheduler:
    def __init__(self):
        self.started = False
        self.shutdowns = 0

    def start(self):
        self.started = True

    def shutdown(self, wait=True):
        self.shutdowns += 1


@pytest.fixture
def fake_service(monkeypatch):
    """把 run 的整条链路换成替身：不建库、不联网、不起 uvicorn。"""
    import uvicorn

    import skinpulse.runtime as runtime_mod
    import skinpulse.scheduler as scheduler_mod
    import skinpulse.web.app as web_app_mod
    from skinpulse import cli
    from skinpulse.config import load_settings

    state = SimpleNamespace(console_levels=[], sched=_FakeScheduler(), during_serve=None)

    def fake_rt(settings, *, console_level="INFO"):
        state.console_levels.append(console_level)
        return SimpleNamespace(settings=load_settings(settings), ntfy=None)

    monkeypatch.setattr(cli, "_rt", fake_rt)
    monkeypatch.setattr(runtime_mod, "warn_about_channels", lambda ntfy: None)
    monkeypatch.setattr(scheduler_mod, "build_scheduler", lambda rt: state.sched)
    monkeypatch.setattr(web_app_mod, "create_app", lambda rt, sched: object())

    def fake_uvicorn_run(app_obj, **kw):
        if state.during_serve:
            state.during_serve()

    monkeypatch.setattr(uvicorn, "run", fake_uvicorn_run)
    return state


def test_run_refuses_to_start_while_another_instance_holds_the_lock(tmp_path, monkeypatch):
    import os

    from skinpulse import cli
    from skinpulse.runlock import acquire_run_lock

    monkeypatch.chdir(tmp_path)
    settings_path, lock_path = _run_settings(tmp_path)

    def must_not_run(*a, **kw):
        raise AssertionError("锁冲突时不能再往下走：不能建库、不能配日志、更不能联网")

    monkeypatch.setattr(cli, "_rt", must_not_run)
    with acquire_run_lock(lock_path):
        r = CliRunner().invoke(app, ["run", "--settings", str(settings_path)])
    assert r.exit_code == 1, r.output
    assert str(os.getpid()) in r.output
    assert "已有" in r.output
    assert not (tmp_path / "data" / "skinpulse.db").exists()
    assert not (tmp_path / "data" / "logs").exists()


def test_run_holds_the_lock_while_serving_and_releases_it_after(tmp_path, monkeypatch, fake_service):
    from skinpulse.runlock import RunLockHeld, acquire_run_lock

    monkeypatch.chdir(tmp_path)
    settings_path, lock_path = _run_settings(tmp_path)
    seen = {}

    def while_serving():
        with pytest.raises(RunLockHeld):
            acquire_run_lock(lock_path)
        seen["checked"] = True

    fake_service.during_serve = while_serving
    r = CliRunner().invoke(app, ["run", "--settings", str(settings_path)])
    assert r.exit_code == 0, r.output
    assert seen == {"checked": True}
    assert fake_service.sched.started and fake_service.sched.shutdowns == 1
    with acquire_run_lock(lock_path):  # run 退出后锁已释放
        pass


def test_run_console_level_is_passed_through(tmp_path, monkeypatch, fake_service):
    monkeypatch.chdir(tmp_path)
    settings_path, _ = _run_settings(tmp_path)
    assert CliRunner().invoke(app, ["run", "--settings", str(settings_path)]).exit_code == 0
    r = CliRunner().invoke(app, ["run", "--settings", str(settings_path), "--console-level", "warning"])
    assert r.exit_code == 0, r.output
    assert fake_service.console_levels == ["INFO", "WARNING"]
    assert CliRunner().invoke(app, ["run", "--settings", str(settings_path), "--console-level", "loud"]).exit_code == 2


def test_run_web_only_serves_the_dashboard_without_a_scheduler(tmp_path, monkeypatch, fake_service):
    """--web-only 只开看板：不建调度器，也就不跑监控、不发简报、不收尾在途告警。
    用在「另一台机器上看一眼看板」——两台都跑监控会各推一遍。"""
    import skinpulse.scheduler as scheduler_mod
    import skinpulse.web.app as web_app_mod
    from skinpulse.runlock import RunLockHeld, acquire_run_lock

    monkeypatch.chdir(tmp_path)
    settings_path, lock_path = _run_settings(tmp_path)

    def must_not_build(rt):
        raise AssertionError("--web-only 不能建调度器")

    monkeypatch.setattr(scheduler_mod, "build_scheduler", must_not_build)
    got = {}
    monkeypatch.setattr(web_app_mod, "create_app", lambda rt, sched: got.setdefault("sched", sched) or object())

    def while_serving():
        with pytest.raises(RunLockHeld):  # 照样占着单实例锁：同一个 data_dir 不能再起一个 run
            acquire_run_lock(lock_path)

    fake_service.during_serve = while_serving
    r = CliRunner().invoke(app, ["run", "--settings", str(settings_path), "--web-only"])
    assert r.exit_code == 0, r.output
    assert got == {"sched": None}
    assert not fake_service.sched.started and fake_service.sched.shutdowns == 0


def test_run_web_only_and_no_web_are_mutually_exclusive(tmp_path, monkeypatch, fake_service):
    monkeypatch.chdir(tmp_path)
    settings_path, _ = _run_settings(tmp_path)
    r = CliRunner().invoke(app, ["run", "--settings", str(settings_path), "--web-only", "--no-web"])
    assert r.exit_code == 2, r.output
    assert fake_service.console_levels == []


def test_one_shot_commands_ignore_the_run_lock(tmp_path, settings, monkeypatch):
    """只有 run 加锁：服务在跑时，brief 预览、sync、round 这些一次性命令照样能跑。"""
    from skinpulse import cli
    from skinpulse.db.session import make_engine, make_session_factory, upgrade_db
    from skinpulse.runlock import acquire_run_lock

    db = tmp_path / "b.db"
    upgrade_db(db)
    rt = SimpleNamespace(settings=settings, session_factory=make_session_factory(make_engine(db)), ntfy=None)
    monkeypatch.setattr(cli, "_rt", lambda _settings: rt)
    with acquire_run_lock(settings.data_path / "skinpulse.run.lock"):
        r = CliRunner().invoke(app, ["brief", "--date", "2026-09-22"])
    assert r.exit_code == 0, r.output


_SIGTERM_SCRIPT = r'''
import os, signal, socket, sys, threading, time
from types import SimpleNamespace

import fastapi
import skinpulse.runtime as runtime_mod
import skinpulse.scheduler as scheduler_mod
import skinpulse.web.app as web_app_mod
from skinpulse import cli
from skinpulse.config import load_settings

settings_path, port, no_web = sys.argv[1], int(sys.argv[2]), sys.argv[3] == "1"
started = threading.Event()

class Sched:
    def start(self):
        # 真调度器的一轮监控跑在线程池的非守护线程里，一轮要几分钟；解释器正常退出会等它跑完
        threading.Thread(target=time.sleep, args=(30,)).start()
        started.set()
    def shutdown(self, wait=True):
        print("SCHED-SHUTDOWN", flush=True)

cli._rt = lambda settings, console_level="INFO": SimpleNamespace(settings=load_settings(settings), ntfy=None)
runtime_mod.warn_about_channels = lambda ntfy: None
scheduler_mod.build_scheduler = lambda rt: Sched()
web_app_mod.create_app = lambda rt, sched: fastapi.FastAPI()

def kick():
    started.wait(10)
    if not no_web:  # 等 uvicorn 真的在听端口，SIGTERM 才会走 uvicorn 自己的信号处理
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)
    time.sleep(0.2)
    os.kill(os.getpid(), signal.SIGTERM)

threading.Thread(target=kick, daemon=True).start()
args = ["run", "--settings", settings_path] + (["--no-web"] if no_web else [])
cli.app(args)
'''


@pytest.mark.skipif(__import__("sys").platform == "win32", reason="POSIX SIGTERM")
@pytest.mark.parametrize("no_web", [False, True], ids=["uvicorn", "no-web"])
def test_sigterm_still_shuts_the_scheduler_down(tmp_path, no_web):
    """launchctl stop / bootout 发的是 SIGTERM。uvicorn 0.52 收到后自己先优雅退出，再把
    原来的 handler 装回去、把信号重发一遍——默认 handler 直接杀进程，run 的 finally 就跑不到。
    --no-web 分支更直接：sleep 里吃到 SIGTERM 就死。用真的 uvicorn、在子进程里跑。"""
    import socket
    import subprocess
    import sys

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    settings_path, _ = _run_settings(tmp_path, port=port)
    import time

    t0 = time.monotonic()
    proc = subprocess.run([sys.executable, "-c", _SIGTERM_SCRIPT, str(settings_path), str(port), "1" if no_web else "0"],
                          cwd=tmp_path, capture_output=True, text=True, timeout=60)
    elapsed = time.monotonic() - t0
    assert "SCHED-SHUTDOWN" in proc.stdout, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.returncode == 143, (proc.returncode, proc.stderr)
    # 不等在跑的那一轮：launchd 默认 20 秒后就 SIGKILL，等也等不完
    assert elapsed < 15, elapsed


def test_valuation_command_defaults_to_yesterday_and_supports_dry_run(settings, monkeypatch):
    from datetime import date, timedelta

    from skinpulse import cli
    from skinpulse.core.localday import local_today
    import skinpulse.services.valuation as val

    seen = []
    rt = SimpleNamespace(settings=settings, session_factory=lambda: _NullSession(), market="M")
    monkeypatch.setattr(cli, "_rt", lambda _settings: rt)
    monkeypatch.setattr(cli, "_steamdt_client", lambda rt: "DT")
    monkeypatch.setattr(val, "run_valuation",
                        lambda s, client, market, st, *, day, now, dry_run=False, **kw:
                        seen.append((client, market, day, dry_run)) or val.ValuationReport(day=day.isoformat()))
    r = CliRunner().invoke(app, ["valuation"])
    assert r.exit_code == 0, r.output
    assert seen[-1] == ("DT", "M", local_today(settings.timezone) - timedelta(days=1), False)
    r = CliRunner().invoke(app, ["valuation", "--date", "2026-09-20", "--dry-run"])
    assert r.exit_code == 0 and seen[-1] == ("DT", "M", date(2026, 9, 20), True)
    assert "没有写库" in r.output
    assert CliRunner().invoke(app, ["valuation", "--date", "09-20"]).exit_code != 0


class _NullSession:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fills_backfill_resolves_without_pushing(settings, tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal

    from sqlalchemy import select

    from skinpulse import cli
    from skinpulse.adapters.youpin.models import MyPurchaseOrder
    from skinpulse.db.models import MyBuyOrder, NotificationLog, OrderEvent
    from skinpulse.db.session import make_engine, make_session_factory, upgrade_db

    db = tmp_path / "b.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    old = datetime.now(timezone.utc) - timedelta(days=3)          # 早就超出 resolve_window_h
    with Session() as s:
        for no, name in (("A", "SSG 08 | 渐变强酸 (崭新出厂)"), ("B", "AWP | 野火 (久经沙场)")):
            s.add(MyBuyOrder(purchase_no=no, template_id=1, commodity_name=name, price=Decimal("5"),
                             queue_key="1|ANY|-1|ANY|ANY", synced_at=old, active=False))
            s.add(OrderEvent(purchase_no=no, template_id=1, kind="gone", qty_before=1, price=Decimal("5"), at=old))
        s.commit()

    def row(no, status, created):
        raw = {"status": status, "quantity": 1, "buyQuantity": 1 if status == 40 else 0, "createTime": created}
        return MyPurchaseOrder(no, 1, "", "", Decimal("5"), 1, 0, None, -1, None, None, raw)

    class Client:
        s = SimpleNamespace(page_sleep_s=0)
        calls = []

        def sleep(self, _):
            pass

        def search_my_purchase_orders_page(self, status, page, size=40):
            self.calls.append((status, page))
            return {40: [row("A", 40, "2026.10.02 09:55:14")], 30: [row("B", 30, "2026.10.01 18:29:50")]}[status] \
                if page == 1 else []

    rt = SimpleNamespace(settings=settings, session_factory=Session, client=Client())
    monkeypatch.setattr(cli, "_rt", lambda _settings: rt)
    r = CliRunner().invoke(app, ["fills", "backfill"])
    assert r.exit_code == 0, r.output
    assert "SSG 08 | 渐变强酸 (崭新出厂)" in r.output and "成交" in r.output and "10-02 09:55" in r.output
    assert "暂停" in r.output
    with Session() as s:
        out = {e.purchase_no: (e.outcome, e.notice) for e in s.scalars(select(OrderEvent))}
        assert out == {"A": ("done", None), "B": ("paused", None)}        # 回填绝不推送
        assert s.scalars(select(NotificationLog)).all() == []


def test_help_lists_fills_command():
    assert "fills" in CliRunner().invoke(app, ["--help"]).output
    assert "backfill" in CliRunner().invoke(app, ["fills", "--help"]).output


# ---- dump trades / order / lease-out / inventory：盈亏统计调研用的只读查询（research 2026-11-24） ----


def _dump_rt(client):
    from skinpulse.config import Settings

    return SimpleNamespace(client=client, settings=Settings())


@respx.mock
def test_dump_trades_posts_read_only_query_with_token(monkeypatch, youpin_client):
    import json

    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    route = respx.post(f"{PC}/api/youpin/bff/trade/sale/v1/sell/list").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"orderList": [], "total": None}}))
    import skinpulse.cli as cli_mod

    youpin_client.token = "tok"
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _dump_rt(youpin_client))
    r = CliRunner().invoke(app, ["dump", "trades", "sell", "--page", "2"])

    assert r.exit_code == 0, r.output
    sent = json.loads(route.calls.last.request.content)
    assert sent["orderStatus"] == 340 and sent["pageIndex"] == 2 and sent["pageSize"] == 20
    assert route.calls.last.request.headers["authorization"] == "Bearer tok"
    assert json.loads(r.output) == {"orderList": [], "total": None}


@respx.mock
def test_dump_inventory_and_lease_out_hit_their_paths(monkeypatch, youpin_client):
    respx.post(f"{APP}/api/deviceW2").mock(side_effect=device_w2_responder())
    inv = respx.post(f"{PC}/api/youpin/commodity/user/inventory/price/trend").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"itemsInfos": []}}))
    lease = respx.post(f"{PC}/api/youpin/bff/trade/v1/order/lease/out/list").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"orderDataList": []}}))
    import skinpulse.cli as cli_mod

    youpin_client.token = "tok"
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: _dump_rt(youpin_client))
    assert CliRunner().invoke(app, ["dump", "inventory"]).exit_code == 0
    assert CliRunner().invoke(app, ["dump", "lease-out"]).exit_code == 0
    assert inv.called and lease.called


def test_dump_help_lists_pnl_probes():
    r = CliRunner().invoke(app, ["dump", "--help"])
    for cmd in ("trades", "order", "lease-out", "on-sale", "inventory"):
        assert cmd in r.output


def test_set_token_for_a_second_account_saves_under_its_own_key(monkeypatch, tmp_path):
    """多账号 spec §9：`set-token --account 2` 存进 youpin_token:2，建号 2 的行，同步用号 2 的身份。"""
    import skinpulse.cli as cli_mod
    from skinpulse.db.models import Account
    from skinpulse.db.session import make_engine, make_session_factory, upgrade_db
    from skinpulse.services import account as account_mod
    from skinpulse.services.account import AccountRuntime

    db = tmp_path / "c.db"
    upgrade_db(db)
    Session = make_session_factory(make_engine(db))
    with Session() as s:
        s.add(Account(id=1, user_id=1, nickname="主"))
        s.commit()
    second = _FakeYoupinClient(user_info=_user_info(user_id=2, nickname="小"))
    second.device_id = "dev-2"
    secrets = _FakeSecretStore()
    rt = SimpleNamespace(client=object(), secrets=secrets, session_factory=Session,
                         accounts={1: AccountRuntime(1, object()), 2: AccountRuntime(2, second)})
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: rt)
    monkeypatch.setattr(cli_mod.typer, "prompt", lambda text, hide_input=False, **kw: "abc123")
    synced = []
    monkeypatch.setattr(account_mod, "sync_my_orders", lambda session, c: synced.append(c) or 3)
    r = CliRunner().invoke(app, ["set-token", "--account", "2"])
    assert r.exit_code == 0, r.output
    assert secrets.saved == {"youpin_token:2": "abc123"}
    assert synced[0].id == 2 and synced[0].client is second
    with Session() as s:
        assert s.get(Account, 2).device_id == "dev-2"
    # 别的号的 token 不能存到这个号上
    monkeypatch.setattr(cli_mod.typer, "prompt", lambda text, hide_input=False, **kw: "zzz")
    second._user_info = _user_info(user_id=1)
    r = CliRunner().invoke(app, ["set-token", "--account", "2"])
    assert r.exit_code == 1 and "另一个号" in r.output



# ---- pnl import / run / show（盈亏 spec §5.3）----


def test_pnl_show_empty_and_with_data(tmp_path, settings, monkeypatch):
    import skinpulse.cli as cli_mod
    from tests.test_web_pnl import _rows
    from tests.test_web_overview import _rt as web_rt

    rt, _ = web_rt(tmp_path, settings, [])
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: rt)
    r = CliRunner().invoke(app, ["pnl", "show"])
    assert r.exit_code == 0 and "pnl import" in r.output
    (tmp_path / "b").mkdir()
    rt2, _ = web_rt(tmp_path / "b", settings, _rows())
    monkeypatch.setattr(cli_mod, "_rt", lambda settings: rt2)
    r = CliRunner().invoke(app, ["pnl", "show"])
    assert r.exit_code == 0, r.output
    assert "总盈亏 +¥528.50" in r.output and "不在库存 1" in r.output and "−¥30.00" in r.output
    r = CliRunner().invoke(app, ["pnl", "show", "--basis", "net"])
    assert "到手价" in r.output
    assert CliRunner().invoke(app, ["pnl", "show", "--basis", "x"]).exit_code != 0


def test_pnl_import_runs_wide_and_prints(tmp_path, settings, monkeypatch):
    import skinpulse.cli as cli_mod
    import skinpulse.services.pnl as svc
    from tests.test_web_overview import _rt as web_rt

    rt, _ = web_rt(tmp_path, settings, [])
    seen = {}

    def fake_run(rt_, day, now, *, import_all=False, echo=None):
        seen["import_all"] = import_all
        echo("号 1：新买入 3 笔")
        return svc.PnlReport(day=day.isoformat(), accounts=1, new_buys=3)

    monkeypatch.setattr(cli_mod, "_rt", lambda settings: rt)
    monkeypatch.setattr(svc, "run_pnl", fake_run)
    r = CliRunner().invoke(app, ["pnl", "import"])
    assert r.exit_code == 0, r.output
    assert seen == {"import_all": True} and "新买入 3 笔" in r.output and "new_buys=3" in r.output
