"""
Pydantic models for validating and hashing forecast configuration files.
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator


class ConfigError(RuntimeError):
    """Raised when configuration files cannot be loaded or validated."""


class LocationConfig(BaseModel):
    """
    Configuration for a single forecast location.

    Attributes:
        name: Display name (e.g., "London, UK").
        translation_language: Optional target language for translation.
        lang: Deprecated alias for translation_language.
        units: Dictionary of unit preferences (e.g., {"temperature_unit": "celsius"}).
    """
    name: str
    lang: Optional[str] = None
    translation_language: Optional[str] = None
    translation_lang: Optional[str] = Field(default=None, exclude=True)
    units: Dict[str, str] = Field(default_factory=dict)
    snow_levels: Optional[bool] = None
    model: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_translation_aliases(cls, data: Any):
        if not isinstance(data, dict):
            return data
        if not data.get("translation_language"):
            legacy = data.get("translation_lang") or data.get("lang")
            if legacy:
                data = dict(data)
                data["translation_language"] = legacy
        return data


class AreaConfig(BaseModel):
    """
    Configuration for an aggregated area forecast.

    Attributes:
        name: Display name for the area.
        locations: List of location names that comprise the area.
        translation_language: Optional target language.
        lang: Deprecated alias for translation_language.
        mode: "area" (summary) or "regional" (breakdown).
        units: Dictionary of unit preferences.
    """
    name: str
    locations: List[str]
    lang: Optional[str] = None
    translation_language: Optional[str] = None
    translation_lang: Optional[str] = Field(default=None, exclude=True)
    mode: Literal["area", "regional"] = "area"
    units: Dict[str, str] = Field(default_factory=dict)
    snow_levels: Optional[bool] = None
    model: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_translation_aliases(cls, data: Any):
        if not isinstance(data, dict):
            return data
        if not data.get("translation_language"):
            legacy = data.get("translation_lang") or data.get("lang")
            if legacy:
                data = dict(data)
                data["translation_language"] = legacy
        return data


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
        llm: LLM model identifier (e.g., "gpt-4o-mini").
        context_llm: LLM model identifier to use for impact-context web search (default "gpt-4o").
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
    translation_lang: Optional[str] = Field(default=None, exclude=True)
    translation_llm: Optional[str] = None
    recent_overwrite_minutes: int = 0
    snow_levels: bool = False
    # Global default forecast model. This name matches the per-location/per-area override field.
    # Backwards-compat: configs may still provide "ensemble_model"; it will be mapped into "model".
    model: Optional[str] = None
    ensemble_model: Optional[str] = Field(default=None, exclude=True)

    model_config = {
        "arbitrary_types_allowed": True,
        "populate_by_name": True,
    }

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_fields(cls, data: Any):
        if not isinstance(data, dict):
            return data
        if not data.get("model") and data.get("ensemble_model"):
            data = dict(data)
            data["model"] = data.get("ensemble_model")
        if not data.get("translation_language") and data.get("translation_lang"):
            data = dict(data)
            data["translation_language"] = data.get("translation_lang")
        return data

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
    Load and validate a config file into a ForecastConfig instance.

    Args:
        path: Path to the JSON configuration file.

    Returns:
        A validated ForecastConfig object.

    Raises:
        ConfigError: If the file is missing, unreadable, or invalid.
    """
    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        raise ConfigError(f"Configuration file not found: {config_path}")

    try:
        with config_path.open("r", encoding="utf-8") as handle:
            raw_data: Dict[str, Any] = json.load(handle)
    except OSError as exc:
        raise ConfigError(f"Unable to read configuration file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in configuration file: {exc}") from exc

    try:
        return ForecastConfig.model_validate(raw_data)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc
