"""
Core package for the unified Impact-Based Forecast tooling.
"""

from importlib import metadata as _metadata

_EMBEDDED_VERSION = "0.8.12"


def _resolve_version() -> str:
    """Return installed metadata when available, or the frozen-app version."""
    try:
        return _metadata.version("ibf")
    except _metadata.PackageNotFoundError:
        return _EMBEDDED_VERSION


__version__ = _resolve_version()

__all__ = ["__version__"]
