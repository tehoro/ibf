"""
Environment variable helpers with safe cleanup.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterable, Optional


@contextmanager
def temporary_environ(*, set_vars: dict[str, Optional[str]], remove: Iterable[str] = ()):
    keys = set(set_vars.keys()) | set(remove)
    previous = {key: os.environ.get(key) for key in keys}
    try:
        for key in remove:
            os.environ.pop(key, None)
        for key, value in set_vars.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def force_gemini_api_key(api_key: Optional[str]):
    """
    Ensure GEMINI_API_KEY is set and GOOGLE_API_KEY is hidden for Gemini SDK usage.
    """
    set_vars = {"GEMINI_API_KEY": api_key}
    with temporary_environ(set_vars=set_vars, remove=("GOOGLE_API_KEY",)):
        yield
