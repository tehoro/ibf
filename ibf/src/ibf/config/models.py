"""
Pydantic models for validating and hashing forecast configuration files.
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field, ValidationError


class ConfigError(RuntimeError):
    """Raised when configuration files cannot be loaded or validated."""


class LocationConfig(BaseModel):
    name: str
    lang: Optional[str] = None
    translation_language: Optional[str] = None
    units: Dict[str, str] = Field(default_factory=dict)


class AreaConfig(BaseModel):
    name: str
    locations: List[str]
    lang: Optional[str] = None
    translation_language: Optional[str] = None
    mode: Literal["area", "regional"] = "area"
    units: Dict[str, str] = Field(default_factory=dict)


class ForecastConfig(BaseModel):
    locations: List[LocationConfig] = Field(default_factory=list)
    areas: List[AreaConfig] = Field(default_factory=list)
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
    translation_language: Optional[str] = None
    translation_llm: Optional[str] = None
    recent_overwrite_minutes: int = 0

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
    Load and validate a config file into a ForecastConfig instance.
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

