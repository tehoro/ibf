"""
Name-to-coordinate geocoding via the Open-Meteo API with caching.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from typing import Optional

import requests
from timezonefinder import TimezoneFinder

from ..util import ensure_directory, file_lock, format_request_exception, safe_unlink, write_text_file
from ..config.settings import get_secrets

logger = logging.getLogger(__name__)

CACHE_PATH = ensure_directory("ibf_cache/geocode") / "search_cache.json"
_tz_finder = TimezoneFinder()


@dataclass
class GeocodeResult:
    """
    Resolved location data.

    Attributes:
        name: The formatted name of the location.
        latitude: Latitude coordinate.
        longitude: Longitude coordinate.
        timezone: Timezone identifier (e.g., "Europe/London").
        country_code: ISO 3166-1 alpha-2 country code.
        country_name: Human-readable country name.
        admin1: First-level administrative region.
        admin2: Second-level administrative region.
        altitude: Elevation in meters (optional).
    """
    name: str
    latitude: float
    longitude: float
    timezone: str
    country_code: Optional[str] = None
    country_name: Optional[str] = None
    admin1: Optional[str] = None
    admin2: Optional[str] = None
    altitude: Optional[float] = None


def geocode_name(name: str, *, language: str = "en") -> Optional[GeocodeResult]:
    """
    Resolve a place name into coordinates.

    First attempts to use the Open-Meteo Geocoding API. If that fails or returns no results,
    it falls back to the Google Geocoding API when a key is available. Results are cached.

    Args:
        name: The place name to search for (e.g., "London, UK").
        language: Preferred language for the results (default "en").

    Returns:
        A GeocodeResult object if found, otherwise None.
    """
    secrets = get_secrets()

    normalized = name.strip().lower()
    with file_lock(CACHE_PATH):
        cache = _read_cache()
        data = cache.get(normalized)
    if data:
        result = GeocodeResult(**data)
        if not {"country_name", "admin1", "admin2"}.issubset(data):
            result = _enrich_cached_identity(name, result)
            with file_lock(CACHE_PATH):
                cache = _read_cache()
                cache[normalized] = _cache_entry(result)
                _write_cache(cache)
        logger.info(
            "Geocode cache hit for '%s' (lat=%.4f, lon=%.4f)",
            name,
            result.latitude,
            result.longitude,
        )
        return result

    params = {"name": name, "count": 1, "language": language, "format": "json"}
    try:
        resp = requests.get("https://geocoding-api.open-meteo.com/v1/search", params=params, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Open-Meteo geocoding failed for %s: %s", name, format_request_exception(exc))
        result = None
    else:
        payload = resp.json()
        results = payload.get("results") or []
        if not results:
            logger.info("No Open-Meteo geocoding results for %s", name)
            result = None
        else:
            logger.debug("Open-Meteo geocode hit for %s", name)
            entry = results[0]
            result = GeocodeResult(
                name=entry.get("name", name),
                latitude=entry["latitude"],
                longitude=entry["longitude"],
                timezone=entry.get("timezone", "UTC"),
                country_code=entry.get("country_code"),
                country_name=entry.get("country"),
                admin1=entry.get("admin1"),
                admin2=entry.get("admin2"),
                altitude=entry.get("elevation"),
            )
            logger.info(
                "Geocode resolved via Open-Meteo for '%s' (lat=%.4f, lon=%.4f)",
                name,
                result.latitude,
                result.longitude,
            )

    if result is None:
        if not secrets.google_api_key:
            logger.warning(
                "GOOGLE_API_KEY not set; unable to fall back to Google geocoding for '%s'.",
                name,
            )
            return None
        result = _google_geocode(name, secrets.google_api_key)
        if result is None:
            return None

    with file_lock(CACHE_PATH):
        cache = _read_cache()
        cache[normalized] = _cache_entry(result)
        _write_cache(cache)
    return result


def _cache_entry(result: GeocodeResult) -> dict:
    """Return the stable on-disk representation of a geocode result."""
    return {
        "name": result.name,
        "latitude": result.latitude,
        "longitude": result.longitude,
        "timezone": result.timezone,
        "country_code": result.country_code,
        "country_name": result.country_name,
        "admin1": result.admin1,
        "admin2": result.admin2,
        "altitude": result.altitude,
    }


def _enrich_cached_identity(name: str, result: GeocodeResult) -> GeocodeResult:
    """Add administrative identity to a legacy cache entry without moving its point."""
    locality = name.split(",", 1)[0].strip() or name
    try:
        response = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": locality, "count": 10, "language": "en", "format": "json"},
            timeout=20,
        )
        response.raise_for_status()
        entries = [entry for entry in response.json().get("results", []) if isinstance(entry, dict)]
    except (AttributeError, TypeError, ValueError, requests.RequestException) as exc:
        logger.debug("Unable to enrich geocode identity for %s: %s", name, exc)
        entries = []

    if result.country_code:
        same_country = [
            entry
            for entry in entries
            if str(entry.get("country_code") or "").upper() == result.country_code.upper()
        ]
        if same_country:
            entries = same_country
    candidates = [
        entry
        for entry in entries
        if isinstance(entry.get("latitude"), (int, float))
        and isinstance(entry.get("longitude"), (int, float))
    ]
    if candidates:
        entry = min(
            candidates,
            key=lambda item: (float(item["latitude"]) - result.latitude) ** 2
            + (float(item["longitude"]) - result.longitude) ** 2,
        )
        distance_squared = (float(entry["latitude"]) - result.latitude) ** 2 + (
            float(entry["longitude"]) - result.longitude
        ) ** 2
        if distance_squared <= 4.0:
            return replace(
                result,
                country_name=str(entry.get("country") or "").strip() or _country_from_name(name),
                admin1=str(entry.get("admin1") or "").strip() or None,
                admin2=str(entry.get("admin2") or "").strip() or None,
            )
    return replace(result, country_name=result.country_name or _country_from_name(name))


def _country_from_name(name: str) -> Optional[str]:
    """Use the final comma-delimited component as a last-resort country label."""
    parts = [part.strip() for part in name.split(",") if part.strip()]
    return parts[-1] if len(parts) > 1 else None


def _read_cache() -> dict:
    """Load the geocode search cache from disk."""
    if not CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Invalid geocode cache %s (%s). Deleting.", CACHE_PATH, exc)
        _delete_cache_file()
        return {}
    if not _is_valid_cache_payload(data):
        logger.warning("Invalid geocode cache %s (schema mismatch). Deleting.", CACHE_PATH)
        _delete_cache_file()
        return {}
    return data


def _write_cache(data: dict) -> None:
    """Persist the geocode search cache to disk."""
    try:
        write_text_file(CACHE_PATH, json.dumps(data, indent=2), lock=False)
    except OSError:
        logger.debug("Failed to update geocode cache")


def _google_geocode(address: str, api_key: str) -> Optional[GeocodeResult]:
    """Fallback to Google Geocoding (and Elevation) when Open-Meteo has no result."""
    try:
        logger.info("Fallback geocoding '%s' via Google", address)
        encoded_address = requests.utils.quote(address)
        url = f"https://maps.googleapis.com/maps/api/geocode/json?address={encoded_address}&key={api_key}"
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "OK" or not data.get("results"):
            logger.warning("Google Geocoding returned %s for '%s'", data.get("status"), address)
            return None
        result_entry = data["results"][0]
        lat = result_entry["geometry"]["location"]["lat"]
        lon = result_entry["geometry"]["location"]["lng"]
        formatted = result_entry.get("formatted_address", address)
        timezone = _tz_finder.timezone_at(lat=lat, lng=lon) or "UTC"

        elevation_url = f"https://maps.googleapis.com/maps/api/elevation/json?locations={lat},{lon}&key={api_key}"
        elevation_resp = requests.get(elevation_url, timeout=10)
        elevation_resp.raise_for_status()
        elevation_payload = elevation_resp.json()
        altitude = None
        if elevation_payload.get("status") == "OK" and elevation_payload.get("results"):
            altitude = elevation_payload["results"][0].get("elevation")
        else:
            logger.debug("Google Elevation returned %s for '%s'", elevation_payload.get("status"), address)

        logger.info(
            "Geocode resolved via Google for '%s' (lat=%.4f, lon=%.4f)",
            address,
            lat,
            lon,
        )
        return GeocodeResult(
            name=formatted,
            latitude=lat,
            longitude=lon,
            timezone=timezone,
            country_code=_extract_country_code(result_entry),
            country_name=_extract_address_component(result_entry, "country"),
            admin1=_extract_address_component(result_entry, "administrative_area_level_1"),
            admin2=_extract_address_component(result_entry, "administrative_area_level_2"),
            altitude=altitude,
        )
    except requests.RequestException as exc:
        logger.error("Google geocoding request failed for %s: %s", address, format_request_exception(exc))
        return None
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        logger.error("Unexpected Google geocoding failure for %s: %s", address, exc, exc_info=True)
        return None


def _extract_country_code(result_entry: dict) -> Optional[str]:
    """Extract the ISO country code from a Google geocode result."""
    for component in result_entry.get("address_components", []):
        if "country" in component.get("types", []):
            return component.get("short_name")
    return None


def _extract_address_component(result_entry: dict, component_type: str) -> Optional[str]:
    """Extract a long-form Google address component by type."""
    for component in result_entry.get("address_components", []):
        if component_type in component.get("types", []):
            return component.get("long_name")
    return None


def _is_valid_cache_payload(payload: object) -> bool:
    """Return True if the geocode cache payload structure is valid."""
    if not isinstance(payload, dict):
        return False
    for key, value in payload.items():
        if not isinstance(key, str):
            return False
        if not _is_valid_cache_entry(value):
            return False
    return True


def _is_valid_cache_entry(entry: object) -> bool:
    """Return True if a cached geocode entry contains the required fields."""
    if not isinstance(entry, dict):
        return False
    required = {"name", "latitude", "longitude", "timezone"}
    if not required.issubset(entry.keys()):
        return False
    if not isinstance(entry.get("name"), str):
        return False
    if not isinstance(entry.get("latitude"), (int, float)):
        return False
    if not isinstance(entry.get("longitude"), (int, float)):
        return False
    if not isinstance(entry.get("timezone"), str):
        return False
    country = entry.get("country_code")
    if country is not None and not isinstance(country, str):
        return False
    for field in ("country_name", "admin1", "admin2"):
        value = entry.get(field)
        if value is not None and not isinstance(value, str):
            return False
    altitude = entry.get("altitude")
    if altitude is not None and not isinstance(altitude, (int, float)):
        return False
    return True


def _delete_cache_file() -> None:
    """Delete the geocode cache file if present."""
    safe_unlink(CACHE_PATH, base_dir=CACHE_PATH.parent)
