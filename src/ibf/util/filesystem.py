"""
Filesystem helpers shared across pipeline modules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def ensure_directory(path: Path | str) -> Path:
    """
    Ensure a directory exists, returning the resolved Path.
    """
    resolved = Path(path).expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def write_text_file(path: Path | str, content: str, encoding: str = "utf-8") -> Path:
    """
    Write text to a file, creating parent directories as needed.
    """
    target = Path(path).expanduser().resolve()
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding=encoding)
    return target

