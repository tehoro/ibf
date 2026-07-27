from __future__ import annotations

from types import SimpleNamespace

import pytest

from ibf.llm.usage import log_gemini_usage_and_cost, log_openai_usage_and_cost


def test_openrouter_reported_cost_is_preferred_over_baked_estimate() -> None:
    usage = {
        "prompt_tokens": 1_000_000,
        "completion_tokens": 1_000_000,
        "total_tokens": 2_000_000,
        "cost": 0.1234,
    }

    cents = log_openai_usage_and_cost(
        "gpt-4o",
        usage,
        provider="openrouter",
    )

    assert cents == pytest.approx(12.34)


def test_direct_provider_uses_updated_estimate_when_cost_is_not_returned() -> None:
    usage = {
        "prompt_tokens": 1_000_000,
        "completion_tokens": 1_000_000,
        "total_tokens": 2_000_000,
    }

    cents = log_openai_usage_and_cost(
        "gpt-4.1-mini",
        usage,
        provider="openai",
    )

    assert cents == pytest.approx(200.0)


def test_gemini_estimate_accounts_for_cached_input_tokens() -> None:
    usage = SimpleNamespace(
        prompt_token_count=1_000_000,
        cached_content_token_count=500_000,
        candidates_token_count=0,
        total_token_count=1_000_000,
    )

    cents = log_gemini_usage_and_cost("gemini-3-flash-preview", usage)

    # 500k standard input at $0.50/M plus 500k cached input at $0.05/M.
    assert cents == pytest.approx(27.5)


def test_gemini_35_flash_lite_uses_current_standard_price() -> None:
    usage = SimpleNamespace(
        prompt_token_count=1_000_000,
        cached_content_token_count=0,
        candidates_token_count=1_000_000,
        total_token_count=2_000_000,
    )

    cents = log_gemini_usage_and_cost("gemini-3.5-flash-lite", usage)

    assert cents == pytest.approx(280.0)
