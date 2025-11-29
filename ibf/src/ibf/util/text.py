"""
Text-related helpers.
"""

from __future__ import annotations

import re

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """
    Generate a filesystem-friendly slug.
    """
    return _SLUG_PATTERN.sub("", value.lower())

