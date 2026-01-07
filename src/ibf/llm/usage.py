"""
Shared usage logging helpers for LLM calls.
"""

from __future__ import annotations

import logging
from typing import Any

from .costs import get_model_cost

logger = logging.getLogger(__name__)


def log_gemini_usage_and_cost(model_name: str, usage_metadata: Any, *, label: str = "LLM usage") -> float:
    """Log Gemini usage and return estimated cost in USD cents."""
    if not usage_metadata:
        logger.info(
            "%s – model=%s prompt_tokens=%s cached_prompt_tokens=%s completion_tokens=%s total_tokens=%s cost_usd_cents=%s",
            label,
            model_name,
            "n/a",
            "n/a",
            "n/a",
            "n/a",
            "n/a",
        )
        return 0.0

    def _get(obj: Any, key: str) -> Any:
        """Return attribute or dict value when present, else None."""
        if hasattr(obj, key):
            return getattr(obj, key)
        if isinstance(obj, dict):
            return obj.get(key)
        return None

    prompt_tokens = _get(usage_metadata, "prompt_token_count")
    completion_tokens = _get(usage_metadata, "candidates_token_count")
    total_tokens = _get(usage_metadata, "total_token_count")
    try:
        prompt_tokens_i = int(prompt_tokens or 0)
        completion_tokens_i = int(completion_tokens or 0)
        total_tokens_i = int(total_tokens or (prompt_tokens_i + completion_tokens_i))
    except (TypeError, ValueError):
        prompt_tokens_i, completion_tokens_i, total_tokens_i = 0, 0, 0

    cost_entry = get_model_cost(model_name)
    cost_display = "n/a"
    cost_cents = 0.0
    if cost_entry:
        usd = cost_entry.cost_for_usage(
            input_tokens=prompt_tokens_i,
            output_tokens=completion_tokens_i,
            cached_input_tokens=0,
        )
        cost_cents = usd * 100
        cost_display = f"{cost_cents:.2f}"

    logger.info(
        "%s – model=%s prompt_tokens=%s cached_prompt_tokens=%s completion_tokens=%s total_tokens=%s cost_usd_cents=%s",
        label,
        model_name,
        prompt_tokens_i,
        0,
        completion_tokens_i,
        total_tokens_i,
        cost_display,
    )
    return cost_cents


def log_openai_usage_and_cost(model_name: str, usage: Any, *, label: str = "LLM usage") -> float:
    """Log OpenAI-compatible usage and return estimated cost in USD cents."""
    if not usage:
        logger.info(
            "%s – model=%s prompt_tokens=%s cached_prompt_tokens=%s completion_tokens=%s total_tokens=%s cost_usd_cents=%s",
            label,
            model_name,
            "n/a",
            "n/a",
            "n/a",
            "n/a",
            "n/a",
        )
        return 0.0

    try:
        prompt_tokens, cached_prompt_tokens, completion_tokens, total_tokens = _normalize_openai_usage(usage)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:  # pragma: no cover - defensive
        logger.debug("Unable to normalize LLM usage data (%s): %s", type(usage), exc)
        logger.info(
            "%s – model=%s prompt_tokens=%s cached_prompt_tokens=%s completion_tokens=%s total_tokens=%s cost_usd_cents=%s",
            label,
            model_name,
            getattr(usage, "prompt_tokens", getattr(usage, "input_tokens", "n/a")),
            "n/a",
            getattr(usage, "completion_tokens", getattr(usage, "output_tokens", "n/a")),
            getattr(usage, "total_tokens", "n/a"),
            "n/a",
        )
        return 0.0

    cost_entry = get_model_cost(model_name)
    cost_display = "n/a"
    cost_cents = 0.0
    if cost_entry:
        usd = cost_entry.cost_for_usage(
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            cached_input_tokens=cached_prompt_tokens,
        )
        cost_cents = usd * 100
        cost_display = f"{cost_cents:.2f}"

    logger.info(
        "%s – model=%s prompt_tokens=%s cached_prompt_tokens=%s completion_tokens=%s total_tokens=%s cost_usd_cents=%s",
        label,
        model_name,
        prompt_tokens,
        cached_prompt_tokens,
        completion_tokens,
        total_tokens,
        cost_display,
    )
    return cost_cents


def _normalize_openai_usage(usage: Any) -> tuple[int, int, int, int]:
    """Return (prompt_tokens, cached_prompt_tokens, completion_tokens, total_tokens)."""

    def _get_attr(obj: Any, attr: str) -> Any:
        """Return attribute or dict value when present, else None."""
        if hasattr(obj, attr):
            return getattr(obj, attr)
        if isinstance(obj, dict):
            return obj.get(attr)
        return None

    input_tokens = _get_attr(usage, "input_tokens")
    if input_tokens is not None:
        cached = _get_attr(_get_attr(usage, "input_tokens_details") or {}, "cached_tokens") or 0
        output_tokens = _get_attr(usage, "output_tokens") or 0
        total_tokens = _get_attr(usage, "total_tokens") or (int(input_tokens) + int(output_tokens))
        return int(input_tokens), int(cached), int(output_tokens), int(total_tokens)

    prompt_tokens = _get_attr(usage, "prompt_tokens")
    if prompt_tokens is not None:
        cached = _get_attr(_get_attr(usage, "prompt_tokens_details") or {}, "cached_tokens") or 0
        completion_tokens = _get_attr(usage, "completion_tokens") or 0
        total_tokens = _get_attr(usage, "total_tokens") or (int(prompt_tokens) + int(completion_tokens))
        return int(prompt_tokens), int(cached), int(completion_tokens), int(total_tokens)

    raise ValueError("Unsupported usage payload structure")
