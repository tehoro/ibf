"""
Pydantic models for validating and hashing forecast configuration files.
"""

from __future__ import annotations

import json
import hashlib
import logging
import re
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

logger = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """Raised when configuration files cannot be loaded or validated."""


class LocationConfig(BaseModel):
    """
    Configuration for a single forecast location.

    Attributes:
        name: Display name (e.g., "London, UK").
        translation_language: Optional target language for translation.
        extra_context: Optional user-supplied impact context notes.
        units: Dictionary of unit preferences (e.g., {"temperature_unit": "celsius"}).
        minimum_refresh_minutes: Optional per-location refresh interval override.
    """
    name: str
    translation_language: Optional[str] = None
    extra_context: Optional[str] = None
    units: Dict[str, str] = Field(default_factory=dict)
    snow_levels: Optional[bool] = None
    model: Optional[str] = None
    minimum_refresh_minutes: Optional[int] = None

    model_config = {
        "extra": "forbid",
    }


class AreaConfig(BaseModel):
    """
    Configuration for an aggregated area forecast.

    Attributes:
        name: Display name for the area.
        locations: List of location names that comprise the area.
        translation_language: Optional target language.
        extra_context: Optional user-supplied impact context notes.
        mode: "area" (summary) or "regional" (breakdown).
        units: Dictionary of unit preferences.
        minimum_refresh_minutes: Optional per-area refresh interval override.
    """
    name: str
    locations: List[str]
    translation_language: Optional[str] = None
    extra_context: Optional[str] = None
    mode: Literal["area", "regional"] = "area"
    units: Dict[str, str] = Field(default_factory=dict)
    snow_levels: Optional[bool] = None
    model: Optional[str] = None
    minimum_refresh_minutes: Optional[int] = None

    model_config = {
        "extra": "forbid",
    }


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
        minimum_refresh_minutes: Minimum minutes between refreshes (global default).
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
    location_impact_based: bool = True
    area_impact_based: bool = True
    location_thin_select: Optional[int] = None
    area_thin_select: Optional[int] = None
    llm: Optional[str] = None
    context_llm: Optional[str] = None
    translation_language: Optional[str] = None
    translation_llm: Optional[str] = None
    minimum_refresh_minutes: int = 0
    snow_levels: bool = False
    # Global default forecast model. This name matches the per-location/per-area override field.
    model: Optional[str] = None

    model_config = {
        "arbitrary_types_allowed": True,
        "populate_by_name": True,
        "extra": "forbid",
    }

    @model_validator(mode="after")
    def _validate_llm_settings(self) -> "ForecastConfig":
        """Validate LLM-related fields and supported context models."""
        if self.llm is not None:
            raw = str(self.llm).strip()
            if not raw:
                raise ValueError("llm cannot be blank.")
        if self.translation_llm is not None:
            raw = str(self.translation_llm).strip()
            if not raw:
                raise ValueError("translation_llm cannot be blank.")
        if self.context_llm:
            raw = str(self.context_llm).strip()
            if raw and not _is_supported_context_llm(raw):
                raise ValueError(
                    "context_llm must be a Gemini or OpenAI model name; OpenRouter models are not supported "
                    "for impact-context web search."
                )
        return self

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
        config = ForecastConfig.model_validate(raw_data)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc

    _warn_on_unknown_area_locations(config)
    return config


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

    _raise_on_disallowed_units(normalized, "Global settings")
    root_units = _extract_inline_units(normalized)
    if root_units:
        normalized["units"] = root_units

    locations = _coerce_table_array(normalized.pop("location", None), "location")
    for location in locations:
        if "units" in location:
            raise ConfigError("Use inline unit keys inside [[location]]; [location.units] is not supported.")
        _raise_on_disallowed_units(location, "[[location]]")
        location_units = _extract_inline_units(location)
        if location_units:
            location["units"] = location_units

    areas = _coerce_table_array(normalized.pop("area", None), "area")
    for area in areas:
        if "units" in area:
            raise ConfigError("Use inline unit keys inside [[area]]; [area.units] is not supported.")
        _raise_on_disallowed_units(area, "[[area]]")
        area_units = _extract_inline_units(area)
        if area_units:
            area["units"] = area_units

    normalized["locations"] = locations
    normalized["areas"] = areas
    return normalized


def _coerce_table_array(value: Any, label: str) -> list[dict]:
    """Coerce a TOML table or array-of-tables into a list of dicts."""
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
}

_DISALLOWED_UNIT_KEYS = {
    "snowfall_unit",
    "altitude_m",
}

_UNIT_ALIASES: dict[str, dict[str, str]] = {
    "temperature_unit": {
        "c": "celsius",
        "°c": "celsius",
        "celsius": "celsius",
        "centigrade": "celsius",
        "celcius": "celsius",
        "f": "fahrenheit",
        "°f": "fahrenheit",
        "fahrenheit": "fahrenheit",
    },
    "precipitation_unit": {
        "mm": "mm",
        "millimeter": "mm",
        "millimeters": "mm",
        "millimetre": "mm",
        "millimetres": "mm",
        "in": "inch",
        "inch": "inch",
        "inches": "inch",
    },
    "windspeed_unit": {
        "kph": "kph",
        "kmh": "kph",
        "km/h": "kph",
        "kmph": "kph",
        "mph": "mph",
        "mps": "mps",
        "m/s": "mps",
        "ms": "mps",
        "kt": "kt",
        "kts": "kt",
        "kn": "kt",
        "knots": "kt",
    },
}


def _extract_inline_units(payload: dict) -> Dict[str, Any]:
    """Pop inline unit keys from a payload and normalize their values."""
    units: Dict[str, Any] = {}
    for key in _UNIT_KEYS:
        if key in payload:
            units[key] = _normalize_unit_value(payload.pop(key), key)
    return units


def _raise_on_disallowed_units(payload: dict, scope: str) -> None:
    """Reject disallowed unit keys for the given config scope."""
    disallowed = sorted(key for key in _DISALLOWED_UNIT_KEYS if key in payload)
    if not disallowed:
        return
    joined = ", ".join(disallowed)
    raise ConfigError(
        f"{scope} does not support {joined}. "
        "Snowfall units are derived from precipitation and altitude comes from geocoding."
    )


def _normalize_unit_value(value: Any, key: str) -> str:
    """Normalize a unit value, supporting primary(secondary) syntax."""
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string (got {type(value).__name__}).")
    raw = value.strip()
    if not raw:
        raise ConfigError(f"{key} cannot be blank.")
    if "(" in raw:
        if not raw.endswith(")") or raw.count("(") != 1:
            raise ConfigError(
                f"{key} secondary units must be written as primary(secondary), e.g. \"mph(kph)\"."
            )
        primary, secondary = raw.split("(", 1)
        primary_norm = _normalize_unit_token(primary, key)
        secondary_norm = _normalize_unit_token(secondary[:-1], key)
        return f"{primary_norm}({secondary_norm})"
    return _normalize_unit_token(raw, key)


def _normalize_unit_token(token: str, key: str) -> str:
    """Normalize a unit token to its canonical value."""
    normalized = token.strip().lower()
    mapping = _UNIT_ALIASES.get(key, {})
    if normalized in mapping:
        return mapping[normalized]
    allowed = ", ".join(sorted(set(mapping.values())))
    raise ConfigError(f"Invalid {key} value '{token}'. Allowed: {allowed}.")


def _is_supported_context_llm(value: str) -> bool:
    """Return True if the LLM name is supported for context web search."""
    lowered = value.strip().lower()
    if lowered.startswith("gemini-") or lowered.startswith("google/gemini-"):
        return True
    if lowered.startswith("gpt-") or lowered.startswith("openai/"):
        return True
    if re.match(r"^o[1-9]", lowered):
        return True
    return False




def _warn_on_unknown_area_locations(config: ForecastConfig) -> None:
    """Log debug info when an area references a location missing from [[location]]."""
    if not config.areas:
        return
    known = {loc.name.strip() for loc in config.locations}
    for area in config.areas:
        for entry in area.locations:
            if entry.strip() not in known:
                logger.debug(
                    "Area '%s' references location '%s' that is not defined in [[location]]; "
                    "area-level settings apply for this member.",
                    area.name,
                    entry,
                )
