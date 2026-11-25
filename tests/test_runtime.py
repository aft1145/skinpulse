from __future__ import annotations

import logging
from pathlib import Path

import pytest
from sqlalchemy import select

from skinpulse.adapters.ntfy.client import NtfyClient
from skinpulse.adapters.youpin.client import YoupinClient
from skinpulse.db.models import AlertStateRow, NotificationLog
from skinpulse.db.session import make_engine, make_session_factory, upgrade_db, utcnow
from skinpulse.logging_setup import setup_logging
from skinpulse.runtime import Runtime, build_runtime
from skinpulse.secrets import KEY_NTFY_TOPIC, KEY_PUSHPLUS, SecretStore
from skinpulse.services.monitor import log as monitor_log


class _NoKeyring:
    """Stand-in for the `keyring` module: every call raises, forcing SecretStore to
    fall back to its .env file so these tests never touch the developer's real OS
    keychain. build_runtime constructs its own SecretStore internally with no way to
    inject one, so this module-level fake is the only interception point available."""

    def get_password(self, *a):
        raise RuntimeError("keyring disabled in tests")

    def set_password(self, *a):
        raise RuntimeError("keyring disabled in tests")

    def delete_password(self, *a):
        raise RuntimeError("keyring disabled in tests")


@pytest.fixture(autouse=True)
def _no_real_keyring(monkeypatch):
    import skinpulse.secrets as sec

    monkeypatch.setattr(sec, "keyring", _NoKeyring())


def _write_settings(tmp_path: Path, data_dir: Path) -> Path:
    p = tmp_path / "settings.toml"
    p.write_text(f'data_dir = "{data_dir.as_posix()}"\n', encoding="utf-8")
    return p


def _seed_dangling_pending(data_dir: Path, purchase_no: str) -> None:
    db = data_dir / "skinpulse.db"
    upgrade_db(db)
    engine = make_engine(db)
    Session = make_session_factory(engine)
    with Session() as s:
        s.add(AlertStateRow(purchase_no=purchase_no, state="ALERTING", streak=2, last_rank=2, updated_at=utcnow()))
        s.add(NotificationLog(fingerprint=purchase_no, event="ENTER_TOP2", rank=2, payload_json="{}",
                              created_at=utcnow(), status="pending"))
        s.commit()
    engine.dispose()


# ---- finding 3: the pending sweep must run before the first round - and nowhere else ----


def test_build_runtime_leaves_pending_notifications_alone(tmp_path: Path):
    """构造 runtime 不清扫 pending 通知。这个清扫对正在发送中的通知是破坏性的，而
    cli._rt 每条命令都走 build_runtime——放在这里就意味着后台服务跑着的时候，随手一条
    只读命令都可能把服务正在发的告警判死。2026-09-21 真撞上过一次。"""
    data_dir = tmp_path / "data"
    _seed_dangling_pending(data_dir, "P1")
    settings_path = _write_settings(tmp_path, data_dir)

    rt = build_runtime(settings_path)

    with rt.session_factory() as s:
        log_row = s.scalar(select(NotificationLog).where(NotificationLog.fingerprint == "P1"))
        assert log_row.status == "pending"   # 原封不动
        assert s.get(AlertStateRow, "P1").state == "ALERTING"


def test_scheduler_startup_recovers_pending(tmp_path: Path):
    """真正该做清扫的是即将接管轮次的那个进程：build_scheduler。"""
    from skinpulse.scheduler import build_scheduler

    data_dir = tmp_path / "data"
    _seed_dangling_pending(data_dir, "P1")
    settings_path = _write_settings(tmp_path, data_dir)

    rt = build_runtime(settings_path)
    build_scheduler(rt)  # 不 start()，只走构造时的清扫

    with rt.session_factory() as s:
        log_row = s.scalar(select(NotificationLog).where(NotificationLog.fingerprint == "P1"))
        assert log_row.status == "failed"
        state_row = s.get(AlertStateRow, "P1")
        assert state_row.state == "NORMAL"
        assert state_row.streak == 0


def test_recover_pending_runs_once_not_per_round(tmp_path: Path):
    from skinpulse.scheduler import build_scheduler

    data_dir = tmp_path / "data"
    _seed_dangling_pending(data_dir, "P1")
    settings_path = _write_settings(tmp_path, data_dir)

    rt = build_runtime(settings_path)
    build_scheduler(rt)  # recovers P1 once, at scheduler startup

    # A fresh dangling row created *after* startup must not be recovered by a
    # monitor round - only the scheduler startup recovers, and only once. Asserting on
    # this observable DB state (rather than a call counter on recover_pending)
    # also catches a regression where recovery got wired into the round loop
    # instead of - or in addition to - startup.
    with rt.session_factory() as s:
        s.add(AlertStateRow(purchase_no="P2", state="ALERTING", streak=1, last_rank=2, updated_at=utcnow()))
        s.add(NotificationLog(fingerprint="P2", event="ENTER_TOP2", rank=2, payload_json="{}",
                              created_at=utcnow(), status="pending"))
        s.commit()

    rt.monitor.run_round()  # no Account row exists yet, so this is a safe no-op round

    with rt.session_factory() as s:
        log_row = s.scalar(select(NotificationLog).where(NotificationLog.fingerprint == "P2"))
        assert log_row.status == "pending"  # untouched by the round
        state_row = s.get(AlertStateRow, "P2")
        assert state_row.state == "ALERTING"  # untouched by the round


# ---- Runtime must be fully assembled ----


def test_runtime_fields_populated_with_no_ntfy_topic(tmp_path: Path):
    data_dir = tmp_path / "data"
    settings_path = _write_settings(tmp_path, data_dir)

    rt = build_runtime(settings_path)

    assert isinstance(rt, Runtime)
    assert rt.settings is not None
    assert rt.settings_path == settings_path
    assert rt.secrets is not None
    assert rt.session_factory is not None
    assert isinstance(rt.client, YoupinClient)
    assert rt.ntfy is None  # no ntfy_topic secret configured
    assert rt.monitor is not None


def test_runtime_ntfy_populated_when_topic_secret_configured(tmp_path: Path):
    data_dir = tmp_path / "data"
    SecretStore(data_dir / ".env", use_keyring=False).set(KEY_NTFY_TOPIC, "my-secret-topic")
    settings_path = _write_settings(tmp_path, data_dir)

    rt = build_runtime(settings_path)

    # rt.ntfy 现在是多通道推送器；只配了 ntfy topic 时，里面就只有 ntfy 这一条通道。
    assert rt.ntfy is not None and rt.ntfy.names == ["ntfy"]
    assert isinstance(rt.ntfy.channels[0][1], NtfyClient)


def test_pushplus_is_the_primary_channel_when_configured(tmp_path: Path):
    """配了 PushPlus 就排在最前面：ntfy 在用户手机上从没实时送达过，只能当兜底。"""
    data_dir = tmp_path / "data"
    store = SecretStore(data_dir / ".env", use_keyring=False)
    store.set(KEY_NTFY_TOPIC, "my-secret-topic")
    store.set(KEY_PUSHPLUS, "pp-token")
    settings_path = _write_settings(tmp_path, data_dir)

    rt = build_runtime(settings_path)

    assert rt.ntfy.names == ["pushplus", "ntfy"]


# ---- alembic fileConfig regression: application logging must survive startup ----


def test_logging_survives_build_runtime_then_setup_logging(tmp_path: Path):
    """Pins the alembic fileConfig hazard: fileConfig(disable_existing_loggers=True)
    (the logging.config default) permanently disables every logger that already
    exists at the moment it runs - which includes every skinpulse.* module logger,
    since they're all created at import time via logging.getLogger(__name__). If
    build_runtime's DB migration ever re-introduced that call unconditionally,
    application logging would go silent for the rest of the process, no matter what
    setup_logging does afterwards. monitor_log is the real module-level logger,
    already imported (and thus already created) before build_runtime runs here -
    exactly like production import order.
    """
    data_dir = tmp_path / "data"
    settings_path = _write_settings(tmp_path, data_dir)

    root = logging.getLogger()
    prev_handlers = list(root.handlers)
    prev_level = root.level
    try:
        rt = build_runtime(settings_path)  # migrates the DB via alembic
        setup_logging(rt.settings.data_path)

        assert monitor_log.disabled is False

        monitor_log.info("RUNTIME_STARTUP_PROBE_MARKER")

        log_file = rt.settings.data_path / "logs" / "skinpulse.jsonl"
        assert log_file.exists()
        assert "RUNTIME_STARTUP_PROBE_MARKER" in log_file.read_text(encoding="utf-8")
    finally:
        for h in root.handlers:
            if h not in prev_handlers:
                h.close()
        root.handlers = prev_handlers
        root.setLevel(prev_level)



def test_channel_warning_is_not_raised_just_by_building_the_runtime(tmp_path: Path, caplog):
    """每条命令都会构造 runtime，包括 pushplus-token 自己。在那里提醒「PushPlus 没配置」，
    用户正在配置它的时候就会先看到这句。提醒只该出现在启动监控服务时。"""
    data_dir = tmp_path / "data"
    SecretStore(data_dir / ".env", use_keyring=False).set(KEY_NTFY_TOPIC, "t")
    with caplog.at_level(logging.WARNING, logger="skinpulse.runtime"):
        build_runtime(_write_settings(tmp_path, data_dir))
    assert "PushPlus not configured" not in caplog.text


def test_channel_warning_names_the_missing_primary_channel(caplog):
    from skinpulse.runtime import warn_about_channels
    from skinpulse.services.notifier import FanoutNotifier

    with caplog.at_level(logging.WARNING, logger="skinpulse.runtime"):
        warn_about_channels(FanoutNotifier([("ntfy", object())]))
        warn_about_channels(None)
    assert "PushPlus not configured" in caplog.text
    assert "no notification channel configured" in caplog.text


def test_build_runtime_does_not_reset_alerting_rows_outside_enter_rank(tmp_path: Path):
    """enter_rank 收窄后的复位和 pending 清扫一样，只归 build_scheduler：随手一条 CLI 命令不能改告警状态。"""
    data_dir = tmp_path / "data"
    _seed_dangling_pending(data_dir, "P1")          # ALERTING, last_rank=2
    settings_path = tmp_path / "settings.toml"
    settings_path.write_text(f'data_dir = "{data_dir.as_posix()}"\n\n[alerts]\nenter_rank = 1\n', encoding="utf-8")

    rt = build_runtime(settings_path)

    assert rt.settings.alerts.enter_rank == 1
    with rt.session_factory() as s:
        assert s.get(AlertStateRow, "P1").state == "ALERTING"
