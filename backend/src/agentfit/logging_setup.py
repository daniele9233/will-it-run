"""Structured JSON-lines logging with secret redaction."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import Any

from agentfit import paths

REDACTED = "[REDACTED]"

_SENSITIVE_KEY = re.compile(
    r"(pass(word|wd)?|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key|session[_-]?id)",
    re.IGNORECASE,
)

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {REDACTED}"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL), REDACTED),
    (re.compile(r"\b(hf_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|lm-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,})\b"), REDACTED),
    (
        re.compile(
            r"(?i)\b(pass(?:word|wd)?|secret|token|api[_-]?key|cookie|credential)s?(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
        ),
        rf"\1\2{REDACTED}",
    ),
]


def redact_text(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact(value: Any) -> Any:
    """Recursively redact secrets from strings, mappings and sequences."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            k: (REDACTED if isinstance(k, str) and _SENSITIVE_KEY.search(k) and v else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_text(record.getMessage()),
        }
        extra = getattr(record, "data", None)
        if extra is not None:
            payload["data"] = redact(extra)
        if record.exc_info:
            payload["exc"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


_configured = False


def configure_logging(level: int = logging.INFO, *, console: bool = False) -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger("agentfit")
    root.setLevel(level)
    root.propagate = False
    handler = RotatingFileHandler(
        paths.logs_dir() / "agentfit.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(JsonFormatter())
        root.addHandler(stream)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"agentfit.{name}")


def log_event(logger: logging.Logger, msg: str, **data: Any) -> None:
    logger.info(msg, extra={"data": data})


def read_log_tail(lines: int = 300) -> list[dict[str, Any]]:
    path = paths.logs_dir() / "agentfit.log"
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]:
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            out.append({"level": "INFO", "msg": raw})
    return out
