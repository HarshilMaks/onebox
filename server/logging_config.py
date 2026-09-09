from __future__ import annotations

import logging
import re
from contextvars import ContextVar, Token
from logging.config import dictConfig
from typing import Any


_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")
_configured = False
_EMAIL_PATTERN = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-z]{2,}")
_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[^\s,]+")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(token|authorization|oauth[_ -]?(?:code|state)|code|state|query|subject|body|"
    r"recipient(?:_email)?|email_body|tool[_ -]?args?|provider[_ -]?body)\b\s*([:=])\s*[^,\n]+"
)
_SENSITIVE_KEY = re.compile(
    r"(?i)^(token|authorization|oauth[_ -]?(?:code|state)|code|state|query|subject|body|"
    r"recipient(?:_email)?|email_body|tool[_ -]?args?|provider[_ -]?body)$"
)


def bind_correlation_id(value: str) -> Token[str]:
    return _correlation_id.set(value)


def reset_correlation_id(token: Token[str]) -> None:
    _correlation_id.reset(token)


def _redact_text(value: str) -> str:
    value = _BEARER_PATTERN.sub(r"\1[redacted]", value)
    value = _SENSITIVE_ASSIGNMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}[redacted]", value)
    return _EMAIL_PATTERN.sub("[redacted-email]", value)


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]" if _SENSITIVE_KEY.match(str(key)) else _redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return type(value)(_redact_value(item) for item in value)
    if isinstance(value, str):
        # Logger interpolation has no semantic schema. Preserve stable log
        # messages while never placing arbitrary request/provider text in args.
        return "[redacted]"
    return value


class RedactingCorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id.get()
        if isinstance(record.msg, str) and not record.args:
            record.msg = _redact_text(record.msg)
        if record.args:
            record.args = _redact_value(record.args)
        return True


class RedactingFormatter(logging.Formatter):
    def formatException(self, exc_info: tuple[type[BaseException], BaseException, Any]) -> str:
        return _redact_text(super().formatException(exc_info))


LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"redact": {"()": RedactingCorrelationFilter}},
    "formatters": {
        "safe": {
            "()": RedactingFormatter,
            "format": "%(asctime)s %(levelname)s %(name)s correlation_id=%(correlation_id)s %(message)s",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "INFO",
            "formatter": "safe",
            "filters": ["redact"],
        }
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "uvicorn": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "uvicorn.access": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "watchfiles.main": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}


def setup_logging() -> None:
    """Configure safe process logging once from an executable entrypoint."""
    global _configured
    if _configured:
        return
    dictConfig(LOGGING_CONFIG)
    _configured = True
