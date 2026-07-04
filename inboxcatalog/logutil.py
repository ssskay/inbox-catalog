"""Structured logging with a redacting filter.

Logging is verbose by design, but a filter guarantees that any registered secret
(the IMAP password, an API key) can never reach a log line even if it is
accidentally interpolated into a message.
"""
from __future__ import annotations

import logging
import re

_SECRET_PATTERNS: list[re.Pattern] = []


def register_secret(value: str | None) -> None:
    """Register a literal secret value to be scrubbed from all log output."""
    if value and len(value.strip()) >= 4:
        _SECRET_PATTERNS.append(re.compile(re.escape(value.strip())))


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        red = msg
        for pat in _SECRET_PATTERNS:
            red = pat.sub("***REDACTED***", red)
        if red != msg:
            record.msg = red
            record.args = ()
        return True


def setup(debug: bool = False) -> logging.Logger:
    level = logging.DEBUG if debug else logging.INFO
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-5s %(name)s | %(message)s",
                          datefmt="%H:%M:%S")
    )
    handler.addFilter(_RedactFilter())
    root = logging.getLogger("inboxcatalog")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False
    return root


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"inboxcatalog.{name}")
