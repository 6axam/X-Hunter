"""Small, dependency-free structured logging for long-running Docker services.

The log files intentionally contain operational metadata only.  Payloads, Telegram
init data, bot tokens and OpenRouter keys are never put into log records.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import Any

from app.config import Settings

_SECRET_PATTERN = re.compile(
    r"(?i)(authorization|api[_-]?key|token|password|secret)\s*[=:]\s*[^\s,;]+"
)


class JsonFormatter(logging.Formatter):
    """Emit one safe JSON object per line, easy to search with jq or rg."""

    def __init__(self, service: str | None = None) -> None:
        super().__init__()
        self.service = service or os.getenv("SERVICE_NAME", "backend")

    def format(self, record: logging.LogRecord) -> str:
        message = _SECRET_PATTERN.sub(r"\1=[REDACTED]", record.getMessage())
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service", self.service),
            "logger": record.name,
            "message": message,
        }
        for key in ("request_id", "research_id", "search_id", "item_id", "event", "status"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = str(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(settings: Settings, service: str | None = None) -> None:
    """Configure stderr and a rotating persistent file exactly once per process."""

    service_name = service or os.getenv("SERVICE_NAME", "backend")
    root = logging.getLogger()
    if getattr(root, "_olx_hunter_configured", False):
        return

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root.setLevel(level)
    formatter = JsonFormatter(service_name)
    stream = logging.StreamHandler(sys.stdout)
    stream.setLevel(level)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    try:
        settings.log_root.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            settings.log_root / f"{service_name}.log",
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        # Stderr remains available through Docker even if a mounted volume is unavailable.
        root.warning("Persistent log file is unavailable", exc_info=True)

    root._olx_hunter_configured = True  # type: ignore[attr-defined]
    logging.getLogger(__name__).info(
        "Logging configured",
        extra={"service": service_name, "event": "logging_configured"},
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
