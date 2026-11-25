from __future__ import annotations

import json
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_TOKEN_RE = re.compile(r"Bearer\s+\S+")


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _TOKEN_RE.sub("Bearer ***", record.msg)
        # %-style args are interpolated later by the formatter, so a call like
        # log.error("...%s", "Bearer " + token) puts the token in record.args,
        # not record.msg - redact those too, or they bypass this filter entirely.
        if isinstance(record.args, tuple):
            record.args = tuple(
                _TOKEN_RE.sub("Bearer ***", a) if isinstance(a, str) else a for a in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                k: (_TOKEN_RE.sub("Bearer ***", v) if isinstance(v, str) else v)
                for k, v in record.args.items()
            }
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"), "level": record.levelname,
                           "logger": record.name, "msg": record.getMessage()}, ensure_ascii=False)


def setup_logging(data_dir: Path, level: str = "INFO", console_level: str = "INFO") -> None:
    """`level` 管日志文件，`console_level` 管控制台。launchd 下控制台就是 launchd.log（不轮转），
    所以那里传 WARNING，只留警告、错误和 traceback；完整记录照旧在 jsonl 里（spec §11.1）。"""
    names = logging.getLevelNamesMapping()
    level, console_level = names[level.upper()], names[console_level.upper()]  # 名字不对就 KeyError
    root = logging.getLogger()
    root.setLevel(min(level, console_level))
    root.handlers.clear()
    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(name)s: %(message)s"))
    console.addFilter(RedactFilter())
    root.addHandler(console)
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(logs / "skinpulse.jsonl", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(JsonFormatter())
    fh.addFilter(RedactFilter())
    root.addHandler(fh)
    logging.getLogger("httpx").setLevel("WARNING")
    logging.getLogger("apscheduler").setLevel("WARNING")
