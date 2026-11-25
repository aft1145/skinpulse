from pathlib import Path

from skinpulse.config import PollingSettings, Settings, load_settings, save_settings


def test_load_example_has_defaults(tmp_path: Path):
    example = Path("settings.example.toml").read_text(encoding="utf-8")
    p = tmp_path / "settings.toml"
    p.write_text(example, encoding="utf-8")
    s = load_settings(p)
    # A round is now one authenticated call plus a handful of verification samples
    # (see Monitor.run_round), not one request per order, so the shipped default was
    # lowered from the old per-order-polling-era 15 minutes to 5 - see PollingSettings.
    assert s.polling.round_interval_min == 5
    assert s.alerts.enter_rank == 2
    assert s.alerts.max_gap_ratio == 3.0
    assert s.youpin.pc_headers.platform == "pc"
    assert s.ntfy.base_url == "https://ntfy.sh"


def test_polling_default_round_interval_is_five_minutes():
    assert PollingSettings().round_interval_min == 5


def test_missing_file_yields_defaults(tmp_path: Path):
    s = load_settings(tmp_path / "nope.toml")
    assert s.polling.request_interval_s == 3.0
    assert s.web.port == 8765


def test_round_trip(tmp_path: Path):
    s = Settings()
    s.polling.round_interval_min = 20
    p = tmp_path / "s.toml"
    save_settings(s, p)
    assert load_settings(p).polling.round_interval_min == 20


def test_validation_rejects_bad_values(tmp_path: Path):
    import pytest
    p = tmp_path / "bad.toml"
    p.write_text("[polling]\nround_interval_min = 0\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(p)


def test_alert_settings_rejects_exit_rank_equal_to_enter_rank():
    import pytest
    from pydantic import ValidationError
    from skinpulse.config import AlertSettings

    with pytest.raises(ValidationError) as exc_info:
        AlertSettings.model_validate(
            {"enter_rank": 3, "exit_rank": 3}
        )
    assert "exit_rank (3) must be strictly greater than enter_rank (3)" in str(
        exc_info.value
    )


def test_alert_settings_rejects_exit_rank_less_than_enter_rank():
    import pytest
    from pydantic import ValidationError
    from skinpulse.config import AlertSettings

    with pytest.raises(ValidationError) as exc_info:
        AlertSettings.model_validate(
            {"enter_rank": 5, "exit_rank": 3}
        )
    assert "exit_rank (3) must be strictly greater than enter_rank (5)" in str(
        exc_info.value
    )


def test_alert_settings_defaults_are_valid():
    from skinpulse.config import AlertSettings

    s = AlertSettings()
    assert s.enter_rank == 2
    assert s.exit_rank == 4
    assert s.max_gap_ratio == 3.0


def test_alert_settings_max_gap_ratio_rejects_negative():
    import pytest
    from pydantic import ValidationError
    from skinpulse.config import AlertSettings

    with pytest.raises(ValidationError):
        AlertSettings.model_validate({"max_gap_ratio": -1})


def test_alert_settings_max_gap_ratio_zero_is_allowed():
    from skinpulse.config import AlertSettings

    s = AlertSettings.model_validate({"max_gap_ratio": 0})
    assert s.max_gap_ratio == 0


def test_alert_settings_max_gap_ratio_validates_on_assignment():
    """max_gap_ratio must respect validate_assignment=True like every other field, and
    assigning it must not disturb the enter_rank/exit_rank cross-field validator."""
    import pytest
    from pydantic import ValidationError
    from skinpulse.config import AlertSettings

    s = AlertSettings()
    s.max_gap_ratio = 5.0
    assert s.max_gap_ratio == 5.0
    assert s.exit_rank == 4  # cross-field validator still holds, untouched

    with pytest.raises(ValidationError):
        s.max_gap_ratio = -0.5


def test_alert_settings_valid_combination():
    from skinpulse.config import AlertSettings

    s = AlertSettings.model_validate({"enter_rank": 1, "exit_rank": 2})
    assert s.enter_rank == 1
    assert s.exit_rank == 2


def test_load_settings_rejects_invalid_alert_ranks(tmp_path: Path):
    import pytest
    from pydantic import ValidationError

    p = tmp_path / "invalid_alerts.toml"
    p.write_text("[alerts]\nenter_rank = 5\nexit_rank = 3\n", encoding="utf-8")
    with pytest.raises(ValidationError) as exc_info:
        load_settings(p)
    assert "exit_rank" in str(exc_info.value) and "enter_rank" in str(exc_info.value)


def test_removed_polling_fields_are_gone_from_the_model():
    """per_item_min_interval_min/fast_lane_interval_min/fast_lane_gap_pct/
    account_sync_interval_min described a per-order polling cadence that no longer
    exists after the monitor rework (see services/monitor.py's run_round) - they were
    removed rather than left dormant. fast_lane_rank_threshold and the night_* fields
    were dormant too but are revived (pass B priority and Monitor.run_round's night
    gate, respectively), so they must remain."""
    fields = PollingSettings.model_fields
    for removed in ("per_item_min_interval_min", "fast_lane_interval_min",
                     "fast_lane_gap_pct", "account_sync_interval_min"):
        assert removed not in fields
    for kept in ("round_interval_min", "fast_lane_rank_threshold", "night_start_hour",
                 "night_end_hour", "night_multiplier"):
        assert kept in fields


def test_load_settings_warns_and_succeeds_on_removed_polling_key(tmp_path: Path, caplog):
    """A settings.toml written before this change may still carry a now-removed key
    (e.g. per_item_min_interval_min). load_settings must not raise - it should load
    successfully and warn, naming the ignored key, so the operator notices their file
    is stale instead of the setting silently vanishing with no trace."""
    import logging

    p = tmp_path / "settings.toml"
    p.write_text(
        "[polling]\nper_item_min_interval_min = 10\nround_interval_min = 7\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="skinpulse.config"):
        s = load_settings(p)
    assert s.polling.round_interval_min == 7
    assert "per_item_min_interval_min" in caplog.text


def test_load_settings_warns_naming_every_removed_key_across_sections(tmp_path: Path, caplog):
    import logging

    p = tmp_path / "settings.toml"
    p.write_text(
        "[polling]\n"
        "fast_lane_interval_min = 5\n"
        "fast_lane_gap_pct = 1.0\n"
        "account_sync_interval_min = 60\n"
        "round_interval_min = 5\n"
        "\n[bogus_section]\n"
        "whatever = 1\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="skinpulse.config"):
        s = load_settings(p)
    assert s.polling.round_interval_min == 5
    for name in ("fast_lane_interval_min", "fast_lane_gap_pct", "account_sync_interval_min", "bogus_section"):
        assert name in caplog.text


def test_load_settings_no_warning_when_nothing_unknown(tmp_path: Path, caplog):
    import logging

    p = tmp_path / "settings.toml"
    p.write_text("[polling]\nround_interval_min = 5\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="skinpulse.config"):
        load_settings(p)
    assert caplog.text == ""


def test_alert_settings_validates_on_assignment():
    """Regression test: mutation path is guarded by validate_assignment=True"""
    import pytest
    from pydantic import ValidationError
    from skinpulse.config import Settings

    # Construct a valid Settings with custom values to have more room for testing
    settings = Settings()
    settings.alerts.enter_rank = 3
    assert settings.alerts.enter_rank == 3
    assert settings.alerts.exit_rank == 4

    # Try to assign an invalid exit_rank (equal to enter_rank)
    with pytest.raises(ValidationError) as exc_info:
        settings.alerts.exit_rank = 3
    assert "exit_rank (3) must be strictly greater than enter_rank (3)" in str(
        exc_info.value
    )

    # Verify that assigning a valid value still succeeds
    settings.alerts.exit_rank = 5
    assert settings.alerts.exit_rank == 5

    # Verify other valid assignments still work
    settings.alerts.confirm_rounds = 3
    assert settings.alerts.confirm_rounds == 3


def test_brief_settings_defaults():
    from skinpulse.config import BriefSettings

    b = Settings().brief
    assert isinstance(b, BriefSettings)
    assert (b.enabled, b.send_at, b.snapshot_at) == (True, "00:00", "23:50")
    assert (b.top_rank, b.quiet_days) == (2, 7)
    for gone in ("near_top_count", "movers_count", "price_movers_count"):   # 2026-09-24 改版删掉的三块
        assert not hasattr(b, gone)
    assert b.hm("send_at") == (0, 0) and b.hm("snapshot_at") == (23, 50)


def test_brief_settings_normalizes_and_rejects_times(tmp_path: Path):
    import pytest
    from pydantic import ValidationError

    from skinpulse.config import BriefSettings

    assert BriefSettings(send_at="0:5").send_at == "00:05"
    for bad in ("24:00", "12:60", "noon", "12"):
        with pytest.raises(ValidationError):
            BriefSettings(send_at=bad)
    with pytest.raises(ValidationError):
        BriefSettings(top_rank=0)


def test_example_settings_has_brief_section(tmp_path: Path):
    example = Path("settings.example.toml").read_text(encoding="utf-8")
    p = tmp_path / "settings.toml"
    p.write_text(example, encoding="utf-8")
    s = load_settings(p)
    assert s.brief.send_at == "00:00" and s.brief.quiet_days == 7 and s.brief.top_rank == 2
    assert "near_top_count" not in example and "price_movers_count" not in example


def test_valuation_settings_defaults():
    from skinpulse.config import ValuationSettings

    v = Settings().valuation
    assert isinstance(v, ValuationSettings)
    assert (v.enabled, v.run_at, v.rate_per_min, v.min_bars) == (True, "00:30", 60, 60)
    assert (v.undercut, v.withdraw_fee, v.target_margin, v.min_profit, v.bad_quantile) == (0.01, 0.01, 0.15, 1.0, 0.10)
    # 用户 2026-09-27 给的实测：悠悠 1%，BUFF 自 2026-04-14 起统一 1.5%
    assert v.fees == {"YOUPIN": 0.01, "BUFF": 0.015}
    assert v.hm() == (0, 30)


def test_valuation_settings_rejects_bad_values():
    import pytest
    from pydantic import ValidationError

    from skinpulse.config import ValuationSettings

    assert ValuationSettings(run_at="1:5").run_at == "01:05"
    for bad in (
        {"run_at": "25:00"},
        {"fees": {"YOUPIN": 0.3}},            # 费率超过 20% 多半是填错了（写成了 1 而不是 0.01 之类）
        {"fees": {"YOUPIN": -0.01}},
        {"fees": {"STEAM": 0.15}},            # 只认实际卖出的两个平台
        {"undercut": 0.5},
        {"withdraw_fee": -0.01},
        {"bad_quantile": 0},
        {"bad_quantile": 0.6},
        {"min_bars": 10},
        {"rate_per_min": 0},
        {"rate_per_min": 121},                # SteamDT 单品 K 线配额 120 次/分钟
    ):
        with pytest.raises(ValidationError):
            ValuationSettings(**bad)
    # 只给一个平台时另一个用默认，不是变成「没有费率」
    assert ValuationSettings(fees={"BUFF": 0.02}).fees == {"YOUPIN": 0.01, "BUFF": 0.02}


def test_example_settings_has_valuation_section(tmp_path: Path):
    example = Path("settings.example.toml").read_text(encoding="utf-8")
    p = tmp_path / "settings.toml"
    p.write_text(example, encoding="utf-8")
    s = load_settings(p)
    assert "[valuation]" in example and "[valuation.fees]" in example
    assert s.valuation.run_at == "00:30" and s.valuation.fees == {"YOUPIN": 0.01, "BUFF": 0.015}


def test_valuation_settings_round_trip(tmp_path: Path):
    from skinpulse.config import save_settings

    s = Settings()
    s.valuation.fees = {"YOUPIN": 0.01, "BUFF": 0.02}
    p = tmp_path / "settings.toml"
    save_settings(s, p)
    assert load_settings(p).valuation.fees == {"YOUPIN": 0.01, "BUFF": 0.02}


def test_fills_settings_defaults_and_bounds(tmp_path: Path):
    import pytest
    from pydantic import ValidationError

    from skinpulse.config import FillsSettings

    f = Settings().fills
    assert isinstance(f, FillsSettings)
    assert (f.enabled, f.max_pages, f.resolve_window_h, f.notify_window_h) == (True, 15, 24, 24)
    for bad in ({"max_pages": 0}, {"max_pages": 51}, {"resolve_window_h": 0}, {"notify_window_h": 169}):
        with pytest.raises(ValidationError):
            FillsSettings(**bad)
    example = Path("settings.example.toml").read_text(encoding="utf-8")
    assert "[fills]" in example
    p = tmp_path / "settings.toml"
    p.write_text(example, encoding="utf-8")
    assert load_settings(p).fills.max_pages == 15


def test_pnl_settings_defaults_and_bounds(tmp_path: Path):
    import pytest
    from pydantic import ValidationError

    from skinpulse.config import PnlSettings

    p = Settings().pnl
    assert isinstance(p, PnlSettings)
    assert (p.enabled, p.run_at, p.basis, p.max_pages, p.detail_per_run, p.thin_count, p.spread_ratio) == \
        (True, "00:45", "market", 10, 10, 3, 1.5)
    assert p.hm() == (0, 45)
    for bad in ({"basis": "cost"}, {"run_at": "25:00"}, {"max_pages": 0}, {"spread_ratio": 1.0}, {"thin_count": 0}):
        with pytest.raises(ValidationError):
            PnlSettings(**bad)
    example = Path("settings.example.toml").read_text(encoding="utf-8")
    assert "[pnl]" in example
    f = tmp_path / "settings.toml"
    f.write_text(example, encoding="utf-8")
    assert load_settings(f).pnl.basis == "market"
def test_paused_check_example_matches_defaults(tmp_path: Path):
    p = tmp_path / "settings.toml"
    p.write_text(Path("settings.example.toml").read_text(encoding="utf-8"), encoding="utf-8")
    assert load_settings(p).paused_check == Settings().paused_check
    assert Settings().paused_check.old_days == 60 and Settings().paused_check.verify_max == 30
