from __future__ import annotations

import io
import json
import logging

import pytest

from skinpulse.logging_setup import RedactFilter, setup_logging


def test_redact_filter_redacts_msg():
    f = RedactFilter()
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, "leaked: Bearer abcdef123", None, None)
    assert f.filter(record) is True
    assert "abcdef123" not in record.msg
    assert "Bearer ***" in record.msg


def test_redact_filter_redacts_lazy_args():
    """A call like log.error('...%s', 'Bearer ' + token) puts the token in
    record.args, not record.msg - the formatter interpolates %-style args later,
    so a filter that only rewrites msg lets the token straight through. Regression
    coverage for the also-fix item: RedactFilter must scrub record.args too."""
    f = RedactFilter()
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, "auth failed: %s",
                               ("Bearer secrettoken123",), None)
    assert f.filter(record) is True
    assert "secrettoken123" not in record.getMessage()
    assert "Bearer ***" in record.getMessage()


def test_redact_filter_end_to_end_lazy_args_via_logger():
    """Same bug, exercised through a real logging call (the actual call shape a
    call site would use) rather than a hand-built LogRecord."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactFilter())
    logger = logging.getLogger("skinpulse.test_redact_e2e")
    logger.setLevel(logging.ERROR)
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.error("token leaked as arg: %s", "Bearer topsecrettoken999")
    finally:
        logger.removeHandler(handler)
    output = stream.getvalue()
    assert "topsecrettoken999" not in output
    assert "Bearer ***" in output


# ---- setup_logging 的 console_level（launchd 下只让警告以上进 launchd.log，spec §11.1）----

@pytest.fixture
def isolated_root_logger():
    """setup_logging 会清空并替换 root 的 handler：测完要恢复原样，并关掉新建的
    handler（RotatingFileHandler 不关会在 GC 时报 ResourceWarning）。"""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    yield root
    for h in root.handlers:
        h.close()
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def _handlers(root):
    file_h = next(h for h in root.handlers if isinstance(h, logging.FileHandler))
    console_h = next(h for h in root.handlers if not isinstance(h, logging.FileHandler))
    return console_h, file_h


def test_default_console_level_is_info(tmp_path, isolated_root_logger):
    setup_logging(tmp_path)
    console_h, file_h = _handlers(isolated_root_logger)
    assert console_h.level == logging.INFO
    assert file_h.level == logging.INFO
    assert isolated_root_logger.level == logging.INFO


def test_console_warning_keeps_info_in_the_log_file(tmp_path, isolated_root_logger, capsys):
    setup_logging(tmp_path, console_level="WARNING")  # StreamHandler 在这里绑定被 capsys 换掉的 stderr
    log = logging.getLogger("skinpulse.test_console_level")
    log.info("round: routine info line")
    log.warning("something worth a look")
    for h in isolated_root_logger.handlers:
        h.flush()

    err = capsys.readouterr().err
    assert "routine info line" not in err
    assert "something worth a look" in err
    lines = [json.loads(x) for x in (tmp_path / "logs" / "skinpulse.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["msg"] for r in lines] == ["round: routine info line", "something worth a look"]


def test_console_debug_is_not_masked_by_root_level(tmp_path, isolated_root_logger):
    setup_logging(tmp_path, console_level="DEBUG")
    console_h, file_h = _handlers(isolated_root_logger)
    assert console_h.level == logging.DEBUG
    assert file_h.level == logging.INFO
    assert isolated_root_logger.level == logging.DEBUG


def test_level_names_are_case_insensitive(tmp_path, isolated_root_logger):
    setup_logging(tmp_path, level="info", console_level="warning")
    console_h, file_h = _handlers(isolated_root_logger)
    assert (console_h.level, file_h.level, isolated_root_logger.level) == (logging.WARNING, logging.INFO, logging.INFO)
