"""Structured JSON logging with a trace identifier attached to each record."""

import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any


trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")

_STANDARD_RECORD_FIELDS = frozenset(
    logging.LogRecord(
        name="",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="",
        args=(),
        exc_info=None,
    ).__dict__
)


class JsonFormatter(logging.Formatter):
    """Serialize a log record as one UTF-8-friendly JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        """Format standard fields and any extra structured fields."""
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": trace_id_var.get(),
        }
        payload.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_")
            }
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Configure the root logger with a single JSON stream handler."""
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
