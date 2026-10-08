from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

task_id = ContextVar("task_id", default=None)
movie_key = ContextVar("movie_key", default=None)
progress = ContextVar("progress", default=None)


def report_progress(stage: str, status: str = "running", source=None, message: str | None = None) -> None:
    callback = progress.get()
    if callback:
        callback(
            {
                "stage": stage,
                "status": status,
                "source": source,
                "message": message,
                "time": datetime.now(UTC).isoformat(),
            }
        )


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value = {
            "time": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "task_id": task_id.get(),
            "key": movie_key.get(),
            "message": record.getMessage(),
        }
        if record.exc_info:
            value["exception"] = self.formatException(record.exc_info)
        return json.dumps(value, ensure_ascii=False)


def configure() -> None:
    logger = logging.getLogger("eros_scraper")
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    for handler in (logging.StreamHandler(sys.stderr),):
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def cleanup() -> None:
    logger = logging.getLogger("eros_scraper")
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    logger.propagate = True
