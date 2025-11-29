"""
Alert aggregation for different providers (OpenWeather, NWS, etc.).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import List, Optional

import requests

from ..config import Secrets, get_secrets
from ..util import ensure_directory

logger = logging.getLogger(__name__)

COUNTRY_CACHE_PATH = ensure_directory("ibf_cache/geocode") / "country_cache.json"
COUNTRY_CACHE_LOCK = Lock()


@dataclass
class AlertSummary:
    title: str
    description: str
    severity: Optional[str] = None
    source: Optional[str] = None
    onset: Optional[str] = None
    expires: Optional[str] = None


def fetch_alerts(latitude: float, longitude: float, *, country_code: Optional[str] = None, secrets: Optional[Secrets] = None) -> List[AlertSummary]:
    secrets = secrets or get_secrets()
    country = (country_code or _resolve_country_code(latitude, longitude, secrets)) or ""
    country = country.upper()

    if country == "US":
        return _fetch_us_alerts(latitude, longitude)
    if country == "CA":
        logger.info("Canadian alerts falling back to OpenWeatherMap.")
    if country == "NZ":
        logger.info("NZ alerts not yet implemented; returning OpenWeatherMap results.")

    return _fetch_openweather_alerts(latitude, longitude, secrets)


def _fetch_us_alerts(latitude: float, longitude: float) -> List[AlertSummary]:
    url = f"https://api.weather.gov/alerts/active?point={latitude},{longitude}"
    try:
        resp = requests.get(url, headers={"User-Agent": "ibf-refactor/0.1"}, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        logger.warning("NWS alerts API failed: %s", exc)
        return []

    summaries: List[AlertSummary] = []
    for feature in payload.get("features", []):
        props = feature.get("properties", {})
        summaries.append(
            AlertSummary(
                title=props.get("event") or "NWS Alert",
                description=props.get("description") or props.get("headline") or "",
                severity=props.get("severity"),
                source="National Weather Service",
                onset=props.get("onset"),
                expires=props.get("ends") or props.get("expires"),
            )
        )
    return summaries


def _fetch_openweather_alerts(latitude: float, longitude: float, secrets: Secrets) -> List[AlertSummary]:
    if not secrets.openweathermap_api_key:
        logger.debug("OPENWEATHERMAP_API_KEY not configured; skipping alerts.")
        return []

    params = {
        "lat": latitude,
        "lon": longitude,
        "exclude": "current,minutely,hourly,daily",
        "appid": secrets.openweathermap_api_key,
    }
    try:
        resp = requests.get("https://api.openweathermap.org/data/3.0/onecall", params=params, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("OpenWeather alerts request failed: %s", exc)
        return []

    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        logger.warning("OpenWeather alerts returned invalid JSON: %s", exc)
        return []

    summaries: List[AlertSummary] = []
    for alert in data.get("alerts", []):
        onset = alert.get("start")
        expires = alert.get("end")
        summaries.append(
            AlertSummary(
                title=alert.get("event", "Weather Alert"),
                description=alert.get("description", ""),
                severity=alert.get("severity"),
                source=alert.get("sender_name"),
                onset=_unix_to_iso(onset) if isinstance(onset, (int, float)) else None,
                expires=_unix_to_iso(expires) if isinstance(expires, (int, float)) else None,
            )
        )
    return summaries


def _resolve_country_code(latitude: float, longitude: float, secrets: Secrets) -> Optional[str]:
    if not secrets.google_api_key:
        return None

    cache_key = f"{latitude:.4f},{longitude:.4f}"
    cached = _read_country_cache().get(cache_key)
    if cached:
        return cached

    params = {"latlng": f"{latitude},{longitude}", "key": secrets.google_api_key}
    try:
        resp = requests.get("https://maps.googleapis.com/maps/api/geocode/json", params=params, timeout=20)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        for component in results[0].get("address_components", []):
            if "country" in component.get("types", []):
                code = component.get("short_name")
                if code:
                    _write_country_cache(cache_key, code)
                    return code
    except requests.RequestException as exc:
        logger.debug("Google reverse geocode failed: %s", exc)
    except (IndexError, KeyError, json.JSONDecodeError):
        logger.debug("Unexpected Google geocode response structure.")

    return None


def _read_country_cache() -> dict:
    with COUNTRY_CACHE_LOCK:
        if not COUNTRY_CACHE_PATH.exists():
            return {}
        try:
            return json.loads(COUNTRY_CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}


def _write_country_cache(key: str, code: str) -> None:
    with COUNTRY_CACHE_LOCK:
        data = _read_country_cache()
        data[key] = code
        try:
            COUNTRY_CACHE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.debug("Failed to update country cache: %s", exc)


def _unix_to_iso(value: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()

