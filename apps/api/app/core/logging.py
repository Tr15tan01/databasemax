"""Structured JSON logging with secret redaction.

Every record passes through `RedactingFilter`, which scrubs:
- PostgreSQL URLs with embedded passwords (postgresql://user:***@host)
- key=value / "key": "value" pairs whose key looks sensitive (password, token, secret, ...)
- DatabaseMax API keys (dmx_...) and bearer tokens
Request bodies are never logged by the application.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import time
from typing import Any

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

_SENSITIVE_KEYS = (
    "password|passwd|pwd|secret|token|authorization|cookie|api[_-]?key|scram|verifier|"
    "client_secret|session|csrf|private[_-]?key"
)
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # postgres://user:password@host  -> postgres://user:***@host
    (re.compile(r"(postgres(?:ql)?(?:\+\w+)?://[^:/@\s]+:)([^@\s]+)(@)", re.I), r"\1***\3"),
    # generic scheme://user:pass@
    (re.compile(r"(\w+://[^:/@\s]+:)([^@\s]+)(@)"), r"\1***\3"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.I), r"\1***"),
    # "password": "value"  / password=value / password: value
    (
        re.compile(rf'(["\']?(?:\w*(?:{_SENSITIVE_KEYS})\w*)["\']?\s*[:=]\s*)(["\']?)([^"\'\s,;}}&]+)(\2)', re.I),
        r"\1\2***\4",
    ),
    (re.compile(r"\bdmx_(live|test)_[A-Za-z0-9_]+"), "dmx_***"),
    (re.compile(r"SCRAM-SHA-256\$[^\s'\"]+"), "SCRAM-SHA-256$***"),
]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - malformed log call
            msg = str(record.msg)
        record.msg = redact(msg)
        record.args = ()
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return redact(json.dumps(payload, default=str))


_configured = False


def configure_logging(level: str = "INFO") -> None:
    global _configured
    root = logging.getLogger()
    if _configured:
        root.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    root.handlers = [handler]
    root.setLevel(level)
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    _configured = True


def log_event(logger: logging.Logger, msg: str, level: int = logging.INFO, **fields: Any) -> None:
    logger.log(level, msg, extra={"fields": fields})
