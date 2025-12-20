"""
Pipeline executor ties together API clients, processing, and rendering.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Protocol, Iterable
from zoneinfo import ZoneInfo
import math
import re

from ..config import ForecastConfig, LocationConfig, AreaConfig
from ..api import (
    fetch_alerts,
    fetch_forecast,
    fetch_impact_context,
    geocode_name,
    ForecastRequest,
    AlertSummary,
    GeocodeResult,
    ENSEMBLE_MODELS,
    DEFAULT_ENSEMBLE_MODEL,
    HOURLY_FIELDS_SNOW_PROFILE,
    PRESSURE_LEVELS_SNOW_HPA,
    ModelSpec,
    resolve_model_spec,
)
from ..render import ForecastPage, render_forecast_page
from ..util import slugify, write_text_file, ensure_directory
from ..util.elevation import get_highest_point
from ..util.snow import should_check_snow_level
from .dataset import build_processed_days
from ..llm import (
    LLMSettings,
    resolve_llm_settings,
    generate_forecast_text,
    consume_last_cost_cents,
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
PROMPT_SNAPSHOT_DIR = ensure_directory("ibf_cache/prompts")

_SNOW_PROFILE_UNSUPPORTED_MODELS: set[str] = set()


@dataclass
class CostBreakdown:
    context_cents: float = 0.0
    forecast_cents: float = 0.0
    translation_cents: float = 0.0


_COST_TRACKER: Dict[str, CostBreakdown] = {}


def _reset_cost_tracker() -> None:
    _COST_TRACKER.clear()


def _record_cost(kind: str, name: str, *, context: float = 0.0, forecast: float = 0.0, translation: float = 0.0) -> None:
    label = f"{kind}: {name}"
    entry = _COST_TRACKER.setdefault(label, CostBreakdown())
    entry.context_cents += context
    entry.forecast_cents += forecast
    entry.translation_cents += translation


def _log_cost_summary() -> None:
    if not _COST_TRACKER:
        logger.info("LLM cost summary – no tracked costs this run.")
        return

    def _clamp_width(value: int, *, min_width: int, max_width: int) -> int:
        return max(min_width, min(max_width, value))

    def _format_label(label: str, width: int) -> str:
        if len(label) <= width:
            return f"{label:<{width}}"
        if width <= 3:
            return label[:width]
        return f"{label[: width - 3]}..."

    # Keep the log readable while ensuring columns align.
    label_header = "Location or Area"
    widest_label = max((len(k) for k in _COST_TRACKER.keys()), default=len(label_header))
    label_width = _clamp_width(max(len(label_header), widest_label), min_width=40, max_width=70)

    header = f"{label_header:<{label_width}} {'Context':>12} {'Forecast':>12} {'Translation':>12}"
    lines = [header, "-" * len(header)]
    total_context = total_forecast = total_translation = 0.0

    for label in sorted(_COST_TRACKER.keys()):
        entry = _COST_TRACKER[label]
        total_context += entry.context_cents
        total_forecast += entry.forecast_cents
        total_translation += entry.translation_cents
        lines.append(
            f"{_format_label(label, label_width)} {entry.context_cents:>12.1f} {entry.forecast_cents:>12.1f} {entry.translation_cents:>12.1f}"
        )

    lines.append("-" * len(header))
    lines.append(
        f"{'TOTAL':<{label_width}} {total_context:>12.1f} {total_forecast:>12.1f} {total_translation:>12.1f}"
    )
    grand_total = total_context + total_forecast + total_translation
    lines.append(f"{'Grand total':<{label_width}} {grand_total:>12.1f}")

    logger.info("LLM cost summary (USD cents):\n%s", "\n".join(lines))


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
    snow_levels_enabled: bool = False


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
    highest_terrain_m: Optional[float] = None
    model_id: str = DEFAULT_ENSEMBLE_MODEL
    model_kind: str = "ensemble"
    model_ref: str = ""


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

    _reset_cost_tracker()
    # Generate unique names for locations to avoid conflicts
    unique_names = _generate_unique_location_names(config)
    for i, location in enumerate(config.locations):
        _process_location(location, config, unique_names[i])
    for area in config.areas:
        if getattr(area, "mode", "area") == "regional":
            _process_regional_area(area, config)
        else:
            _process_area(area, config)
    _log_cost_summary()


def _process_location(location: LocationConfig, config: ForecastConfig, display_name: Optional[str] = None) -> Optional[LocationForecastPayload]:
    """Drive the full fetch/LLM/render workflow for a single configured location."""
    name = location.name
    unique_name = display_name or name
    logger.info("Processing location '%s' (display: '%s')", name, unique_name)
    model_spec = _resolve_model_spec(location, config)
    snow_feature_enabled = _snow_levels_enabled(location, config, model_spec)
    units = _resolve_units(location, global_units=config.units, use_snow_levels=snow_feature_enabled)
    forecast_days = _resolve_forecast_days(config.location_forecast_days, 4)
    payload = _collect_location_payload(
        name,
        config=config,
        units=units,
        thin_select=int(config.location_thin_select or 16),
        forecast_days=forecast_days,
        model_spec=model_spec,
    )
    if not payload:
        return None
    geocode = payload.geocode
    timezone_name = geocode.timezone or "UTC"
    context_llm = (getattr(config, "context_llm", None) or "gpt-4o").strip()
    impact_context = fetch_impact_context(
        name,
        context_type="location",
        forecast_days=forecast_days,
        timezone_name=timezone_name,
        context_llm=context_llm,
    )
    ibf_context = impact_context.content
    _record_cost("Location", unique_name, context=impact_context.cost_cents)
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
            system_prompt = build_spot_system_prompt(
                _unit_instructions(payload.units),
                model_kind=payload.model_kind,
            )
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
            reasoning_payload = (
                _reasoning_payload(
                    _as_bool(config.enable_reasoning),
                    getattr(config, "location_reasoning", None),
                )
                if _supports_reasoning(llm_settings)
                else None
            )
            _snapshot_prompt("location", name, llm_settings.model, system_prompt, prompt)
            forecast_text = generate_forecast_text(
                prompt,
                system_prompt,
                llm_settings,
                reasoning=reasoning_payload,
            )
            _record_cost("Location", unique_name, forecast=consume_last_cost_cents())
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
    if translated_text is not None:
        _record_cost("Location", unique_name, translation=consume_last_cost_cents())

    destination = _build_destination_path(config, unique_name)
    logger.info("Writing forecast page for '%s' → %s", unique_name, destination)
    model_label, model_ack = _model_credit([payload.model_ref or payload.model_id])
    render_forecast_page(
        ForecastPage(
            destination=destination,
            display_name=unique_name,
            issue_time=_format_issue_time(timezone_name),
            forecast_text=forecast_text,
            translated_text=translated_text,
            translation_language=translation_target,
            ibf_context=ibf_context,
            model_label=model_label,
            model_ack_url=model_ack,
        )
    )
    logger.info("Rendered forecast page for '%s' → %s", unique_name, destination)
    return payload


def _process_area(area: AreaConfig, config: ForecastConfig) -> None:
    """Generate an area-level forecast (single text block) across representative spots."""
    logger.info("Processing area '%s'", area.name)
    area_model_spec = _resolve_model_spec(area, config)
    base_units = _resolve_units(
        area,
        global_units=config.units,
        use_snow_levels=_snow_levels_enabled(area, config, area_model_spec),
    )
    thin_select = int(config.area_thin_select or config.location_thin_select or 16)
    forecast_days = _resolve_forecast_days(
        config.area_forecast_days or config.location_forecast_days, 4
    )

    payloads = _collect_area_payloads(area, config, base_units, thin_select, forecast_days, area_model_spec)

    if not payloads:
        logger.warning("Area '%s' has no valid location data; skipping.", area.name)
        return

    area_timezone = payloads[0].geocode.timezone or "UTC"
    context_llm = (getattr(config, "context_llm", None) or "gpt-4o").strip()
    impact_context = fetch_impact_context(
        area.name,
        context_type="area",
        forecast_days=forecast_days,
        timezone_name=area_timezone,
        context_llm=context_llm,
    )
    ibf_context = impact_context.content
    _record_cost("Area", area.name, context=impact_context.cost_cents)
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
            area_kind = "ensemble" if any(p.model_kind == "ensemble" for p in payloads) else "deterministic"
            system_prompt = build_area_system_prompt(
                _unit_instructions(base_units),
                model_kind=area_kind,
            )
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
            reasoning_payload = (
                _reasoning_payload(
                    _as_bool(config.enable_reasoning),
                    getattr(config, "area_reasoning", None),
                )
                if _supports_reasoning(llm_settings)
                else None
            )
            _snapshot_prompt("area", area.name, llm_settings.model, system_prompt, prompt)
            forecast_text = generate_forecast_text(
                prompt,
                system_prompt,
                llm_settings,
                reasoning=reasoning_payload,
            )
            _record_cost("Area", area.name, forecast=consume_last_cost_cents())
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
    if translated_text is not None:
        _record_cost("Area", area.name, translation=consume_last_cost_cents())

    destination = _build_destination_path(config, area.name)
    logger.info("Writing area forecast page for '%s' → %s", area.name, destination)
    map_link = _map_link_for(config, area.name)

    model_label, model_ack = _model_credit(payload.model_ref or payload.model_id for payload in payloads)
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
            model_label=model_label,
            model_ack_url=model_ack,
        )
    )
    logger.info("Rendered area forecast page for '%s' → %s", area.name, destination)


def _process_regional_area(area: AreaConfig, config: ForecastConfig) -> None:
    """Produce a regional forecast that is broken down by sub-regions."""
    logger.info("Processing regional area '%s'", area.name)
    area_model_spec = _resolve_model_spec(area, config)
    base_units = _resolve_units(
        area,
        global_units=config.units,
        use_snow_levels=_snow_levels_enabled(area, config, area_model_spec),
    )
    thin_select = int(config.area_thin_select or config.location_thin_select or 16)
    forecast_days = _resolve_forecast_days(
        config.area_forecast_days or config.location_forecast_days, 4
    )

    payloads = _collect_area_payloads(area, config, base_units, thin_select, forecast_days, area_model_spec)
    if not payloads:
        logger.warning("Regional area '%s' has no valid location data; skipping.", area.name)
        return

    area_timezone = payloads[0].geocode.timezone or "UTC"
    context_llm = (getattr(config, "context_llm", None) or "gpt-4o").strip()
    regional_context = fetch_impact_context(
        area.name,
        context_type="regional",
        forecast_days=forecast_days,
        timezone_name=area_timezone,
        context_llm=context_llm,
    )
    ibf_context = regional_context.content
    _record_cost("Regional", area.name, context=regional_context.cost_cents)
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
            area_kind = "ensemble" if any(p.model_kind == "ensemble" for p in payloads) else "deterministic"
            system_prompt = build_regional_system_prompt(
                _unit_instructions(base_units),
                model_kind=area_kind,
            )
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
            reasoning_payload = (
                _reasoning_payload(
                    _as_bool(config.enable_reasoning),
                    getattr(config, "area_reasoning", None),
                )
                if _supports_reasoning(llm_settings)
                else None
            )
            _snapshot_prompt(
                "regional-area",
                area.name,
                llm_settings.model,
                system_prompt,
                prompt,
            )
            forecast_text = generate_forecast_text(
                prompt,
                system_prompt,
                llm_settings,
                reasoning=reasoning_payload,
            )
            logger.info("Requesting regional LLM forecast for '%s' using model %s", area.name, llm_settings.model)
            _record_cost("Regional", area.name, forecast=consume_last_cost_cents())
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
    if translated_text is not None:
        _record_cost("Regional", area.name, translation=consume_last_cost_cents())

    destination = _build_destination_path(config, area.name)
    logger.info("Writing regional forecast page for '%s' → %s", area.name, destination)
    map_link = _map_link_for(config, area.name)

    model_label, model_ack = _model_credit(payload.model_ref or payload.model_id for payload in payloads)
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
            model_label=model_label,
            model_ack_url=model_ack,
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
    model_spec: Optional[ModelSpec] = None,
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

    highest_terrain: Optional[float] = None
    if units.snow_levels_enabled:
        highest_val = get_highest_point(geocode.latitude, geocode.longitude, radius_km=50)
        if math.isfinite(highest_val):
            highest_terrain = highest_val
        else:
            logger.debug("Highest terrain lookup failed for '%s'; continuing without it.", name)

    request_days = max(forecast_days, 0) + 1
    resolved_model = model_spec or _resolve_model_spec(None, config)
    available_members = max(1, int(getattr(resolved_model, "members", 1) or 1))
    effective_thin = min(thin_select, available_members)
    try:
        logger.info(
            "Fetching forecast data for '%s' (%s days + buffer)", name, forecast_days
        )
        forecast = fetch_forecast(
            ForecastRequest(
                latitude=geocode.latitude,
                longitude=geocode.longitude,
                timezone=geocode.timezone,
                forecast_days=request_days,
                temperature_unit=_temperature_unit_for_api(units.temperature_primary),
                precipitation_unit=_precipitation_unit_for_api(units.precipitation_primary),
                windspeed_unit=_windspeed_unit_for_api(units.windspeed_primary),
                models=resolved_model.model_id,
                model_kind=resolved_model.kind,
            )
        )
    except RuntimeError as exc:
        logger.error("Failed to fetch forecast for %s: %s", name, exc)
        return None

    raw_forecast = forecast.raw

    # Best-available station altitude for snow-level calculations:
    # - explicit config (`units.altitude_m`) wins
    # - else geocode altitude (if provided by Google Elevation)
    # - else Open-Meteo's `elevation` field from the forecast response
    altitude_for_snow = units.altitude_m
    if altitude_for_snow <= 0:
        if isinstance(getattr(geocode, "altitude", None), (int, float)) and float(geocode.altitude) > 0:
            altitude_for_snow = float(geocode.altitude)
        else:
            try:
                elevation = float(raw_forecast.get("elevation"))  # type: ignore[call-arg]
            except Exception:
                elevation = 0.0
            if elevation > 0:
                altitude_for_snow = elevation

    if units.snow_levels_enabled and resolved_model.kind == "deterministic":
        if highest_terrain is not None:
            logger.info(
                "Snow levels: enabled; station_altitude=%.0fm; max_terrain_50km=%.0fm",
                altitude_for_snow,
                highest_terrain,
            )
        else:
            logger.info(
                "Snow levels: enabled; station_altitude=%.0fm; max_terrain_50km=unavailable",
                altitude_for_snow,
            )

    # Optional second request to fetch pressure-level profiles for snow-level calculations.
    if units.snow_levels_enabled and resolved_model.kind == "deterministic":
        if not _has_any_freezing_level(raw_forecast):
            if resolved_model.model_id in _SNOW_PROFILE_UNSUPPORTED_MODELS:
                logger.info(
                    "Snow levels: skipping profile fetch (model '%s' has no pressure-level data in this environment)",
                    resolved_model.model_id,
                )
            elif _needs_snow_profile_request(raw_forecast):
                try:
                    logger.info("Snow levels: freezing level unavailable; fetching pressure-level profile fields")
                    profile = fetch_forecast(
                        ForecastRequest(
                            latitude=geocode.latitude,
                            longitude=geocode.longitude,
                            timezone=geocode.timezone,
                            forecast_days=request_days,
                            temperature_unit=_temperature_unit_for_api(units.temperature_primary),
                            precipitation_unit=_precipitation_unit_for_api(units.precipitation_primary),
                            windspeed_unit=_windspeed_unit_for_api(units.windspeed_primary),
                            models=resolved_model.model_id,
                            model_kind=resolved_model.kind,
                            hourly_fields=HOURLY_FIELDS_SNOW_PROFILE,
                        )
                    )
                    if _has_any_pressure_level_profile(profile.raw):
                        raw_forecast = _merge_open_meteo_hourly(raw_forecast, profile.raw)
                    else:
                        _SNOW_PROFILE_UNSUPPORTED_MODELS.add(resolved_model.model_id)
                        logger.info(
                            "Snow levels: pressure-level variables returned all-null/undefined for model '%s'; "
                            "disabling profile-based snow levels for this model.",
                            resolved_model.model_id,
                        )
                except Exception as exc:
                    logger.info("Snow-profile fetch failed for '%s'; continuing without it (%s).", name, exc)

    dataset = build_processed_days(
        raw_forecast,
        timezone_name=geocode.timezone or "UTC",
        precipitation_unit=units.precipitation_primary,
        windspeed_unit=units.windspeed_primary,
        thin_select=effective_thin,
        location_altitude=altitude_for_snow,
        snow_levels_enabled=units.snow_levels_enabled,
        highest_terrain_m=highest_terrain,
        pressure_levels_hpa=list(PRESSURE_LEVELS_SNOW_HPA),
    )
    dataset = _limit_days(dataset, forecast_days)
    if not dataset:
        logger.warning("No processed data produced for '%s'; skipping.", name)
        return None

    cache_slug = cache_label or f"{name}__{resolved_model.kind}__{resolved_model.model_id}"
    dataset_path = _write_dataset_cache(cache_slug, dataset)
    logger.info("Processed %d day(s) for '%s'; dataset cached at %s", len(dataset), name, dataset_path)

    if units.snow_levels_enabled and resolved_model.kind == "deterministic":
        _log_snow_levels_summary(
            name,
            raw_forecast,
            dataset,
            timezone_name=geocode.timezone or "UTC",
        )
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
        highest_terrain_m=highest_terrain,
        model_id=resolved_model.model_id,
        model_kind=resolved_model.kind,
        model_ref=resolved_model.ref,
    )


def _collect_area_payloads(
    area: AreaConfig,
    config: ForecastConfig,
    base_units: LocationUnits,
    thin_select: int,
    forecast_days: int,
    model_spec: ModelSpec,
) -> List[LocationForecastPayload]:
    """Fetch datasets for each representative location needed for an area."""
    payloads: List[LocationForecastPayload] = []
    for location_name in area.locations:
        logger.info("Collecting data for representative location '%s' in area '%s'", location_name, area.name)
        location_units = _find_location_units(config, location_name)
        location_cfg = _find_location_config(config, location_name)
        effective_spec = _resolve_model_spec(location_cfg, config) if location_cfg else model_spec

        # Snow levels can be enabled globally, per-area, or per-location, but they only
        # apply when the effective model is deterministic.
        if getattr(effective_spec, "kind", None) != "deterministic":
            effective_snow_levels = False
        else:
            if location_cfg is not None and getattr(location_cfg, "snow_levels", None) is not None:
                effective_snow_levels = bool(location_cfg.snow_levels)
            elif getattr(area, "snow_levels", None) is not None:
                effective_snow_levels = bool(area.snow_levels)
            else:
                effective_snow_levels = bool(getattr(config, "snow_levels", False))

        if location_units:
            units_for_location = replace(
                base_units,
                altitude_m=location_units.altitude_m,
                snow_levels_enabled=effective_snow_levels,
            )
        else:
            units_for_location = replace(base_units, snow_levels_enabled=effective_snow_levels)
        slug = f"{area.name}__{location_name}__{effective_spec.kind}__{effective_spec.model_id}"
        payload = _collect_location_payload(
            location_name,
            config=config,
            units=units_for_location,
            thin_select=thin_select,
            forecast_days=forecast_days,
            cache_label=slug,
            model_spec=effective_spec,
        )
        if payload:
            payloads.append(payload)
    return payloads


def _resolve_units(
    config_obj: SupportsUnits,
    *,
    global_units: Dict[str, str] | None = None,
    use_snow_levels: bool = False,
) -> LocationUnits:
    """Normalize/merge explicit unit overrides with defaults for a config entry."""
    units: Dict[str, str] = {}
    if global_units:
        units.update(global_units)
    units.update(getattr(config_obj, "units", {}) or {})

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
        snow_levels_enabled=use_snow_levels,
    )


def _resolve_model_spec(config_obj: object, config: ForecastConfig) -> ModelSpec:
    """
    Determine which forecast model to use for a given config entity.

    Supports:
    - Explicit prefixes: "ens:<id>" / "det:<id>"
    - Back-compat: unprefixed ensemble IDs still work
    """
    candidate = None
    if config_obj is not None:
        candidate = getattr(config_obj, "model", None)
    if not candidate:
        # Prefer the unified global name ("model"), but accept legacy "ensemble_model".
        candidate = getattr(config, "model", None) or getattr(config, "ensemble_model", None)
    if not candidate:
        candidate = f"ens:{DEFAULT_ENSEMBLE_MODEL}"
    return resolve_model_spec(str(candidate))


def _generate_unique_location_names(config: ForecastConfig) -> List[str]:
    """
    Generate unique display names for locations to avoid conflicts when multiple
    forecasts exist for the same location name (e.g., deterministic vs ensemble).

    Returns a list of unique display names, one per location in config order.
    For duplicates, appends suffixes like " (Deterministic)", " (Ensemble)", or " 1", " 2".
    """
    from collections import defaultdict
    
    name_counts: Dict[str, int] = {}
    location_kinds: List[tuple[str, str]] = []  # (name, kind) pairs in order
    
    # First pass: count occurrences and record model kinds
    for location in config.locations:
        name = location.name
        name_counts[name] = name_counts.get(name, 0) + 1
        model_spec = _resolve_model_spec(location, config)
        location_kinds.append((name, model_spec.kind))
    
    # Determine which names should use kind labels (exactly 2 duplicates with different kinds)
    name_should_use_kinds: Dict[str, bool] = {}
    name_kinds_all: Dict[str, set] = defaultdict(set)
    for i, location in enumerate(config.locations):
        name = location.name
        kind = location_kinds[i][1]
        name_kinds_all[name].add(kind)
    
    for name in name_counts:
        if name_counts[name] == 2 and len(name_kinds_all[name]) == 2:
            name_should_use_kinds[name] = True
        else:
            name_should_use_kinds[name] = False
    
    # Second pass: assign unique names
    result: List[str] = []
    name_occurrences: Dict[str, int] = {}
    
    for i, location in enumerate(config.locations):
        name = location.name
        kind = location_kinds[i][1]
        
        if name_counts[name] == 1:
            # No duplicates, use original name
            result.append(name)
        else:
            # Duplicate found - need to disambiguate
            occurrence = name_occurrences.get(name, 0) + 1
            name_occurrences[name] = occurrence
            
            # If exactly 2 duplicates with different kinds, use kind labels
            if name_should_use_kinds[name]:
                if kind == "deterministic":
                    result.append(f"{name} (Deterministic)")
                else:
                    result.append(f"{name} (Ensemble)")
            else:
                # More than two duplicates or same kind - use numbers
                result.append(f"{name} {occurrence}")
    
    return result


def _snow_levels_enabled(entity: SupportsUnits, config: ForecastConfig, model_spec: ModelSpec) -> bool:
    """
    Resolve snow-level feature enablement.

    IMPORTANT: Snow levels are only supported for deterministic models.
    """
    if getattr(model_spec, "kind", None) != "deterministic":
        return False
    override = getattr(entity, "snow_levels", None)
    if override is not None:
        return bool(override)
    return bool(getattr(config, "snow_levels", False))


def _has_any_freezing_level(forecast_raw: dict) -> bool:
    """Return True if the payload includes at least one non-null freezing level value."""
    try:
        hourly = forecast_raw.get("hourly", {})
        series = hourly.get("freezing_level_height", [])
        return any(v is not None for v in series)
    except Exception:
        return False


def _needs_snow_profile_request(forecast_raw: dict) -> bool:
    """
    Decide whether it's worth doing a second request for pressure-level snow diagnostics.

    We only do the extra call when the base data suggests snow might be relevant at all:
    precip > 0, temp < ~15C, and weather_code not already a snow/freezing type.
    """
    try:
        hourly = forecast_raw.get("hourly", {})
        temps = hourly.get("temperature_2m", [])
        precip = hourly.get("precipitation", [])
        codes = hourly.get("weather_code", [])
        for t, p, c in zip(temps, precip, codes):
            if t is None or p is None or c is None:
                continue
            try:
                if should_check_snow_level(float(p), int(c), float(t)):
                    return True
            except Exception:
                continue
        return False
    except Exception:
        return False


def _merge_open_meteo_hourly(base_raw: dict, extra_raw: dict) -> dict:
    """
    Merge additional hourly arrays/units into a base Open-Meteo payload.

    Requires `hourly.time` to match exactly. If it doesn't, returns base_raw unchanged.
    """
    if not isinstance(base_raw, dict) or not isinstance(extra_raw, dict):
        return base_raw

    base_hourly = base_raw.get("hourly")
    extra_hourly = extra_raw.get("hourly")
    if not isinstance(base_hourly, dict) or not isinstance(extra_hourly, dict):
        return base_raw

    base_times = base_hourly.get("time")
    extra_times = extra_hourly.get("time")
    if not isinstance(base_times, list) or not isinstance(extra_times, list) or base_times != extra_times:
        return base_raw

    # Merge hourly arrays (excluding time which we've validated).
    for key, value in extra_hourly.items():
        if key == "time":
            continue
        base_hourly[key] = value

    # Merge units too (useful for member detection / completeness).
    base_units = base_raw.get("hourly_units")
    extra_units = extra_raw.get("hourly_units")
    if isinstance(extra_units, dict):
        if not isinstance(base_units, dict):
            base_raw["hourly_units"] = dict(extra_units)
        else:
            base_units.update(extra_units)

    return base_raw


def _has_any_pressure_level_profile(raw: dict) -> bool:
    """
    Return True if the Open-Meteo payload contains any non-null pressure-level values
    required for snow-level diagnostics.

    Open-Meteo may return the requested keys with unit "undefined" and all-null arrays
    when the model does not support those variables.
    """
    try:
        hourly = raw.get("hourly", {})
        if not isinstance(hourly, dict):
            return False
        # Only check the profile-critical fields (temps/RH/geopotential). Surface pressure
        # is commonly present even when pressure levels are not.
        keys = [
            k
            for k in hourly.keys()
            if (
                (k.startswith("temperature_") and k.endswith("hPa"))
                or (k.startswith("relative_humidity_") and k.endswith("hPa"))
                or (k.startswith("geopotential_height_") and k.endswith("hPa"))
            )
        ]
        for key in keys:
            series = hourly.get(key, [])
            if isinstance(series, list) and any(v is not None for v in series):
                return True
        return False
    except Exception:
        return False


def _log_snow_levels_summary(name: str, raw_forecast: dict, dataset: List[dict], *, timezone_name: str) -> None:
    """
    INFO-level debugging summary for snow-level calculations.
    """
    try:
        hourly = raw_forecast.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        precip = hourly.get("precipitation", [])
        codes = hourly.get("weather_code", [])

        candidates: list[tuple[int, str, float, float, int]] = []
        for idx, (ts, t, p, c) in enumerate(zip(times, temps, precip, codes)):
            if ts is None or t is None or p is None or c is None:
                continue
            try:
                t_f = float(t)
                p_f = float(p)
                c_i = int(c)
            except Exception:
                continue
            if should_check_snow_level(p_f, c_i, t_f):
                candidates.append((idx, str(ts), t_f, p_f, c_i))

        # Map dataset snow levels by local date/hour
        produced: dict[tuple[str, str], int] = {}
        debug_by_hour: dict[tuple[str, str], dict] = {}
        for day in dataset:
            date_key = day.get("date")
            for hour in day.get("hours", []):
                hour_key = hour.get("hour")
                member = (hour.get("ensemble_members", {}) or {}).get("member00") or {}
                sl = member.get("snow_level")
                if isinstance(date_key, str) and isinstance(hour_key, str) and isinstance(sl, int) and sl > 0:
                    produced[(date_key, hour_key)] = sl
                dbg = member.get("_snow_level_debug")
                if isinstance(date_key, str) and isinstance(hour_key, str) and isinstance(dbg, dict):
                    debug_by_hour[(date_key, hour_key)] = dbg

        produced_values = list(produced.values())
        logger.info(
            "Snow levels summary for '%s': candidate_hours=%d, computed_hours=%d",
            name,
            len(candidates),
            len(produced_values),
        )
        if produced_values:
            logger.info(
                "Snow levels summary for '%s': min=%dm max=%dm",
                name,
                min(produced_values),
                max(produced_values),
            )

        # Print a few sample candidate hours
        if candidates:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            try:
                tz = ZoneInfo(timezone_name)
            except Exception:
                tz = ZoneInfo("UTC")

            sample = candidates[:5]
            for _, ts, t_f, p_f, c_i in sample:
                # Open-Meteo times can be either "YYYY-MM-DDTHH:MM" or include "Z"/offset.
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(tz)
                    date_key = dt.strftime("%Y-%m-%d")
                    hour_key = dt.strftime("%H:00")
                except Exception:
                    date_key, hour_key = "?", "?"
                sl = produced.get((date_key, hour_key))
                dbg = debug_by_hour.get((date_key, hour_key))
                dbg_txt = ""
                if isinstance(dbg, dict) and dbg.get("raw_estimate_m") is not None:
                    try:
                        dbg_txt = f" raw_est={float(dbg['raw_estimate_m']):.0f}m"
                    except Exception:
                        dbg_txt = ""
                logger.info(
                    "Snow levels candidate '%s' %s %s: T=%.1fC precip=%.1fmm code=%d snow_level=%s%s",
                    name,
                    date_key,
                    hour_key,
                    t_f,
                    p_f,
                    c_i,
                    (f"{sl}m" if isinstance(sl, int) else "none"),
                    dbg_txt,
                )
    except Exception as exc:
        logger.debug("Snow levels summary failed for '%s': %s", name, exc)


def _find_location_units(config: ForecastConfig, name: str) -> Optional[LocationUnits]:
    """Look up a location's specific unit overrides by name."""
    target = name.strip().lower()
    for entry in config.locations:
        if entry.name.strip().lower() == target:
            model_spec = _resolve_model_spec(entry, config)
            return _resolve_units(
                entry,
                global_units=config.units,
                use_snow_levels=_snow_levels_enabled(entry, config, model_spec),
            )
    return None


def _find_location_config(config: ForecastConfig, name: str):
    """Return the LocationConfig instance matching the provided name, if any."""
    target = name.strip().lower()
    for entry in config.locations:
        if entry.name.strip().lower() == target:
            return entry
    return None


def _model_credit(model_refs: Iterable[str]) -> tuple[str, Optional[str]]:
    """Return a human-readable label and optional acknowledgement URL for footer text."""
    unique_refs = sorted({(ref or "").strip() for ref in model_refs if ref})
    if not unique_refs:
        unique_refs = [f"ens:{DEFAULT_ENSEMBLE_MODEL}"]

    specs = [resolve_model_spec(ref) for ref in unique_refs]

    if len(specs) == 1:
        return specs[0].name, specs[0].ack_url

    labels = [spec.name for spec in specs]
    composite = "multiple models (" + ", ".join(labels) + ")"
    return composite, None


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
    now_hour = datetime.now(tz).hour
    if now_hour >= 22:
        return (
            "CRITICAL: The first forecast period covers only the last 1-2 hours of the day. "
            "Be extremely brief (1-2 sentences), focus only on immediate conditions, and describe temperatures as a short-term trend instead of quoting full low/high values."
        )
    if now_hour >= 15:
        return (
            "IMPORTANT: The first forecast period only covers the remainder of today. Describe how temperatures change through the rest of the day (e.g., 'temperatures drop from 18°C early evening to 13°C by midnight') instead of quoting a separate low/high pair."
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


def _snapshot_prompt(
    kind: str,
    name: str,
    model: Optional[str],
    system_prompt: str,
    user_prompt: str,
) -> None:
    """Persist the full LLM prompt payload for later inspection."""
    try:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        slug_base = slugify(f"{kind}-{name}") or kind or "prompt"
        filename = f"{timestamp}_{slug_base}.txt"
        path = PROMPT_SNAPSHOT_DIR / filename
        header = "\n".join(
            [
                f"kind: {kind}",
                f"name: {name}",
                f"model: {model or 'unknown'}",
                f"timestamp_utc: {timestamp}",
            ]
        )
        body = "\n\n".join(
            [
                header,
                "=== SYSTEM PROMPT ===",
                system_prompt.strip(),
                "=== USER PROMPT ===",
                user_prompt.strip(),
            ]
        )
        path.write_text(body + "\n", encoding="utf-8")
        _cleanup_prompt_cache()
    except Exception as exc:
        logger.debug("Failed to snapshot prompt for %s/%s: %s", kind, name, exc)


def _cleanup_prompt_cache(max_age_days: int = 3, min_keep: int = 10) -> None:
    """
    Remove old prompt snapshot files from the cache.
    
    Files older than max_age_days will be deleted, but at least min_keep files
    will always be retained (the newest ones) as examples.
    
    Args:
        max_age_days: Files older than this many days will be deleted.
        min_keep: Minimum number of files to keep regardless of age.
    """
    try:
        cutoff_time = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        prompt_files = []
        
        # Collect all prompt files with their parsed timestamps
        for path in PROMPT_SNAPSHOT_DIR.glob("*.txt"):
            try:
                # Extract timestamp from filename: YYYYMMDDTHHMMSSZ_*.txt
                filename = path.name
                if "_" not in filename:
                    continue
                timestamp_str = filename.split("_", 1)[0]
                file_time = datetime.strptime(timestamp_str, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                prompt_files.append((file_time, path))
            except (ValueError, OSError):
                # Skip files with invalid timestamps or other errors
                continue
        
        if not prompt_files:
            return
        
        # Sort by timestamp, newest first
        prompt_files.sort(key=lambda x: x[0], reverse=True)
        
        # Keep at least min_keep files
        files_to_keep = prompt_files[:min_keep]
        files_to_check = prompt_files[min_keep:]
        
        # Delete files that are both old and not in the top min_keep
        deleted_count = 0
        for file_time, path in files_to_check:
            if file_time < cutoff_time:
                try:
                    path.unlink()
                    deleted_count += 1
                except OSError:
                    continue
        
        if deleted_count > 0:
            logger.debug("Cleaned up %d old prompt snapshot(s), kept %d", deleted_count, len(files_to_keep))
    except Exception as exc:
        logger.debug("Failed to cleanup prompt cache: %s", exc)


def _limit_days(days: List[dict], max_days: int) -> List[dict]:
    """Restrict the dataset to the requested number of days."""
    if max_days <= 0:
        return days
    if len(days) <= max_days:
        return days
    return days[:max_days]


def _resolve_forecast_days(raw_value, fallback: int) -> int:
    """Normalize configured forecast day counts into integers."""
    if raw_value in (None, ""):
        return fallback
    try:
        return max(0, int(raw_value))
    except (TypeError, ValueError):
        return fallback


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
    return location.translation_language or config.translation_language


def _area_translation_language(area: AreaConfig, config: ForecastConfig) -> Optional[str]:
    """Determine the translation language for an area-level forecast."""
    # Translation aliases are normalized in the config model.
    return area.translation_language or config.translation_language


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
        _snapshot_prompt("translation", language, settings.model, system_prompt, user_prompt)
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


_REASONING_DISABLE = {"off", "disable", "disabled", "none", "false"}
_REASONING_LEVELS = {"low", "medium", "high", "auto"}
_REASONING_MODEL_KEYWORDS = ("o1", "o3", "o4", "gpt-4.1", "gpt-5")


def _reasoning_payload(enabled: bool, level: Optional[str]) -> Optional[dict]:
    """
    Build the extra_body payload that toggles reasoning for OpenAI-compatible providers.
    """
    if not enabled:
        return None
    effort, max_tokens, disable_override = _parse_reasoning_setting(level)
    if disable_override:
        return None
    resolved_effort = effort or "medium"
    payload: dict[str, object] = {"reasoning": {"effort": resolved_effort}}
    if max_tokens:
        payload["max_output_tokens"] = max_tokens
    return payload


def _parse_reasoning_setting(value: Optional[str]) -> tuple[Optional[str], Optional[int], bool]:
    """
    Interpret a free-form reasoning string like "high", "low:2048", or "off".

    Returns (effort, max_tokens, disable_override).
    """
    if not value:
        return None, None, False
    raw = str(value).strip()
    if not raw:
        return None, None, False
    lowered = raw.lower()
    if lowered in _REASONING_DISABLE:
        return None, None, True

    effort = next((lvl for lvl in _REASONING_LEVELS if lvl in lowered), None)
    token_match = re.search(r"(\d{2,})", raw)
    max_tokens = int(token_match.group(1)) if token_match else None

    return effort, max_tokens, False


def _supports_reasoning(settings: Optional[LLMSettings]) -> bool:
    """Return True if the active LLM can accept OpenAI-style reasoning arguments."""
    if not settings or settings.is_google:
        return False
    if settings.provider != "openai":
        return False
    model_name = (settings.model or "").lower()
    return any(keyword in model_name for keyword in _REASONING_MODEL_KEYWORDS)
