"""
Client for the Open-Meteo ECMWF ensemble endpoint with file-based caching.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import requests

from ..util import ensure_directory, is_file_stale

logger = logging.getLogger(__name__)

BASE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
HOURLY_FIELDS = ",".join(
    [
        "temperature_2m",
        "dewpoint_2m",
        "precipitation",
        "snowfall",
        "weather_code",
        "cloud_cover",
        "wind_speed_10m",
        "wind_direction_10m",
        "wind_gusts_10m",
    ]
)

WINDSPEED_CONVERSIONS = {"kph": "kmh", "kt": "kn", "mps": "ms"}

ENSEMBLE_MODELS = {
    "ecmwf_ifs025": {
        "name": "ECMWF IFS 0.25° ensemble",
        "members": 51,
        "ack_url": "https://apps.ecmwf.int/datasets/licences/general/",
    },
    "ecmwf_aifs025": {
        "name": "ECMWF AIFS 0.25° ensemble",
        "members": 51,
        "ack_url": "https://apps.ecmwf.int/datasets/licences/general/",
    },
    "gem_global": {
        "name": "ECCC GEM Global ensemble",
        "members": 21,
        "provider": "Environment and Climate Change Canada",
    },
    "ukmo_global_ensemble_20km": {
        "name": "UKMO MOGREPS-G 20 km ensemble",
        "members": 21,
        "provider": "UK Met Office",
    },
    "ukmo_uk_ensemble_2km": {
        "name": "UKMO MOGREPS-UK 2 km ensemble",
        "members": 3,
        "provider": "UK Met Office",
    },
    "gfs025": {
        "name": "NOAA GFS 0.25° ensemble",
        "members": 31,
        "provider": "NOAA",
    },
    "icon_seamless": {
        "name": "DWD ICON seamless ensemble",
        "members": 40,
        "provider": "Deutscher Wetterdienst",
    },
}

DEFAULT_ENSEMBLE_MODEL = "ecmwf_ifs025"


@dataclass(frozen=True)
class ForecastRequest:
    """
    Parameters for an ECMWF ensemble forecast request.

    Attributes:
        latitude: Target latitude.
        longitude: Target longitude.
        timezone: Timezone string (e.g., "America/New_York").
        forecast_days: Number of days to fetch (default 4).
        temperature_unit: "celsius" or "fahrenheit".
        windspeed_unit: "kph", "mph", "ms", or "kn".
        precipitation_unit: "mm" or "inch".
        models: Comma-separated list of models (default "ecmwf_ifs025").
        cache_ttl_minutes: How long to keep the cache valid (default 60).
        cache_dir: Directory to store cache files.
    """
    latitude: float
    longitude: float
    timezone: str
    forecast_days: int = 4
    temperature_unit: str = "celsius"
    windspeed_unit: str = "kph"
    precipitation_unit: str = "mm"
    models: str = DEFAULT_ENSEMBLE_MODEL
    cache_ttl_minutes: int = 60
    cache_dir: Path = field(default_factory=lambda: Path("ibf_cache/forecasts"))


@dataclass
class ForecastResponse:
    """
    Wrapper for the raw forecast data.

    Attributes:
        raw: The raw JSON dictionary from Open-Meteo.
        from_cache: True if served from local cache.
        cache_path: Path to the cache file used (if any).
    """
    raw: Dict[str, object]
    from_cache: bool
    cache_path: Optional[Path] = None


def fetch_forecast(request: ForecastRequest) -> ForecastResponse:
    """
    Fetch ensemble data with caching and simple retries.

    If a valid cache file exists, it is returned immediately. Otherwise, the data
    is downloaded from Open-Meteo, validated, and cached.

    Args:
        request: A ForecastRequest object containing all parameters.

    Returns:
        A ForecastResponse object with the data.

    Raises:
        RuntimeError: If the download fails after retries.
    """
    if request.cache_ttl_minutes > 0:
        cleanup_forecast_cache(request.cache_dir)

    cache_path = _cache_path(request)
    if request.cache_ttl_minutes > 0:
        cached_data = _load_cache(cache_path, request.cache_ttl_minutes)
        if cached_data is not None:
            logger.debug("Loaded forecast cache for %s", cache_path.name)
            return ForecastResponse(raw=cached_data, from_cache=True, cache_path=cache_path)

    data = _download_forecast(request)
    if request.cache_ttl_minutes > 0:
        _write_cache(cache_path, data)

    return ForecastResponse(raw=data, from_cache=False, cache_path=cache_path)


def _cache_key(request: ForecastRequest) -> str:
    """Create a stable filename fragment for a forecast request."""
    lat_suffix = "N" if request.latitude >= 0 else "S"
    lon_suffix = "E" if request.longitude >= 0 else "W"
    return (
        f"{abs(round(request.latitude, 2))}{lat_suffix}_"
        f"{abs(round(request.longitude, 2))}{lon_suffix}_"
        f"{request.forecast_days}_{request.temperature_unit}_"
        f"{request.precipitation_unit}_{request.windspeed_unit}"
    )


def _cache_path(request: ForecastRequest) -> Path:
    """Return the full cache path for a request, ensuring the directory exists."""
    cache_dir = ensure_directory(request.cache_dir)
    return cache_dir / f"{_cache_key(request)}.json"


def _load_cache(path: Path, ttl_minutes: int) -> Optional[Dict[str, object]]:
    """Read cached forecast data if the file exists and is fresh enough."""
    if not path.exists():
        return None
    if is_file_stale(path, max_age_minutes=ttl_minutes):
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read cache %s (%s). Ignoring.", path, exc)
        return None


def _write_cache(path: Path, data: Dict[str, object]) -> None:
    """Persist JSON forecast data to the cache file."""
    try:
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to write cache %s (%s).", path, exc)


def cleanup_forecast_cache(cache_dir: Path, max_age_hours: int = 48) -> None:
    """Delete forecast cache files older than the supplied age threshold."""
    if max_age_hours <= 0:
        return
    directory = ensure_directory(cache_dir)
    cutoff = time.time() - (max_age_hours * 3600)
    for path in directory.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def _download_forecast(request: ForecastRequest) -> Dict[str, object]:
    """Call Open-Meteo with basic retries and validation."""
    params = {
        "latitude": request.latitude,
        "longitude": request.longitude,
        "hourly": HOURLY_FIELDS,
        "timezone": request.timezone,
        "forecast_days": request.forecast_days,
        "temperature_unit": request.temperature_unit,
        "windspeed_unit": WINDSPEED_CONVERSIONS.get(request.windspeed_unit, request.windspeed_unit),
        "precipitation_unit": request.precipitation_unit,
        "models": request.models,
    }

    last_error: Optional[str] = None
    for attempt in range(1, 4):
        try:
            response = requests.get(BASE_URL, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            _validate_response(data)
            logger.info("Fetched Open-Meteo forecast (%s)", response.url)
            return data
        except requests.RequestException as exc:
            last_error = f"HTTP error calling Open-Meteo: {exc}"
            logger.warning("%s (attempt %s/3)", last_error, attempt)
        except json.JSONDecodeError as exc:
            last_error = f"Invalid JSON from Open-Meteo: {exc}"
            logger.warning("%s (attempt %s/3)", last_error, attempt)
        except ValueError as exc:
            last_error = str(exc)
            logger.warning("%s (attempt %s/3)", last_error, attempt)

        if attempt < 3:
            time.sleep(2 ** (attempt - 1))

    raise RuntimeError(last_error or "Failed to fetch Open-Meteo forecast.")


def _validate_response(data: Dict[str, object]) -> None:
    """Ensure the Open-Meteo payload contains the expected structure."""
    if not isinstance(data, dict):
        raise ValueError("Open-Meteo response must be a JSON object.")
    hourly = data.get("hourly")
    if not isinstance(hourly, dict) or "time" not in hourly:
        raise ValueError("Open-Meteo response missing 'hourly.time' data.")

