"""
Pipeline executor ties together API clients, processing, and rendering.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Protocol
from zoneinfo import ZoneInfo

from ..config import ForecastConfig, LocationConfig, AreaConfig
from ..api import (
    fetch_alerts,
    fetch_forecast,
    fetch_impact_context,
    geocode_name,
    ForecastRequest,
    AlertSummary,
    GeocodeResult,
)
from ..render import ForecastPage, render_forecast_page
from ..util import slugify, write_text_file, ensure_directory
from .dataset import build_processed_days
from ..llm import (
    LLMSettings,
    resolve_llm_settings,
    generate_forecast_text,
    format_location_dataset,
    format_area_dataset,
    determine_current_season,
    build_spot_system_prompt,
    build_spot_user_prompt,
    build_area_system_prompt,
    build_area_user_prompt,
    build_regional_system_prompt,
    build_regional_user_prompt,
    build_translation_system_prompt,
    build_translation_user_prompt,
)
from ..llm.prompts import UnitInstructions

logger = logging.getLogger(__name__)
DATASET_CACHE_DIR = ensure_directory("ibf_cache/processed")


class SupportsUnits(Protocol):
    """Protocol for objects that have a 'units' dictionary attribute."""
    units: Dict[str, str]


@dataclass
class LocationUnits:
    """
    Resolved unit preferences for a location.

    Attributes:
        temperature_primary: Primary temp unit (e.g., "celsius").
        temperature_secondary: Optional secondary temp unit.
        precipitation_primary: Primary precip unit.
        precipitation_secondary: Optional secondary precip unit.
        snowfall_primary: Primary snowfall unit.
        snowfall_secondary: Optional secondary snowfall unit.
        windspeed_primary: Primary wind unit.
        windspeed_secondary: Optional secondary wind unit.
        altitude_m: Location altitude in meters.
    """
    temperature_primary: str
    temperature_secondary: Optional[str]
    precipitation_primary: str
    precipitation_secondary: Optional[str]
    snowfall_primary: str
    snowfall_secondary: Optional[str]
    windspeed_primary: str
    windspeed_secondary: Optional[str]
    altitude_m: float


@dataclass
class LocationForecastPayload:
    """
    Intermediate data container for a location's forecast.

    Attributes:
        name: Location name.
        geocode: Resolved geocoding data.
        alerts: List of active alerts.
        dataset: Processed forecast data (days/hours).
        dataset_cache: Path to the cached dataset file.
        units: Resolved unit settings.
        formatted_dataset: Text representation of the dataset for LLM consumption.
    """
    name: str
    geocode: GeocodeResult
    alerts: List[AlertSummary]
    dataset: List[dict]
    dataset_cache: Path
    units: LocationUnits
    formatted_dataset: str


def execute_pipeline(config: ForecastConfig) -> None:
    """
    Run the full forecast generation pipeline based on the configuration.

    Iterates through all configured locations and areas, fetching data, generating
    forecasts via LLM (or fallback), translating if needed, and rendering HTML pages.

    Args:
        config: The loaded ForecastConfig object.
    """
    if not config.locations and not config.areas:
        logger.info("No locations or areas configured; nothing to do.")
        return

    for location in config.locations:
        _process_location(location, config)
    for area in config.areas:
        if getattr(area, "mode", "area") == "regional":
            _process_regional_area(area, config)
        else:
            _process_area(area, config)


def _process_location(location: LocationConfig, config: ForecastConfig) -> Optional[LocationForecastPayload]:
    """Drive the full fetch/LLM/render workflow for a single configured location."""
    name = location.name
    logger.info("Processing location '%s'", name)
    units = _resolve_units(location)
    forecast_days = config.location_forecast_days or 4
    payload = _collect_location_payload(
        name,
        config=config,
        units=units,
        thin_select=int(config.location_thin_select or 16),
        forecast_days=forecast_days,
    )
    if not payload:
        return None
    geocode = payload.geocode
    timezone_name = geocode.timezone or "UTC"
    ibf_context = fetch_impact_context(
        name,
        context_type="location",
        forecast_days=forecast_days,
        timezone_name=timezone_name,
    ).content
    logger.info("Fetched impact context for '%s'", name)
    formatted_dataset = payload.formatted_dataset
    dataset = payload.dataset
    alerts = payload.alerts
    dataset_path = payload.dataset_cache

    llm_settings = None
    forecast_text: Optional[str] = None
    if formatted_dataset and not formatted_dataset.startswith("Error"):
        try:
            llm_settings = resolve_llm_settings(config)
            system_prompt = build_spot_system_prompt(_unit_instructions(payload.units))
            short_instr = _short_period_instruction(dataset, geocode.timezone or "UTC")
            impact_instr = _impact_instruction(_as_bool(config.location_impact_based))
            prompt = build_spot_user_prompt(
                formatted_dataset,
                location_name=name,
                latitude=geocode.latitude,
                longitude=geocode.longitude,
                season=determine_current_season(geocode.latitude),
                wordiness=(config.location_wordiness or "normal").lower(),
                short_period_instruction=short_instr,
                impact_instruction=impact_instr if ibf_context else "",
                impact_context=ibf_context or "",
            )
            logger.info("Requesting LLM forecast for '%s' using model %s", name, llm_settings.model)
            forecast_text = generate_forecast_text(prompt, system_prompt, llm_settings)
        except Exception as exc:
            logger.error("LLM generation failed for %s: %s", name, exc, exc_info=True)

    if not forecast_text:
        logger.info("LLM unavailable for '%s'; using dataset summary fallback", name)
        forecast_text = _dataset_summary(dataset, alerts, dataset_path)

    translation_target = _location_translation_language(location, config)
    translated_text = _maybe_translate(
        forecast_text,
        translation_target,
        config,
        llm_settings,
    )

    destination = _build_destination_path(config, name)
    logger.info("Writing forecast page for '%s' → %s", name, destination)
    render_forecast_page(
        ForecastPage(
            destination=destination,
            display_name=name,
            issue_time=_format_issue_time(timezone_name),
            forecast_text=forecast_text,
            translated_text=translated_text,
            translation_language=translation_target,
            ibf_context=ibf_context,
        )
    )
    logger.info("Rendered forecast page for '%s' → %s", name, destination)
    return payload


def _process_area(area: AreaConfig, config: ForecastConfig) -> None:
    """Generate an area-level forecast (single text block) across representative spots."""
    logger.info("Processing area '%s'", area.name)
    base_units = _resolve_units(area)
    thin_select = int(config.area_thin_select or config.location_thin_select or 16)
    forecast_days = config.area_forecast_days or config.location_forecast_days or 4

    payloads = _collect_area_payloads(area, config, base_units, thin_select, forecast_days)

    if not payloads:
        logger.warning("Area '%s' has no valid location data; skipping.", area.name)
        return

    area_timezone = payloads[0].geocode.timezone or "UTC"
    ibf_context = fetch_impact_context(
        area.name,
        context_type="area",
        forecast_days=forecast_days,
        timezone_name=area_timezone,
    ).content
    logger.info("Fetched impact context for area '%s'", area.name)
    formatted_dataset = format_area_dataset(
        area.name,
        [
            {
                "name": payload.name,
                "latitude": payload.geocode.latitude,
                "longitude": payload.geocode.longitude,
                "timezone": payload.geocode.timezone,
                "text": payload.formatted_dataset,
            }
            for payload in payloads
        ],
    )

    llm_settings = None
    forecast_text: Optional[str] = None
    if formatted_dataset:
        try:
            llm_settings = resolve_llm_settings(config)
            system_prompt = build_area_system_prompt(_unit_instructions(base_units))
            short_instr = _short_period_instruction(
                payloads[0].dataset, payloads[0].geocode.timezone or "UTC"
            )
            impact_instr = _impact_instruction(_as_bool(config.area_impact_based))
            prompt = build_area_user_prompt(
                formatted_dataset,
                area_name=area.name,
                location_names=[payload.name for payload in payloads],
                wordiness=(config.area_wordiness or config.location_wordiness or "normal").lower(),
                short_period_instruction=short_instr,
                impact_instruction=impact_instr if ibf_context else "",
                impact_context=ibf_context or "",
            )
            forecast_text = generate_forecast_text(prompt, system_prompt, llm_settings)
            logger.info("Requesting area LLM forecast for '%s' using model %s", area.name, llm_settings.model)
        except Exception as exc:
            logger.error("LLM generation failed for area %s: %s", area.name, exc, exc_info=True)

    if not forecast_text:
        logger.info("Area '%s' fell back to dataset summary", area.name)
        forecast_text = _area_dataset_summary(area.name, payloads)

    translation_target = _area_translation_language(area, config)
    translated_text = _maybe_translate(
        forecast_text,
        translation_target,
        config,
        llm_settings,
    )

    destination = _build_destination_path(config, area.name)
    logger.info("Writing area forecast page for '%s' → %s", area.name, destination)
    map_link = _map_link_for(config, area.name)

    render_forecast_page(
        ForecastPage(
            destination=destination,
            display_name=area.name,
            issue_time=_format_issue_time(area_timezone),
            forecast_text=forecast_text,
            translated_text=translated_text,
            translation_language=translation_target,
            ibf_context=ibf_context,
            map_link=map_link,
        )
    )
    logger.info("Rendered area forecast page for '%s' → %s", area.name, destination)


def _process_regional_area(area: AreaConfig, config: ForecastConfig) -> None:
    """Produce a regional forecast that is broken down by sub-regions."""
    logger.info("Processing regional area '%s'", area.name)
    base_units = _resolve_units(area)
    thin_select = int(config.area_thin_select or config.location_thin_select or 16)
    forecast_days = config.area_forecast_days or config.location_forecast_days or 4

    payloads = _collect_area_payloads(area, config, base_units, thin_select, forecast_days)
    if not payloads:
        logger.warning("Regional area '%s' has no valid location data; skipping.", area.name)
        return

    area_timezone = payloads[0].geocode.timezone or "UTC"
    ibf_context = fetch_impact_context(
        area.name,
        context_type="regional",
        forecast_days=forecast_days,
        timezone_name=area_timezone,
    ).content
    logger.info("Fetched impact context for regional area '%s'", area.name)
    formatted_dataset = format_area_dataset(
        area.name,
        [
            {
                "name": payload.name,
                "latitude": payload.geocode.latitude,
                "longitude": payload.geocode.longitude,
                "timezone": payload.geocode.timezone,
                "text": payload.formatted_dataset,
            }
            for payload in payloads
        ],
    )

    llm_settings = None
    forecast_text: Optional[str] = None
    if formatted_dataset:
        try:
            llm_settings = resolve_llm_settings(config)
            system_prompt = build_regional_system_prompt(_unit_instructions(base_units))
            short_instr = _short_period_instruction(
                payloads[0].dataset, payloads[0].geocode.timezone or "UTC"
            )
            impact_instr = _impact_instruction(_as_bool(config.area_impact_based))
            prompt = build_regional_user_prompt(
                formatted_dataset,
                area_name=area.name,
                location_names=[payload.name for payload in payloads],
                wordiness=(config.area_wordiness or config.location_wordiness or "normal").lower(),
                short_period_instruction=short_instr,
                impact_instruction=impact_instr if ibf_context else "",
                impact_context=ibf_context or "",
            )
            forecast_text = generate_forecast_text(prompt, system_prompt, llm_settings)
            logger.info("Requesting regional LLM forecast for '%s' using model %s", area.name, llm_settings.model)
        except Exception as exc:
            logger.error(
                "Regional LLM generation failed for %s: %s", area.name, exc, exc_info=True
            )

    if not forecast_text:
        logger.info("Regional area '%s' fell back to dataset summary", area.name)
        forecast_text = _area_dataset_summary(area.name, payloads)

    translation_target = _area_translation_language(area, config)
    translated_text = _maybe_translate(
        forecast_text,
        translation_target,
        config,
        llm_settings,
    )

    destination = _build_destination_path(config, area.name)
    logger.info("Writing regional forecast page for '%s' → %s", area.name, destination)
    map_link = _map_link_for(config, area.name)

    render_forecast_page(
        ForecastPage(
            destination=destination,
            display_name=area.name,
            issue_time=_format_issue_time(area_timezone),
            forecast_text=forecast_text,
            translated_text=translated_text,
            translation_language=translation_target,
            ibf_context=ibf_context,
            map_link=map_link,
        )
    )
    logger.info("Rendered regional forecast page for '%s' → %s", area.name, destination)


def _collect_location_payload(
    name: str,
    *,
    config: ForecastConfig,
    units: LocationUnits,
    thin_select: int,
    forecast_days: int,
    cache_label: Optional[str] = None,
) -> Optional[LocationForecastPayload]:
    """Gather geocode, alerts, forecast, processed dataset, and formatted text."""
    logger.info("Geocoding '%s'", name)
    geocode = geocode_name(name)
    if not geocode:
        logger.warning("Unable to geocode '%s'; skipping.", name)
        return None

    logger.info("Fetching alerts for '%s'", name)
    alerts = fetch_alerts(
        geocode.latitude,
        geocode.longitude,
        country_code=geocode.country_code,
    )

    try:
        logger.info("Fetching forecast data for '%s' (%s days)", name, forecast_days)
        forecast = fetch_forecast(
            ForecastRequest(
                latitude=geocode.latitude,
                longitude=geocode.longitude,
                timezone=geocode.timezone,
                forecast_days=forecast_days,
                temperature_unit=_temperature_unit_for_api(units.temperature_primary),
                precipitation_unit=_precipitation_unit_for_api(units.precipitation_primary),
                windspeed_unit=_windspeed_unit_for_api(units.windspeed_primary),
            )
        )
    except RuntimeError as exc:
        logger.error("Failed to fetch forecast for %s: %s", name, exc)
        return None

    dataset = build_processed_days(
        forecast.raw,
        timezone_name=geocode.timezone or "UTC",
        precipitation_unit=units.precipitation_primary,
        windspeed_unit=units.windspeed_primary,
        thin_select=thin_select,
        location_altitude=units.altitude_m,
    )
    if not dataset:
        logger.warning("No processed data produced for '%s'; skipping.", name)
        return None

    dataset_path = _write_dataset_cache(cache_label or name, dataset)
    logger.info("Processed %d day(s) for '%s'; dataset cached at %s", len(dataset), name, dataset_path)
    formatted_dataset = format_location_dataset(
        dataset,
        alerts,
        geocode.timezone or "UTC",
        temperature_unit=units.temperature_primary,
        precipitation_unit=units.precipitation_primary,
        snowfall_unit=units.snowfall_primary,
        windspeed_unit=units.windspeed_primary,
    )

    return LocationForecastPayload(
        name=name,
        geocode=geocode,
        alerts=alerts,
        dataset=dataset,
        dataset_cache=dataset_path,
        units=units,
        formatted_dataset=formatted_dataset,
    )


def _collect_area_payloads(
    area: AreaConfig,
    config: ForecastConfig,
    base_units: LocationUnits,
    thin_select: int,
    forecast_days: int,
) -> List[LocationForecastPayload]:
    """Fetch datasets for each representative location needed for an area."""
    payloads: List[LocationForecastPayload] = []
    for location_name in area.locations:
        logger.info("Collecting data for representative location '%s' in area '%s'", location_name, area.name)
        location_units = _find_location_units(config, location_name)
        units_for_location = (
            replace(base_units, altitude_m=location_units.altitude_m)
            if location_units
            else base_units
        )
        payload = _collect_location_payload(
            location_name,
            config=config,
            units=units_for_location,
            thin_select=thin_select,
            forecast_days=forecast_days,
            cache_label=f"{area.name}__{location_name}",
        )
        if payload:
            payloads.append(payload)
    return payloads


def _resolve_units(config_obj: SupportsUnits) -> LocationUnits:
    """Normalize/merge explicit unit overrides with defaults for a config entry."""
    units = getattr(config_obj, "units", {}) or {}

    def _split(value: Optional[str], default: str) -> tuple[str, Optional[str]]:
        if not value:
            return default, None
        if "(" in value and value.endswith(")"):
            primary, secondary = value.split("(", 1)
            return primary.strip(), secondary[:-1].strip() or None
        return value.strip(), None

    altitude_raw = units.get("altitude_m")
    try:
        altitude_val = float(altitude_raw)
    except (TypeError, ValueError):
        altitude_val = 0.0

    temp_primary, temp_secondary = _split(units.get("temperature_unit"), "celsius")
    precip_primary, precip_secondary = _split(units.get("precipitation_unit"), "mm")
    snowfall_raw = units.get("snowfall_unit")
    snow_primary, snow_secondary = _split(snowfall_raw, "cm")
    wind_primary, wind_secondary = _split(units.get("windspeed_unit"), "kph")

    temp_primary = temp_primary.lower()
    temp_secondary = temp_secondary.lower() if temp_secondary else None
    precip_primary = precip_primary.lower()
    precip_secondary = precip_secondary.lower() if precip_secondary else None
    snow_primary = snow_primary.lower()
    snow_secondary = snow_secondary.lower() if snow_secondary else None
    wind_primary = wind_primary.lower()
    wind_secondary = wind_secondary.lower() if wind_secondary else None

    if not snowfall_raw and precip_primary in {"inch", "in", "inches"}:
        snow_primary = "inch"

    return LocationUnits(
        temperature_primary=temp_primary,
        temperature_secondary=temp_secondary,
        precipitation_primary=precip_primary,
        precipitation_secondary=precip_secondary,
        snowfall_primary=snow_primary,
        snowfall_secondary=snow_secondary,
        windspeed_primary=wind_primary,
        windspeed_secondary=wind_secondary,
        altitude_m=altitude_val,
    )


def _find_location_units(config: ForecastConfig, name: str) -> Optional[LocationUnits]:
    """Look up a location's specific unit overrides by name."""
    target = name.strip().lower()
    for entry in config.locations:
        if entry.name.strip().lower() == target:
            return _resolve_units(entry)
    return None


def _unit_instructions(units: LocationUnits) -> UnitInstructions:
    """Convert `LocationUnits` into `UnitInstructions` for the prompts module."""
    return UnitInstructions(
        temperature_primary=units.temperature_primary,
        temperature_secondary=units.temperature_secondary,
        precipitation_primary=units.precipitation_primary,
        precipitation_secondary=units.precipitation_secondary,
        snowfall_primary=units.snowfall_primary,
        snowfall_secondary=units.snowfall_secondary,
        windspeed_primary=units.windspeed_primary,
        windspeed_secondary=units.windspeed_secondary,
    )


def _short_period_instruction(dataset: List[dict], tz_str: str) -> str:
    """Optional reminder when the first period only covers the final moments of a day."""
    if not dataset:
        return ""
    label = dataset[0].get("dayofweek", "")
    label_upper = label.upper()
    if not any(key in label_upper for key in ["REST OF", "THIS EVENING"]):
        return ""
    try:
        tz = ZoneInfo(tz_str)
    except Exception:
        tz = ZoneInfo("UTC")
    if datetime.now(tz).hour >= 22:
        return (
            "CRITICAL: The first forecast period covers only the last 1-2 hours of the day. "
            "Be extremely brief (1-2 sentences) and focus only on immediate conditions."
        )
    return ""


def _impact_instruction(enabled: bool) -> str:
    """Return the impact-forecast instruction block when impact context is enabled."""
    if not enabled:
        return ""
    return (
        "This is an impact-based forecast. Use any additional context to explain vulnerabilities, "
        "upcoming events, or thresholds only when the forecast meets or exceeds them. "
        "If conditions stay below thresholds, omit references to those impacts."
    )


def _as_bool(value: Optional[bool | str]) -> bool:
    """Coerce truthy string/configuration representations into a boolean."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"yes", "true", "1", "on"}
    return False


def _write_dataset_cache(name: str, dataset: List[dict]) -> Path:
    """Persist the processed dataset into the cache directory and return the path."""
    slug = slugify(name)
    path = DATASET_CACHE_DIR / f"{slug}.json"
    write_text_file(path, json.dumps(dataset, indent=2))
    return path


def _dataset_summary(dataset: List[dict], alerts, dataset_path: Path) -> str:
    """Provide a terse textual fallback when the LLM output is unavailable."""
    temps = []
    precip = []
    time_labels = []

    for day in dataset:
        for hour in day.get("hours", []):
            member = hour.get("ensemble_members", {}).get("member00")
            if not member:
                continue
            if member.get("temperature") is not None:
                temps.append(member["temperature"])
            if member.get("precipitation") is not None:
                precip.append(member["precipitation"])
            time_labels.append(f"{day['date']} {hour['hour']}")

    lines = ["**Dataset preview**"]
    if temps:
        lines.append(f"- Core member temps: {min(temps):.1f} – {max(temps):.1f}")
    if precip:
        lines.append(f"- Max precip: {max(precip):.1f}")
    lines.append(f"- Hours captured: {len(time_labels)}")

    lines.append("\n**Alerts**")
    if alerts:
        for alert in alerts[:3]:
            lines.append(f"- {alert.source or 'Alert'}: {alert.title}")
    else:
        lines.append("- No active alerts at fetch time.")

    lines.append(f"\n`Dataset cache:` {dataset_path}")
    return "\n".join(lines)


def _area_dataset_summary(area_name: str, payloads: List[LocationForecastPayload]) -> str:
    """Fallback text listing dataset caches for each area location."""
    lines = [f"**Area dataset preview for {area_name}**"]
    for payload in payloads:
        lines.append(f"- {payload.name}: {payload.dataset_cache}")
    return "\n".join(lines)


def _build_destination_path(config: ForecastConfig, name: str) -> Path:
    """Resolve the filesystem path for the rendered HTML for a given name."""
    from ..web.scaffold import resolve_web_root

    root = resolve_web_root(config)
    folder = root / slugify(name)
    return folder / "index.html"


def _map_link_for(config: ForecastConfig, name: str) -> Optional[str]:
    """Return a relative link to a generated map (PNG/HTML) if one exists."""
    from ..web.scaffold import resolve_web_root

    root = resolve_web_root(config)
    maps_dir = root / "maps"
    slug = slugify(name)
    png = maps_dir / f"{slug}.png"
    if png.exists():
        return f"../maps/{png.name}"
    html = maps_dir / f"{slug}.html"
    if html.exists():
        return f"../maps/{html.name}"
    return None


def _location_translation_language(location: LocationConfig, config: ForecastConfig) -> Optional[str]:
    """Determine which language (if any) a location forecast should be translated into."""
    return (
        location.translation_language
        or location.lang
        or config.translation_language
    )


def _area_translation_language(area: AreaConfig, config: ForecastConfig) -> Optional[str]:
    """Determine the translation language for an area-level forecast."""
    return (
        area.translation_language
        or area.lang
        or config.translation_language
    )


def _maybe_translate(
    text: str,
    language: Optional[str],
    config: ForecastConfig,
    llm_settings: Optional[LLMSettings],
) -> Optional[str]:
    """Translate finished forecast text when a non-English target language is requested."""
    if not language:
        return None
    if language.lower().startswith("en"):
        return None
    if not text:
        return None
    try:
        chosen_model = config.translation_llm
        if chosen_model is None:
            settings = llm_settings or resolve_llm_settings(config)
            model_name = settings.model
        else:
            settings = resolve_llm_settings(config, chosen_model)
            model_name = chosen_model
        logger.info("Translating forecast into %s using %s", language, model_name)
        system_prompt = build_translation_system_prompt(language)
        user_prompt = build_translation_user_prompt(text)
        return generate_forecast_text(user_prompt, system_prompt, settings)
    except Exception as exc:
        logger.error("Translation failed (%s): %s", language, exc, exc_info=True)
        return None


def _format_issue_time(tz_name: Optional[str]) -> str:
    """Format the issue timestamp in the provided timezone."""
    try:
        zone = ZoneInfo(tz_name) if tz_name else ZoneInfo("UTC")
    except Exception:
        zone = ZoneInfo("UTC")
    return datetime.now(zone).strftime("%Y-%m-%d %H:%M %Z")


def _temperature_unit_for_api(value: str) -> str:
    """Map configuration temperature units to Open-Meteo API values."""
    normalized = (value or "celsius").strip().lower()
    return "fahrenheit" if normalized in {"f", "fahrenheit"} else "celsius"


def _precipitation_unit_for_api(value: str) -> str:
    """Map configuration precipitation units to Open-Meteo API values."""
    normalized = (value or "mm").strip().lower()
    return "inch" if normalized in {"in", "inch", "inches"} else "mm"


def _windspeed_unit_for_api(value: str) -> str:
    """Map configuration wind units to Open-Meteo API values."""
    normalized = (value or "kph").strip().lower()
    if normalized in {"kmh", "km/h", "kph"}:
        return "kph"
    if normalized in {"mph"}:
        return "mph"
    if normalized in {"mps", "ms"}:
        return "mps"
    if normalized in {"kt", "knots", "kts", "kn"}:
        return "kt"
    return "kph"
