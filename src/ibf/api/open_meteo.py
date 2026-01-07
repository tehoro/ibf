"""
Client for Open-Meteo forecast endpoints with file-based caching.

Supports both:
- Ensemble endpoint (ensemble models, multiple members)
- Forecast endpoint (deterministic models, single "member00")
"""

from __future__ import annotations

import json
import logging
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Literal, Any

import requests

from ..util import ensure_directory, format_request_exception, is_file_stale, safe_unlink, write_text_file

logger = logging.getLogger(__name__)

ENSEMBLE_BASE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
FORECAST_BASE_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_FIELDS_BASE = ",".join(
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

# Open-Meteo requests are always normalized to these units for caching and processing.
STANDARD_TEMPERATURE_UNIT = "celsius"
STANDARD_PRECIPITATION_UNIT = "mm"
STANDARD_WINDSPEED_UNIT = "kph"

# Deterministic models typically include probability of precipitation.
# Only request this field for deterministic requests to avoid incompatibilities
# with the ensemble endpoint.
HOURLY_FIELDS_DETERMINISTIC = ",".join(
    [
        HOURLY_FIELDS_BASE,
        "precipitation_probability",
        # Optional per-model; if unsupported by a deterministic model the client will
        # fall back to the base deterministic fields without it.
        "freezing_level_height",
    ]
)

# Pressure levels used for snow-level estimation when freezing level is unavailable.
# These correspond to Open-Meteo hourly variable name suffixes like `temperature_850hPa`.
PRESSURE_LEVELS_SNOW_HPA = (1000, 925, 850, 700, 600, 500)

# Hourly variables required to compute snow level from pressure-level profiles.
HOURLY_FIELDS_SNOW_PROFILE = ",".join(
    [
        "surface_pressure",
        *[f"temperature_{level}hPa" for level in PRESSURE_LEVELS_SNOW_HPA],
        *[f"relative_humidity_{level}hPa" for level in PRESSURE_LEVELS_SNOW_HPA],
        *[f"geopotential_height_{level}hPa" for level in PRESSURE_LEVELS_SNOW_HPA],
    ]
)

WINDSPEED_CONVERSIONS = {"kph": "kmh", "kt": "kn", "mps": "ms"}

ModelKind = Literal["ensemble", "deterministic"]

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

DETERMINISTIC_MODELS: Dict[str, Dict[str, Any]] = {
    # Open-Meteo ECMWF IFS HRES 9 km deterministic
    # https://open-meteo.com/en/docs/ecmwf-api
    "ecmwf_ifs": {
        "name": "ECMWF IFS HRES 9 km (deterministic)",
        "ack_url": "https://apps.ecmwf.int/datasets/licences/general/",
    },
    # Open-Meteo DWD ICON seamless deterministic
    # https://open-meteo.com/en/docs/dwd-api
    "icon_seamless": {
        "name": "DWD ICON seamless (deterministic)",
        "provider": "Deutscher Wetterdienst",
    },
    # Open-Meteo auto-select deterministic model (omit the models param).
    "open-meteo": {
        "name": "Open-Meteo auto (best available deterministic)",
        "provider": "Open-Meteo",
    },
}


@dataclass(frozen=True)
class ModelSpec:
    """Resolved model metadata used to route API calls and label outputs."""

    ref: str
    kind: ModelKind
    model_id: str
    name: str
    members: int = 1
    provider: Optional[str] = None
    ack_url: Optional[str] = None


def resolve_model_spec(value: Optional[str]) -> ModelSpec:
    """
    Resolve a model reference into a ModelSpec.

    Accepted forms:
    - "ens:<open-meteo-ensemble-id>" (explicit ensemble)
    - "det:<open-meteo-forecast-id>" (explicit deterministic)
    - "<open-meteo-ensemble-id>"     (back-compat: treated as ensemble if known)
    - "<open-meteo-forecast-id>"     (treated as deterministic otherwise)
    """
    raw = (value or "").strip()
    if not raw:
        raw = f"ens:{DEFAULT_ENSEMBLE_MODEL}"

    prefix: Optional[str] = None
    rest = raw
    if ":" in raw:
        candidate_prefix, candidate_rest = raw.split(":", 1)
        candidate_prefix = candidate_prefix.strip().lower()
        if candidate_prefix in {"ens", "ensemble"}:
            prefix = "ens"
            rest = candidate_rest.strip()
        elif candidate_prefix in {"det", "deterministic"}:
            prefix = "det"
            rest = candidate_rest.strip()

    if not rest:
        rest = DEFAULT_ENSEMBLE_MODEL

    if prefix == "ens":
        kind: ModelKind = "ensemble"
        model_id = rest
    elif prefix == "det":
        kind = "deterministic"
        model_id = rest
    else:
        # Back-compat / convenience: infer kind by known ensemble IDs.
        kind = "ensemble" if rest in ENSEMBLE_MODELS else "deterministic"
        model_id = rest

    if kind == "ensemble":
        info = ENSEMBLE_MODELS.get(model_id)
        if not info:
            logger.warning(
                "Unknown ensemble model '%s'; falling back to %s",
                model_id,
                DEFAULT_ENSEMBLE_MODEL,
            )
            model_id = DEFAULT_ENSEMBLE_MODEL
            info = ENSEMBLE_MODELS.get(model_id, {})
        members = int(info.get("members") or 1)
        return ModelSpec(
            ref=f"ens:{model_id}",
            kind="ensemble",
            model_id=model_id,
            name=str(info.get("name") or model_id),
            members=max(1, members),
            provider=info.get("provider"),
            ack_url=info.get("ack_url"),
        )

    # Deterministic: allow unknown IDs (Open-Meteo may add models); treat as 1 member.
    dinfo = DETERMINISTIC_MODELS.get(model_id, {})
    return ModelSpec(
        ref=f"det:{model_id}",
        kind="deterministic",
        model_id=model_id,
        name=str(dinfo.get("name") or model_id),
        members=1,
        provider=dinfo.get("provider"),
        ack_url=dinfo.get("ack_url"),
    )


@dataclass(frozen=True)
class ForecastRequest:
    """
    Parameters for an Open-Meteo forecast request.

    Attributes:
        latitude: Target latitude.
        longitude: Target longitude.
        timezone: Timezone string (e.g., "America/New_York").
        forecast_days: Number of days to fetch (default 4).
        temperature_unit: Always normalized to "celsius".
        windspeed_unit: Always normalized to "kph".
        precipitation_unit: Always normalized to "mm".
        models: Open-Meteo model identifier (default "ecmwf_ifs025"). Use None to omit.
        model_kind: "ensemble" or "deterministic" (routes to correct endpoint).
        hourly_fields: Optional explicit hourly field list override.
        cache_ttl_minutes: How long to keep the cache valid (default 60).
        cache_dir: Directory to store cache files.
    """
    latitude: float
    longitude: float
    timezone: str
    forecast_days: int = 4
    temperature_unit: str = STANDARD_TEMPERATURE_UNIT
    windspeed_unit: str = STANDARD_WINDSPEED_UNIT
    precipitation_unit: str = STANDARD_PRECIPITATION_UNIT
    models: Optional[str] = DEFAULT_ENSEMBLE_MODEL
    model_kind: ModelKind = "ensemble"
    hourly_fields: Optional[str] = None
    cache_ttl_minutes: int = 60
    cache_dir: Path = field(default_factory=lambda: Path("ibf_cache/forecasts"))

    def __post_init__(self) -> None:
        """Normalize overridden units back to Open-Meteo defaults."""
        if (
            self.temperature_unit != STANDARD_TEMPERATURE_UNIT
            or self.precipitation_unit != STANDARD_PRECIPITATION_UNIT
            or self.windspeed_unit != STANDARD_WINDSPEED_UNIT
        ):
            logger.warning(
                "ForecastRequest units overridden to standard (%s, %s, %s).",
                STANDARD_TEMPERATURE_UNIT,
                STANDARD_PRECIPITATION_UNIT,
                STANDARD_WINDSPEED_UNIT,
            )
            object.__setattr__(self, "temperature_unit", STANDARD_TEMPERATURE_UNIT)
            object.__setattr__(self, "precipitation_unit", STANDARD_PRECIPITATION_UNIT)
            object.__setattr__(self, "windspeed_unit", STANDARD_WINDSPEED_UNIT)


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
    hourly_fields = _hourly_fields_for(request)
    fields_sig = hashlib.sha1(hourly_fields.encode("utf-8")).hexdigest()[:8]
    model_token = (request.models or "auto").strip().lower().replace(",", "+")
    kind_token = "ens" if request.model_kind == "ensemble" else "det"
    temp_unit = STANDARD_TEMPERATURE_UNIT
    precip_unit = STANDARD_PRECIPITATION_UNIT
    wind_unit = STANDARD_WINDSPEED_UNIT
    return (
        f"{abs(round(request.latitude, 2))}{lat_suffix}_"
        f"{abs(round(request.longitude, 2))}{lon_suffix}_"
        f"{request.forecast_days}_{temp_unit}_"
        f"{precip_unit}_{wind_unit}_"
        f"{kind_token}_{model_token}_{fields_sig}"
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
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read cache %s (%s). Deleting.", path, exc)
        _delete_cache_file(path)
        return None
    try:
        _validate_response(data)
    except ValueError as exc:
        logger.warning("Invalid cache %s (%s). Deleting.", path, exc)
        _delete_cache_file(path)
        return None
    return data


def _delete_cache_file(path: Path) -> None:
    """Delete a cached Open-Meteo response file if present."""
    safe_unlink(path, base_dir=path.parent)


def _write_cache(path: Path, data: Dict[str, object]) -> None:
    """Persist JSON forecast data to the cache file."""
    try:
        write_text_file(path, json.dumps(data))
    except OSError as exc:
        logger.warning("Failed to write cache %s (%s).", path, exc)


def cleanup_forecast_cache(cache_dir: Path, max_age_hours: int = 48, *, dry_run: bool = False) -> None:
    """Delete forecast cache files older than the supplied age threshold."""
    if max_age_hours <= 0:
        return
    directory = ensure_directory(cache_dir)
    cutoff = time.time() - (max_age_hours * 3600)
    for path in directory.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                safe_unlink(path, base_dir=directory, dry_run=dry_run)
        except OSError:
            continue


def _download_forecast(request: ForecastRequest) -> Dict[str, object]:
    """Call Open-Meteo with basic retries and validation."""
    base_url = ENSEMBLE_BASE_URL if request.model_kind == "ensemble" else FORECAST_BASE_URL
    primary_hourly_fields = _hourly_fields_for(request)
    hourly_candidates = [primary_hourly_fields]

    # Some deterministic models may not expose freezing level. In many cases Open-Meteo
    # will still return the field but with an all-null series (and units like "undefined"),
    # which is handled at the pipeline layer by detecting "all nulls" and falling back
    # to pressure-level diagnostics only when needed.
    #
    # Separately, if an API/model combination *does* reject the request (HTTP 400),
    # fall back to the base deterministic fields without freezing level.
    if (
        request.model_kind == "deterministic"
        and request.hourly_fields is None
        and "freezing_level_height" in primary_hourly_fields
    ):
        reduced = _remove_hourly_field(primary_hourly_fields, "freezing_level_height")
        if reduced and reduced != primary_hourly_fields:
            hourly_candidates.append(reduced)

    last_error: Optional[str] = None
    for candidate_idx, hourly_fields in enumerate(hourly_candidates, start=1):
        latitude = round(request.latitude, 2)
        longitude = round(request.longitude, 2)
        if (
            request.temperature_unit != STANDARD_TEMPERATURE_UNIT
            or request.precipitation_unit != STANDARD_PRECIPITATION_UNIT
            or request.windspeed_unit != STANDARD_WINDSPEED_UNIT
        ):
            logger.debug(
                "Overriding non-standard units for Open-Meteo request (%s, %s, %s).",
                request.temperature_unit,
                request.precipitation_unit,
                request.windspeed_unit,
            )
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": hourly_fields,
            "timezone": request.timezone,
            "forecast_days": request.forecast_days,
            "temperature_unit": STANDARD_TEMPERATURE_UNIT,
            "windspeed_unit": WINDSPEED_CONVERSIONS.get(STANDARD_WINDSPEED_UNIT, STANDARD_WINDSPEED_UNIT),
            "precipitation_unit": STANDARD_PRECIPITATION_UNIT,
        }
        if request.models:
            params["models"] = request.models

        for attempt in range(1, 4):
            try:
                response = requests.get(base_url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()
                _validate_response(data)
                logger.info("Fetched Open-Meteo forecast (%s)", response.url)
                return data
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                last_error = f"HTTP error calling Open-Meteo: {format_request_exception(exc)}"
                if status == 400 and candidate_idx < len(hourly_candidates):
                    logger.info(
                        "Open-Meteo rejected hourly field-set (candidate %s/%s); retrying with fallback.",
                        candidate_idx,
                        len(hourly_candidates),
                    )
                    break
                logger.warning("%s (attempt %s/3)", last_error, attempt)
            except requests.RequestException as exc:
                last_error = f"HTTP error calling Open-Meteo: {format_request_exception(exc)}"
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


def _hourly_fields_for(request: ForecastRequest) -> str:
    """Return the hourly fields string for this request (affects caching)."""
    if request.hourly_fields:
        return request.hourly_fields
    return HOURLY_FIELDS_BASE if request.model_kind == "ensemble" else HOURLY_FIELDS_DETERMINISTIC


def _remove_hourly_field(fields: str, name: str) -> str:
    """Remove a single hourly field token from a comma-separated field list."""
    tokens = [part.strip() for part in (fields or "").split(",") if part.strip()]
    kept = [tok for tok in tokens if tok != name]
    return ",".join(kept)


def _validate_response(data: Dict[str, object]) -> None:
    """Ensure the Open-Meteo payload contains the expected structure."""
    if not isinstance(data, dict):
        raise ValueError("Open-Meteo response must be a JSON object.")
    hourly = data.get("hourly")
    if not isinstance(hourly, dict) or "time" not in hourly:
        raise ValueError("Open-Meteo response missing 'hourly.time' data.")
