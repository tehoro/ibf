"""
LLM utilities: prompt generation, formatting, and client wrappers.
"""

from .settings import DEFAULT_LLM, LLMSettings, resolve_llm_settings
from .client import generate_forecast_text, consume_last_cost_cents
from .formatter import (
    format_location_dataset,
    format_area_dataset,
    determine_current_season,
)
from .compliance import (
    build_spot_correction_prompts,
    correction_preserves_other_numeric_facts,
    format_spot_output_contract,
    parse_spot_output_requirements,
    postprocess_compact_spot_output,
    validate_spot_forecast,
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
    compact_wind_thresholds,
)

__all__ = [
    "LLMSettings",
    "DEFAULT_LLM",
    "resolve_llm_settings",
    "generate_forecast_text",
    "consume_last_cost_cents",
    "format_location_dataset",
    "format_area_dataset",
    "determine_current_season",
    "build_spot_correction_prompts",
    "correction_preserves_other_numeric_facts",
    "format_spot_output_contract",
    "parse_spot_output_requirements",
    "postprocess_compact_spot_output",
    "validate_spot_forecast",
    "build_spot_system_prompt",
    "build_spot_user_prompt",
    "build_area_system_prompt",
    "build_area_user_prompt",
    "build_regional_system_prompt",
    "build_regional_user_prompt",
    "build_translation_system_prompt",
    "build_translation_user_prompt",
    "compact_wind_thresholds",
]
