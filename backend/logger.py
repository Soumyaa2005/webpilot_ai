"""Structured logging with secret redaction."""
from __future__ import annotations

import logging
import os
import re

_KEY_RE = re.compile(r"gsk_[A-Za-z0-9]{10,}")


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        msg = _KEY_RE.sub("[REDACTED]", msg)
        secret = os.environ.get("GROQ_API_KEY")
        if secret:
            msg = msg.replace(secret, "[REDACTED]")
        record.msg, record.args = msg, ()
        return True


def get_logger(name: str = "webpilot") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s", "%H:%M:%S"))
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger
