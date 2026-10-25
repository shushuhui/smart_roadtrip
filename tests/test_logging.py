"""日志格式测试。"""

from __future__ import annotations

import json
import logging

from smart_roadtrip import config
from smart_roadtrip.api.observability import JsonFormatter, configure_logging


def test_json_formatter_outputs_single_line_json() -> None:
    record = logging.LogRecord(
        "smart_roadtrip.test",
        logging.INFO,
        __file__,
        1,
        "hello %s",
        ("world",),
        None,
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "smart_roadtrip.test"
    assert payload["message"] == "hello world"
    assert payload["ts"]


def test_configure_logging_json(monkeypatch) -> None:
    logger = logging.getLogger("smart_roadtrip")
    old_handlers = list(logger.handlers)
    old_level = logger.level

    try:
        monkeypatch.setattr(config, "LOG_FORMAT", "json")
        monkeypatch.setattr(config, "LOG_LEVEL", "DEBUG")
        configure_logging()

        assert logger.level == logging.DEBUG
        assert any(
            isinstance(handler.formatter, JsonFormatter)
            for handler in logger.handlers
        )
    finally:
        logger.handlers = old_handlers
        logger.setLevel(old_level)
