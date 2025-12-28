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
from xml.etree import ElementTree as ET

import feedparser
import requests
from bs4 import BeautifulSoup, FeatureNotFound
from shapely.geometry import Point, Polygon

from ..config import Secrets, get_secrets
from ..util import ensure_directory

logger = logging.getLogger(__name__)

COUNTRY_CACHE_PATH = ensure_directory("ibf_cache/geocode") / "country_cache.json"
COUNTRY_CACHE_LOCK = Lock()


@dataclass
class AlertSummary:
    """
    Represents a normalized weather alert.

    Attributes:
        title: The headline or title of the alert.
        description: Detailed description of the alert.
        severity: Severity level (e.g., "Severe", "Moderate").
        source: The agency or provider issuing the alert.
        onset: ISO 8601 timestamp for when the alert begins.
        expires: ISO 8601 timestamp for when the alert ends.
    """
    title: str
    description: str
    severity: Optional[str] = None
    source: Optional[str] = None
    onset: Optional[str] = None
    expires: Optional[str] = None


def fetch_alerts(latitude: float, longitude: float, *, country_code: Optional[str] = None, secrets: Optional[Secrets] = None) -> List[AlertSummary]:
    """
    Retrieve active weather alerts for a specific coordinate.

    Automatically selects the best provider based on the country code:
    - US: National Weather Service (NWS)
    - Canada: OpenWeatherMap (fallback)
    - New Zealand: MetService CAP feed
    - Others: OpenWeatherMap

    Args:
        latitude: Latitude of the location.
        longitude: Longitude of the location.
        country_code: Optional ISO 3166-1 alpha-2 country code. If omitted, it will be resolved via reverse geocoding.
        secrets: Optional Secrets instance containing API keys.

    Returns:
        A list of AlertSummary objects.
    """
    secrets = secrets or get_secrets()
    country = (country_code or _resolve_country_code(latitude, longitude, secrets)) or ""
    country = country.upper()

    if country == "US":
        return _fetch_us_alerts(latitude, longitude)
    if country == "NZ":
        # MetService feed is authoritative; do not fall back to OpenWeatherMap if it returns none.
        return _fetch_nz_alerts(latitude, longitude)
    if country == "CA":
        logger.info("Canadian alerts falling back to OpenWeatherMap.")

    return _fetch_openweather_alerts(latitude, longitude, secrets)


def _fetch_us_alerts(latitude: float, longitude: float) -> List[AlertSummary]:
    """Fetch alerts from the National Weather Service for the given point."""
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
    """Fetch alert data from OpenWeatherMap's One Call API."""
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


def _fetch_nz_alerts(latitude: float, longitude: float) -> List[AlertSummary]:
    """Fetch alerts from MetService CAP RSS feed for New Zealand."""
    rss_url = "https://alerts.metservice.com/cap/rss"
    try:
        feed = feedparser.parse(rss_url)
    except Exception as exc:  # feedparser can raise generic exceptions
        logger.warning("MetService RSS parse failed: %s", exc)
        return []

    point = Point(longitude, latitude)
    summaries: List[AlertSummary] = []

    for entry in getattr(feed, "entries", []):
        link = getattr(entry, "link", None)
        if not link:
            continue

        try:
            resp = requests.get(link, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.debug("MetService CAP fetch failed for %s: %s", link, exc)
            continue

        # Initialize variables
        polygons = []
        severity = None
        onset = None
        expires = None

        try:
            # Parse XML with ElementTree for better namespace handling
            root = ET.fromstring(resp.content)
            # CAP 1.2 namespace
            ns = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}
            
            # Find all polygon elements using namespace
            polygon_elements = root.findall(".//cap:polygon", ns)
            if not polygon_elements:
                # Fallback: try without namespace (some feeds might not use it)
                polygon_elements = root.findall(".//polygon")
            
            for poly_elem in polygon_elements:
                poly_text = poly_elem.text
                if poly_text:
                    polygon = _cap_polygon_to_shape(poly_text.strip())
                    if polygon:
                        polygons.append(polygon)
            
            if not polygons:
                logger.debug("No valid polygons found in CAP alert %s", link)
                continue
            
            # Parse other fields using ElementTree as well
            info_elem = root.find(".//cap:info", ns) or root.find(".//info")
            if info_elem is not None:
                severity_elem = info_elem.find("cap:severity", ns) or info_elem.find("severity")
                if severity_elem is not None and severity_elem.text:
                    severity = severity_elem.text.strip()
                onset_elem = info_elem.find("cap:onset", ns) or info_elem.find("onset")
                if onset_elem is not None and onset_elem.text:
                    onset = onset_elem.text.strip()
                expires_elem = info_elem.find("cap:expires", ns) or info_elem.find("expires")
                if expires_elem is not None and expires_elem.text:
                    expires = expires_elem.text.strip()
            
            # Fallback to BeautifulSoup for other fields if ElementTree didn't work
            if severity is None or onset is None or expires is None:
                try:
                    soup = BeautifulSoup(resp.content, "xml")
                    if severity is None:
                        severity = _get_xml_text(soup, "severity")
                    if onset is None:
                        onset = _get_xml_text(soup, "onset")
                    if expires is None:
                        expires = _get_xml_text(soup, "expires")
                except FeatureNotFound:
                    pass
        except ET.ParseError as exc:
            logger.warning("Failed to parse CAP XML for %s: %s", link, exc)
            continue
        except Exception as exc:
            logger.debug("Error processing CAP alert %s: %s", link, exc)
            continue

        # Check if point is within any polygon
        if polygons and any(poly.contains(point) or poly.touches(point) for poly in polygons):
            summaries.append(
                AlertSummary(
                    title=getattr(entry, "title", None) or "MetService Alert",
                    description=getattr(entry, "summary", None)
                    or getattr(entry, "description", None)
                    or "",
                    severity=severity,
                    source="MetService",
                    onset=onset,
                    expires=expires,
                )
            )

    return summaries


def _cap_polygon_to_shape(polygon_text: Optional[str]) -> Optional[Polygon]:
    """Convert a CAP polygon string into a Shapely Polygon."""
    if not polygon_text:
        return None

    coords = []
    for pair in polygon_text.strip().split():
        parts = pair.split(",")
        if len(parts) != 2:
            continue
        try:
            lat = float(parts[0])
            lon = float(parts[1])
        except ValueError:
            continue
        coords.append((lon, lat))

    if len(coords) < 3:
        return None

    polygon = Polygon(coords)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    return polygon if polygon.is_valid else None


def _get_xml_text(soup: BeautifulSoup, tag_name: str) -> Optional[str]:
    """Return stripped text for the first matching tag in the CAP XML."""
    tag = soup.find(tag_name)
    if tag and tag.text:
        return tag.text.strip()
    return None


def _resolve_country_code(latitude: float, longitude: float, secrets: Secrets) -> Optional[str]:
    """Reverse geocode the coordinate to an ISO country code with caching."""
    cache_key = f"{latitude:.4f},{longitude:.4f}"
    cached = _read_country_cache().get(cache_key)
    if cached:
        return cached

    if secrets.google_api_key:
        code = _reverse_country_google(latitude, longitude, secrets.google_api_key)
        if code:
            _write_country_cache(cache_key, code)
            return code

    if secrets.openweathermap_api_key:
        code = _reverse_country_openweather(latitude, longitude, secrets.openweathermap_api_key)
        if code:
            _write_country_cache(cache_key, code)
            return code

    return None


def _read_country_cache() -> dict:
    """Load the cached country lookup table from disk."""
    with COUNTRY_CACHE_LOCK:
        if not COUNTRY_CACHE_PATH.exists():
            return {}
        try:
            return json.loads(COUNTRY_CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}


def _write_country_cache(key: str, code: str) -> None:
    """Persist a country code lookup keyed by coordinate."""
    with COUNTRY_CACHE_LOCK:
        data = _read_country_cache()
        data[key] = code
        try:
            COUNTRY_CACHE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.debug("Failed to update country cache: %s", exc)


def _unix_to_iso(value: float) -> str:
    """Convert a Unix timestamp in seconds to an ISO 8601 string."""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _reverse_country_google(latitude: float, longitude: float, api_key: str) -> Optional[str]:
    """Look up a country code using Google reverse geocoding."""
    params = {"latlng": f"{latitude},{longitude}", "key": api_key}
    try:
        resp = requests.get("https://maps.googleapis.com/maps/api/geocode/json", params=params, timeout=20)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return None
        for component in results[0].get("address_components", []):
            if "country" in component.get("types", []):
                return component.get("short_name")
    except requests.RequestException as exc:
        logger.debug("Google reverse geocode failed: %s", exc)
    except (IndexError, KeyError, json.JSONDecodeError):
        logger.debug("Unexpected Google geocode response structure.")
    return None


def _reverse_country_openweather(latitude: float, longitude: float, api_key: str) -> Optional[str]:
    """Look up a country code using OpenWeatherMap reverse geocoding."""
    params = {"lat": latitude, "lon": longitude, "limit": 1, "appid": api_key}
    try:
        resp = requests.get("https://api.openweathermap.org/geo/1.0/reverse", params=params, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        if not payload:
            return None
        entry = payload[0]
        return entry.get("country")
    except requests.RequestException as exc:
        logger.debug("OpenWeatherMap reverse geocode failed: %s", exc)
    except (IndexError, KeyError, json.JSONDecodeError):
        logger.debug("Unexpected OpenWeatherMap geocode response structure.")
    return None

