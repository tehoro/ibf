"""
Core package for the unified Impact-Based Forecast tooling.
"""

from importlib import metadata as _metadata

try:
    __version__ = _metadata.version("ibf")
except _metadata.PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = ["__version__"]
