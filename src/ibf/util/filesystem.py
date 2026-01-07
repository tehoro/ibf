"""
Filesystem helpers shared across pipeline modules.
"""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock

logger = logging.getLogger(__name__)


def ensure_directory(path: Path | str) -> Path:
    """
    Ensure a directory exists, returning the resolved Path.
    """
    resolved = Path(path).expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _ensure_parent(target: Path) -> None:
    """Ensure the parent directory for target exists."""
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)


def _is_relative_to(path: Path, base: Path) -> bool:
    """Return True if path is under base."""
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def safe_unlink(path: Path | str, *, base_dir: Path | str, dry_run: bool = False) -> bool:
    """Safely delete a file within base_dir, optionally dry-running."""
    target = Path(path).expanduser().resolve()
    base = Path(base_dir).expanduser().resolve()
    if not _is_relative_to(target, base):
        logger.warning("Refusing to delete %s (outside %s)", target, base)
        return False
    if dry_run:
        logger.info("Dry-run: would delete %s", target)
        return True
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.debug("Failed to delete %s (%s)", target, exc)
        return False


@contextmanager
def file_lock(path: Path | str):
    """Context manager for a filesystem lock file alongside the target."""
    target = Path(path).expanduser().resolve()
    lock_path = target.with_suffix(f"{target.suffix}.lock")
    _ensure_parent(lock_path)
    with FileLock(str(lock_path)):
        yield


def _atomic_write_text(target: Path, content: str, encoding: str) -> None:
    """Write text atomically by staging a temp file and renaming."""
    _ensure_parent(target)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def write_text_file(path: Path | str, content: str, encoding: str = "utf-8", *, lock: bool = True) -> Path:
    """
    Write text to a file, creating parent directories as needed.
    """
    target = Path(path).expanduser().resolve()
    if lock:
        with file_lock(target):
            _atomic_write_text(target, content, encoding=encoding)
    else:
        _atomic_write_text(target, content, encoding=encoding)
    return target
