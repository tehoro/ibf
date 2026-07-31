from __future__ import annotations

import logging
from types import SimpleNamespace

from ibf.config.models import ForecastConfig
from ibf.llm.client import _call_openai_compatible
from ibf.llm.settings import LLMSettings, resolve_llm_settings
from ibf.pipeline.executor import (
    _gemini_thinking_level,
    _log_cost_summary,
    _reasoning_payload,
    _record_cost,
    _reset_cost_tracker,
)


def test_default_llm_is_direct_luna(monkeypatch) -> None:
    monkeypatch.delenv("IBF_DEFAULT_LLM", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    settings = resolve_llm_settings(ForecastConfig())

    assert settings.model == "gpt-5.6-luna"
    assert settings.provider == "openai"


def test_direct_luna_uses_gpt5_chat_parameters(monkeypatch) -> None:
    captured = {}
    response = SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="Forecast text"),
                finish_reason="stop",
            )
        ],
    )

    def create(**kwargs):
        captured.update(kwargs)
        return response

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr("ibf.llm.client.OpenAI", lambda **kwargs: fake_client)
    settings = LLMSettings(
        model="gpt-5.6-luna",
        api_key="test-key",
        provider="openai",
    )

    text = _call_openai_compatible(
        "user",
        "system",
        settings,
        reasoning={"reasoning": {"effort": "high"}},
    )

    assert text == "Forecast text"
    assert captured["max_completion_tokens"] == settings.max_tokens
    assert captured["reasoning_effort"] == "high"
    assert "max_tokens" not in captured
    assert "temperature" not in captured
    assert "extra_body" not in captured


def test_enabled_reasoning_defaults_high_but_explicit_values_win() -> None:
    assert _reasoning_payload(True, None) == {"reasoning": {"effort": "high"}}
    assert _reasoning_payload(True, "low") == {"reasoning": {"effort": "low"}}
    assert _reasoning_payload(False, "high") is None
    assert _gemini_thinking_level(True, None) == "high"
    assert _gemini_thinking_level(True, "auto") is None
    assert _gemini_thinking_level(False, "high") == "minimal"


def test_cost_summary_logs_two_decimal_places(caplog) -> None:
    _reset_cost_tracker()
    _record_cost(
        "Location",
        "Test City",
        context=0.126,
        forecast=0.234,
        translation=0.345,
    )

    with caplog.at_level(logging.INFO, logger="ibf.pipeline.executor"):
        _log_cost_summary()

    assert "0.13" in caplog.text
    assert "0.23" in caplog.text
    assert "0.34" in caplog.text
    assert "Grand total" in caplog.text
