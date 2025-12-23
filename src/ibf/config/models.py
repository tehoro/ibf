"""
Pydantic models for validating and hashing forecast configuration files.
"""

from __future__ import annotations

import json
import hashlib
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field, ValidationError


class ConfigError(RuntimeError):
    """Raised when configuration files cannot be loaded or validated."""


class LocationConfig(BaseModel):
    """
    Configuration for a single forecast location.

    Attributes:
        name: Display name (e.g., "London, UK").
        translation_language: Optional target language for translation.
        units: Dictionary of unit preferences (e.g., {"temperature_unit": "celsius"}).
    """
    name: str
    translation_language: Optional[str] = None
    units: Dict[str, str] = Field(default_factory=dict)
    snow_levels: Optional[bool] = None
    model: Optional[str] = None


class AreaConfig(BaseModel):
    """
    Configuration for an aggregated area forecast.

    Attributes:
        name: Display name for the area.
        locations: List of location names that comprise the area.
        translation_language: Optional target language.
        mode: "area" (summary) or "regional" (breakdown).
        units: Dictionary of unit preferences.
    """
    name: str
    locations: List[str]
    translation_language: Optional[str] = None
    mode: Literal["area", "regional"] = "area"
    units: Dict[str, str] = Field(default_factory=dict)
    snow_levels: Optional[bool] = None
    model: Optional[str] = None


class ForecastConfig(BaseModel):
    """
    Top-level configuration for the IBF toolkit.

    Attributes:
        locations: List of individual locations to forecast.
        areas: List of areas to forecast.
        web_root: Directory where HTML output will be written.
        location_forecast_days: Number of days for location forecasts.
        area_forecast_days: Number of days for area forecasts.
        location_wordiness: "normal", "brief", or "detailed".
        area_wordiness: "normal", "brief", or "detailed".
        enable_reasoning: Whether to allow LLM reasoning steps (if supported).
        location_reasoning: Specific reasoning setting for locations.
        area_reasoning: Specific reasoning setting for areas.
        location_impact_based: Enable impact-based context for locations.
        area_impact_based: Enable impact-based context for areas.
        location_thin_select: Number of ensemble members to select for locations.
        area_thin_select: Number of ensemble members to select for areas.
        llm: LLM model identifier (e.g., "gemini-3-flash-preview").
        context_llm: LLM model identifier to use for impact-context web search (default "gemini-3-flash-preview").
        translation_language: Global default translation language.
        translation_llm: Specific LLM to use for translation.
        recent_overwrite_minutes: Prevent overwriting fresh forecasts if < N minutes old.
    """
    locations: List[LocationConfig] = Field(default_factory=list)
    areas: List[AreaConfig] = Field(default_factory=list)
    units: Dict[str, str] = Field(default_factory=dict)
    web_root: Optional[Path] = None
    location_forecast_days: Optional[int] = None
    area_forecast_days: Optional[int] = None
    location_wordiness: Optional[str] = None
    area_wordiness: Optional[str] = None
    enable_reasoning: bool = True
    location_reasoning: Optional[str] = None
    area_reasoning: Optional[str] = None
    location_impact_based: Optional[bool] = None
    area_impact_based: Optional[bool] = None
    location_thin_select: Optional[int] = None
    area_thin_select: Optional[int] = None
    llm: Optional[str] = None
    context_llm: Optional[str] = None
    translation_language: Optional[str] = None
    translation_llm: Optional[str] = None
    recent_overwrite_minutes: int = 0
    snow_levels: bool = False
    # Global default forecast model. This name matches the per-location/per-area override field.
    model: Optional[str] = None

    model_config = {
        "arbitrary_types_allowed": True,
        "populate_by_name": True,
    }

    @property
    def hash(self) -> str:
        """
        Deterministic hash of the normalized config, used to detect changes.
        """
        payload = self.model_dump(mode="json", round_trip=True)
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()


def load_config(path: Path | str) -> ForecastConfig:
    """
    Load and validate a TOML config file into a ForecastConfig instance.

    Args:
        path: Path to the TOML configuration file.

    Returns:
        A validated ForecastConfig object.

    Raises:
        ConfigError: If the file is missing, unreadable, or invalid.
    """
    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        raise ConfigError(f"Configuration file not found: {config_path}")

    try:
        with config_path.open("rb") as handle:
            raw_data: Dict[str, Any] = tomllib.load(handle)
    except OSError as exc:
        raise ConfigError(f"Unable to read configuration file: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in configuration file: {exc}") from exc

    raw_data = _normalize_toml_schema(raw_data)

    try:
        return ForecastConfig.model_validate(raw_data)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc


def _normalize_toml_schema(data: Any) -> Dict[str, Any]:
    """
    Normalize TOML-specific schema conveniences to the internal config model.

    Accepts singular table arrays: [[location]] and [[area]], and maps them to the
    internal plural list fields: locations and areas.
    """
    if not isinstance(data, dict):
        raise ConfigError("Configuration root must be a TOML table/object.")

    if "locations" in data:
        raise ConfigError("Use [[location]] blocks (singular) instead of [[locations]].")
    if "areas" in data:
        raise ConfigError("Use [[area]] blocks (singular) instead of [[areas]].")

    normalized = dict(data)
    if "units" in normalized:
        raise ConfigError("Use inline unit keys at the top level; [units] tables are not supported.")

    root_units = _extract_inline_units(normalized)
    if root_units:
        normalized["units"] = root_units

    locations = _coerce_table_array(normalized.pop("location", None), "location")
    for location in locations:
        if "units" in location:
            raise ConfigError("Use inline unit keys inside [[location]]; [location.units] is not supported.")
        location_units = _extract_inline_units(location)
        if location_units:
            location["units"] = location_units

    areas = _coerce_table_array(normalized.pop("area", None), "area")
    for area in areas:
        if "units" in area:
            raise ConfigError("Use inline unit keys inside [[area]]; [area.units] is not supported.")
        area_units = _extract_inline_units(area)
        if area_units:
            area["units"] = area_units

    normalized["locations"] = locations
    normalized["areas"] = areas
    return normalized


def _coerce_table_array(value: Any, label: str) -> list[dict]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        if not all(isinstance(item, dict) for item in value):
            raise ConfigError(f"Each [[{label}]] entry must be a table/object.")
        return value
    raise ConfigError(f"Invalid [{label}] block; expected a table or array of tables.")


_UNIT_KEYS = {
    "temperature_unit",
    "precipitation_unit",
    "windspeed_unit",
    "snowfall_unit",
    "altitude_m",
}


def _extract_inline_units(payload: dict) -> Dict[str, Any]:
    units: Dict[str, Any] = {}
    for key in _UNIT_KEYS:
        if key in payload:
            units[key] = payload.pop(key)
    return units
