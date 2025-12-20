"""
Text-related helpers.
"""

from __future__ import annotations

import hashlib
import re

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


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
