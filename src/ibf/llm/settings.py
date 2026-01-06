"""
Helpers to determine which LLM/provider to use based on config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from ..config import ForecastConfig


@dataclass
class LLMSettings:
    """
    Configuration for an LLM provider.

    Attributes:
        model: Model identifier (e.g., "gpt-4o-mini").
        api_key: API key for authentication.
        provider: "openai", "openrouter", or "gemini".
        base_url: Optional custom API base URL.
        is_google: True if using the Google Generative AI SDK.
        temperature: Sampling temperature.
        max_tokens: Maximum output tokens.
    """
    model: str
    api_key: str
    provider: str
    base_url: Optional[str] = None
    is_google: bool = False
    temperature: float = 0.2
    max_tokens: int = 8000


def resolve_llm_settings(config: ForecastConfig, override_choice: Optional[str] = None) -> LLMSettings:
    """
    Inspect the forecast config and environment variables to determine which LLM to use.

    Prioritizes `override_choice`, then `config.llm`, then `IBF_DEFAULT_LLM` env var,
    and finally defaults to a specific OpenRouter model.

    Args:
        config: The forecast configuration.
        override_choice: Optional model string to force (e.g., for translation).

    Returns:
        An LLMSettings object.

    Raises:
        RuntimeError: If a required API key is missing.
    """
    base_choice = (
        override_choice
        or config.llm
        or os.environ.get("IBF_DEFAULT_LLM")
        or "gemini-3-flash-preview"
    )
    choice = base_choice.strip()
    choice_lower = choice.lower()

    # Direct Google Gemini SDK:
    # - allow "gemini-*" (native Gemini model names)
    # - also accept OpenRouter-style "google/gemini-*" and map it down to "gemini-*"
    if choice_lower.startswith("gemini-") or choice_lower.startswith("google/gemini-"):
        model_name = choice
        if choice_lower.startswith("google/gemini-"):
            model_name = choice.split("/", 1)[1]
        api_key = _require_env("GEMINI_API_KEY")
        return LLMSettings(
            model=model_name,
            api_key=api_key,
            provider="gemini",
            is_google=True,
            max_tokens=10000,
        )

    if choice_lower == "gpt-4o-mini":
        api_key = _require_env("OPENAI_API_KEY")
        return LLMSettings(
            model="gpt-4o-mini",
            api_key=api_key,
            provider="openai",
            max_tokens=6000,
        )

    if choice_lower.startswith("or:"):
        api_key = _require_env("OPENROUTER_API_KEY")
        model_name = choice[3:]
        return LLMSettings(
            model=model_name,
            api_key=api_key,
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
        )

    if choice_lower == "gpt-4o-latest":
        api_key = _require_env("OPENAI_API_KEY")
        return LLMSettings(
            model="gpt-4o-latest",
            api_key=api_key,
            provider="openai",
        )

    if choice_lower.startswith("gpt-") or (choice_lower.startswith("o") and len(choice_lower) > 1 and choice_lower[1].isdigit()):
        api_key = _require_env("OPENAI_API_KEY")
        return LLMSettings(
            model=choice,
            api_key=api_key,
            provider="openai",
        )

    raise RuntimeError(
        f"Unknown LLM '{choice}'. Use gemini-* for Gemini, gpt-*/o* for OpenAI, or prefix OpenRouter models with 'or:'."
    )


def _require_env(name: str) -> str:
    """Fetch an environment variable or raise a descriptive error."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is required for the selected LLM.")
    return value
