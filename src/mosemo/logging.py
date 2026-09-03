import json
import logging
import math
import os
import re
import traceback
from datetime import UTC, datetime
from logging.config import dictConfig
from pathlib import Path
from typing import Final

SERVICE_NAME: Final = "mosemo"
OPTIONAL_FIELDS: Final = (
    "error_code",
    "duration_ms",
    "request_id",
    "operation",
    "outcome",
    "attempt",
    "retryable",
    "status_code",
    "method",
    "route",
    "request_url",
)
REDACTIONS: Final = (
    (
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
        "Bearer [REDACTED]",
    ),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
        "[REDACTED]",
    ),
    (
        re.compile(
            r"\b(?:access_?token|refresh_?token|authorization|password|secret|"
            r"cookie|code|state|user_?id|account_?id|device_?id)="
            r"[^&\s,]+",
            re.IGNORECASE,
        ),
        "[REDACTED]",
    ),
)


def _is_json_scalar(value: object) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    return value is None or isinstance(value, (str, int, bool))


def _redact(message: str) -> str:
    for pattern, replacement in REDACTIONS:
        message = pattern.sub(replacement, message)
    return message


class SensitiveDataFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> logging.LogRecord:
        sanitized_record = logging.makeLogRecord(record.__dict__.copy())
        sanitized_record.msg = _redact(record.getMessage())
        sanitized_record.args = ()

        for field in OPTIONAL_FIELDS:
            value = getattr(sanitized_record, field, None)
            if isinstance(value, str):
                setattr(sanitized_record, field, _redact(value))

        return sanitized_record


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "service": SERVICE_NAME,
            "environment": os.getenv("MOSEMO_ENV", "unknown"),
            "logger": record.name,
            "level": record.levelname,
            "event": record.getMessage(),
        }

        for field in OPTIONAL_FIELDS:
            value = getattr(record, field, None)
            if value is not None and _is_json_scalar(value):
                payload[field] = value

        if record.exc_info is not None:
            exception_type, _, exception_traceback = record.exc_info
            if exception_type is not None:
                exception: dict[str, object] = {"type": exception_type.__name__}
                if exception_traceback is not None:
                    frame = traceback.extract_tb(exception_traceback)[-1]
                    exception.update(
                        {
                            "file": Path(frame.filename).name,
                            "function": frame.name,
                            "line": frame.lineno,
                        }
                    )
                payload["exception"] = exception

        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )


def configure_logging() -> None:
    config_path = Path(__file__).with_name("logging.json")
    with config_path.open(encoding="utf-8") as config_file:
        config = json.load(config_file)
    dictConfig(config)
