"""
Text-related helpers.
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
_SENSITIVE_QUERY_KEYS = {"key", "api_key", "apikey", "appid", "token", "access_token"}


def slugify(value: str) -> str:
    """
    Generate a filesystem-friendly slug.

    Uses hyphens as separators to reduce collisions.
    """
    raw = (value or "").strip().lower()
    slug = _SLUG_PATTERN.sub("-", raw).strip("-")
    if slug:
        return slug
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return f"item-{digest}"


def redact_url(url: str, *, sensitive_keys: Iterable[str] = _SENSITIVE_QUERY_KEYS) -> str:
    """
    Redact sensitive query parameters in a URL (e.g., key/appid).
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in sensitive_keys:
            query.append((key, "REDACTED"))
        else:
            query.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), parts.fragment))


def format_request_exception(exc: Exception) -> str:
    """
    Format a request exception without exposing sensitive query parameters.
    """
    request = getattr(exc, "request", None)
    response = getattr(exc, "response", None)
    url = getattr(request, "url", None) if request is not None else None
    if not url and response is not None:
        url = getattr(response, "url", None)
    status = getattr(response, "status_code", None)
    message = exc.__class__.__name__
    if status is not None:
        message += f" status={status}"
    if url:
        message += f" url={redact_url(url)}"
    else:
        message += f" detail={exc}"
    return message
