"""The tool-argument secret sanitiser, shared by the call log and the trace.

Stdlib only, with no import-time side effects: the trace imports it inside
toolkit host processes, where ``toolbase.config`` (which creates
``~/.toolbase`` on import) should not run.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Argument names that might hold a secret. Matched as substrings of the
# lower-cased key.
SECRET_KEYS = frozenset({
    'password', 'passwd', 'pwd',
    'token', 'api_key', 'apikey', 'key',
    'secret', 'auth', 'credential'
})

REDACTED = "***REDACTED***"


def is_secret_key(key: str) -> bool:
    key_lower = str(key).lower()
    return any(secret in key_lower for secret in SECRET_KEYS)


def sanitize_args(
    args: Dict[str, Any],
    *,
    max_items: Optional[int] = 10,
    max_str: Optional[int] = None,
) -> Dict[str, Any]:
    """Return a copy of ``args`` with secret-looking keys redacted.

    The defaults reproduce the call log's historical behaviour exactly:
    lists longer than ``max_items`` collapse to a ``<list of N items>``
    placeholder, nested dicts recurse, other types show only their type.

    The trace passes ``max_items=None`` (keep whole lists, recursing into
    them so a secret inside a list of dicts is still redacted) and a
    ``max_str`` cap, so a record stays small when an argument carries a
    file's contents.
    """
    sanitized: Dict[str, Any] = {}
    for key, value in args.items():
        if is_secret_key(key):
            sanitized[key] = REDACTED
        else:
            sanitized[key] = _value(value, max_items, max_str)
    return sanitized


def _value(value: Any, max_items: Optional[int], max_str: Optional[int]) -> Any:
    if isinstance(value, str):
        if max_str is not None and len(value) > max_str:
            return value[:max_str] + f"...[truncated {len(value) - max_str} chars]"
        return value
    if isinstance(value, (int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        if max_items is not None:
            if len(value) > max_items:
                return f"<{type(value).__name__} of {len(value)} items>"
            return value
        return [_value(v, max_items, max_str) for v in value]
    if isinstance(value, dict):
        return sanitize_args(value, max_items=max_items, max_str=max_str)
    return f"<{type(value).__name__}>"
