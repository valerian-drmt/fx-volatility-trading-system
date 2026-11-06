"""Secret redaction for log lines and error payloads (audit 01, #297).

Two leak paths exist in any service: a connection URL carrying a password
(``redis://:pass@host``, ``postgresql+asyncpg://user:pass@host``) and a
``key=value`` style credential (``password=…``, ``token=…``). Both are
scrubbed here, once, so neither structlog nor stdlib ``logging`` can print
them whatever the call site does.

- :func:`redact_secrets` is the pure text scrubber.
- :func:`redact_url` renders a URL as ``scheme://host:port/path`` with the
  userinfo dropped, for the places that want to log *where* they connect.
- :func:`redact_event_dict` is the structlog processor.
- :class:`RedactingFilter` is the stdlib handler filter.

No import outside the standard library: ``core`` may use it too.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlsplit

MASK = "***"

# scheme://user:password@host  →  scheme://user:***@host
# The password group stops at the first "@" or whitespace; a password that
# itself contains "@" is URL-encoded in any valid URL, so this is exact.
_URL_USERINFO = re.compile(r"(\w[\w+.\-]*://[^/\s@:]*:)([^@\s]+)(@)")

# password=hunter2, "password": "hunter2", token: abc, Authorization: Bearer x
_KV_SECRET = re.compile(
    r"(?i)((?:password|passwd|pwd|secret|token|api[_-]?key|authorization|credential)"
    r"['\"]?\s*[=:]\s*['\"]?)((?:(?:bearer|basic|token)\s+)?[^\s'\",;&]+)"
)

# Event-dict keys whose whole value is a secret.
_SECRET_KEYS = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|authorization|credential)"
)


def redact_secrets(text: str) -> str:
    """Return ``text`` with URL passwords and ``key=value`` credentials masked."""
    if not text:
        return text
    text = _URL_USERINFO.sub(rf"\g<1>{MASK}\g<3>", text)
    return _KV_SECRET.sub(rf"\g<1>{MASK}", text)


def redact_url(url: str | None) -> str | None:
    """``scheme://host:port/path`` without userinfo (or the input when it is
    not a URL). Use it to log a connection target: it never carries a secret."""
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return redact_secrets(url)
    if not parts.scheme or parts.hostname is None:
        return redact_secrets(url)
    host = parts.hostname
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return f"{parts.scheme}://{host}{parts.path or ''}"


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {
            k: (MASK if isinstance(k, str) and _SECRET_KEYS.search(k) else _redact_value(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_value(v) for v in value)
    return value


def redact_event_dict(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor: scrub every string in the event dict (nested too)
    and blank any key that names a secret. Put it right before the renderer."""
    return _redact_value(event_dict)


class RedactingFilter(logging.Filter):
    """stdlib handler filter: scrub the *formatted* message, so ``%s``
    interpolation of a URL or an exception text is covered as well."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # malformed %-args: let the record through unchanged
            return True
        clean = redact_secrets(msg)
        if clean != msg:
            record.msg = clean
            record.args = ()
        return True


def install_stdlib_redaction() -> None:
    """Attach :class:`RedactingFilter` to every root handler (idempotent).

    Handler filters, unlike logger filters, see the records of every child
    logger, which is what makes this a single choke point."""
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
