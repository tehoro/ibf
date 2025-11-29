"""
Name-to-coordinate geocoding via the Open-Meteo API with caching.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from timezonefinder import TimezoneFinder

from ..util import ensure_directory
from ..config.settings import get_secrets

logger = logging.getLogger(__name__)

CACHE_PATH = ensure_directory("ibf_cache/geocode") / "search_cache.json"
_tz_finder = TimezoneFinder()


@dataclass
class GeocodeResult:
    name: str
    latitude: float
    longitude: float
    timezone: str
    country_code: Optional[str] = None
    altitude: Optional[float] = None


def geocode_name(name: str, *, language: str = "en") -> Optional[GeocodeResult]:
    """
    Resolve a place name into coordinates.
    """
    normalized = name.strip().lower()
    cache = _read_cache()
    if normalized in cache:
        data = cache[normalized]
        return GeocodeResult(**data)

    params = {"name": name, "count": 1, "language": language, "format": "json"}
    try:
        resp = requests.get("https://geocoding-api.open-meteo.com/v1/search", params=params, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Open-Meteo geocoding failed for %s: %s", name, exc)
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
            )

    if result is None:
        result = _google_geocode(name)
        if result is None:
            return None

    cache[normalized] = {
        "name": result.name,
        "latitude": result.latitude,
        "longitude": result.longitude,
        "timezone": result.timezone,
        "country_code": result.country_code,
        "altitude": result.altitude,
    }
    _write_cache(cache)
    return result


def _read_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_cache(data: dict) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        logger.debug("Failed to update geocode cache")


def _google_geocode(address: str) -> Optional[GeocodeResult]:
    secrets = get_secrets()
    api_key = secrets.google_api_key
    if not api_key:
        logger.debug("GOOGLE_API_KEY not set; skipping Google fallback for %s", address)
        return None

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

        return GeocodeResult(
            name=formatted,
            latitude=lat,
            longitude=lon,
            timezone=timezone,
            country_code=_extract_country_code(result_entry),
            altitude=altitude,
        )
    except requests.RequestException as exc:
        logger.error("Google geocoding request failed for %s: %s", address, exc)
        return None
    except Exception as exc:
        logger.error("Unexpected Google geocoding failure for %s: %s", address, exc, exc_info=True)
        return None


def _extract_country_code(result_entry: dict) -> Optional[str]:
    for component in result_entry.get("address_components", []):
        if "country" in component.get("types", []):
            return component.get("short_name")
    return None

