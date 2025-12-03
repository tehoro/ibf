"""
LLM utilities: prompt generation, formatting, and client wrappers.
"""

from .settings import LLMSettings, resolve_llm_settings
from .client import generate_forecast_text, consume_last_cost_cents
from .formatter import (
    format_location_dataset,
    format_area_dataset,
    determine_current_season,
)
from .prompts import (
    build_spot_system_prompt,
    build_spot_user_prompt,
    build_area_system_prompt,
    build_area_user_prompt,
    build_regional_system_prompt,
    build_regional_user_prompt,
    build_translation_system_prompt,
    build_translation_user_prompt,
)

__all__ = [
    "LLMSettings",
    "resolve_llm_settings",
    "generate_forecast_text",
    "consume_last_cost_cents",
    "format_location_dataset",
    "format_area_dataset",
    "determine_current_season",
    "build_spot_system_prompt",
    "build_spot_user_prompt",
    "build_area_system_prompt",
    "build_area_user_prompt",
    "build_regional_system_prompt",
    "build_regional_user_prompt",
    "build_translation_system_prompt",
    "build_translation_user_prompt",
]

