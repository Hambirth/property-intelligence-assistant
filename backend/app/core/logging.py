import json
import logging
import re
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")
_URI_CREDENTIAL_PATTERN = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://[^\s:/]+:)[^@\s]+@", re.I)
_OPENROUTER_KEY_PATTERN = re.compile(r"\bsk-or-v1-[A-Za-z0-9_-]+\b")
_BEARER_PATTERN = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+")
_OPENROUTER_ENV_PATTERN = re.compile(r"(?i)(OPENROUTER_API_KEY\s*=\s*)[^\s,;]+")


def redact_secrets(value: str) -> str:
    value = _URI_CREDENTIAL_PATTERN.sub(r"\g<scheme>[REDACTED]@", value)
    value = _OPENROUTER_KEY_PATTERN.sub("[REDACTED]", value)
    value = _BEARER_PATTERN.sub(r"\g<1>[REDACTED]", value)
    return _OPENROUTER_ENV_PATTERN.sub(r"\g<1>[REDACTED]", value)


class JsonFormatter(logging.Formatter):
    """Small JSON formatter with a stable schema and no request-body logging."""

    _standard_attributes = frozenset(logging.makeLogRecord({}).__dict__)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_secrets(record.getMessage()),
            "request_id": request_id_context.get(),
        }
        for key, value in record.__dict__.items():
            if key not in self._standard_attributes and key not in {"message", "asctime"}:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = redact_secrets(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)


def bind_request_id(request_id: str) -> Token[str]:
    return request_id_context.set(request_id)


def reset_request_id(token: Token[str]) -> None:
    request_id_context.reset(token)
