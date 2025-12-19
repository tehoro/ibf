"""
Centralised, user-editable LLM pricing table.

Each entry records the USD cost per 1M tokens for:
- standard input tokens
- cached input tokens (OpenAI prompt caching discount)
- output tokens

Edit `MODEL_COSTS` directly to customise or add new models.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class ModelCost:
    """Price information for one model (USD per 1M tokens)."""

    input_per_million: float
    cached_input_per_million: float
    output_per_million: float

    def cost_for_usage(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> float:
        """
        Estimate the USD cost for a single call.

        Args:
            input_tokens: Total input tokens billed at the standard rate.
            output_tokens: Total output tokens.
            cached_input_tokens: Portion of input tokens served from cache.

        Returns:
            Estimated USD charge for the call.
        """
        standard_input = max(input_tokens - cached_input_tokens, 0)
        return (
            (standard_input / 1_000_000) * self.input_per_million
            + (cached_input_tokens / 1_000_000) * self.cached_input_per_million
            + (output_tokens / 1_000_000) * self.output_per_million
        )


# NOTE: Keep this mapping simple so users can edit it without digging through code.
MODEL_COSTS: Dict[str, ModelCost] = {
    # Pricing reference (December 2025): https://platform.openai.com/docs/pricing
    "gpt-4o-mini": ModelCost(
        input_per_million=0.15,
        cached_input_per_million=0.075,
        output_per_million=0.60,
    ),
    "gpt-4.1-mini": ModelCost(
        input_per_million=0.15,
        cached_input_per_million=0.075,
        output_per_million=0.60,
    ),
    "gpt-4o": ModelCost(
        input_per_million=2.50,
        cached_input_per_million=1.25,
        output_per_million=10.00,
    ),
    "openai/gpt-5.1": ModelCost(
        input_per_million=1.25,
        cached_input_per_million=0.125,
        output_per_million=10.00,
    ),
    "openai/gpt-5-mini": ModelCost(
        input_per_million=0.25,
        cached_input_per_million=0.025,
        output_per_million=2.00,
    ),
    "gemini-2.5-flash": ModelCost(
        input_per_million=0.30,
        cached_input_per_million=0.03,
        output_per_million=2.50,
    ),
    "google/gemini-2.5-flash": ModelCost(
        input_per_million=0.30,
        cached_input_per_million=0.03,
        output_per_million=2.50,
    ),
    # Gemini 3 Flash (preview) pricing (USD per 1M tokens).
    # Provided by user: $0.50 input, $0.35 cached input, $3.00 output.
    "gemini-3-flash-preview": ModelCost(
        input_per_million=0.50,
        cached_input_per_million=0.35,
        output_per_million=3.00,
    ),
    "google/gemini-3-flash-preview": ModelCost(
        input_per_million=0.50,
        cached_input_per_million=0.35,
        output_per_million=3.00,
    ),
    "or:deepseek/deepseek-v3.2": ModelCost(
        input_per_million=0.27,
        cached_input_per_million=0.22,
        output_per_million=0.40,
    ),
}


def get_model_cost(model_name: str, *, registry: Optional[Dict[str, ModelCost]] = None) -> Optional[ModelCost]:
    """
    Return the ModelCost entry for the given identifier, if available.

    Args:
        model_name: Exact model key to look up (case-sensitive).
        registry: Optional override mapping; defaults to MODEL_COSTS.
    """
    lookup = registry or MODEL_COSTS
    return lookup.get(model_name)
