from __future__ import annotations

import logging

import pytest

from ibf.config.models import ForecastConfig
from ibf.llm.settings import LLMSettings
from ibf.pipeline.executor import _generate_text_with_fallback


def test_primary_llm_failure_uses_configured_fallback(monkeypatch, caplog) -> None:
    config = ForecastConfig(
        llm="lms:primary-model",
        llm_fallback="gemini-3-flash-preview",
    )
    primary = LLMSettings(
        model="primary-model",
        api_key="local",
        provider="lmstudio",
        base_url="http://localhost:1234/v1",
    )
    fallback = LLMSettings(
        model="gemini-3-flash-preview",
        api_key="cloud",
        provider="gemini",
        is_google=True,
    )

    def fake_resolve(_config, choice):
        return primary if choice == "lms:primary-model" else fallback

    calls = []

    def fake_generate(prompt, system_prompt, settings, **kwargs):
        calls.append(settings.model)
        if settings is primary:
            raise RuntimeError("model is unavailable")
        return "Fallback forecast"

    monkeypatch.setattr("ibf.pipeline.executor.resolve_llm_settings", fake_resolve)
    monkeypatch.setattr("ibf.pipeline.executor.generate_forecast_text", fake_generate)
    monkeypatch.setattr("ibf.pipeline.executor.consume_last_cost_cents", lambda: 0.0)
    monkeypatch.setattr("ibf.pipeline.executor._snapshot_prompt", lambda *args, **kwargs: None)

    with caplog.at_level(logging.ERROR):
        text, settings, cost = _generate_text_with_fallback(
            config,
            "prompt",
            "system",
            primary_choice=config.llm,
            fallback_choice=config.llm_fallback,
            snapshot_kind="test",
            snapshot_name="test",
            operation_label="test forecast",
        )

    assert text == "Fallback forecast"
    assert settings is fallback
    assert cost == 0.0
    assert calls == ["primary-model", "gemini-3-flash-preview"]
    assert "PRIMARY LLM FAILURE" in caplog.text


def test_fallback_does_not_repeat_already_resolved_primary(monkeypatch) -> None:
    config = ForecastConfig(
        llm="lms:primary-model",
        translation_llm_fallback="gemini-3-flash-preview",
    )
    already_resolved = LLMSettings(
        model="gemini-3-flash-preview",
        api_key="cloud",
        provider="gemini",
        is_google=True,
    )
    calls = []

    def fail_generate(*args, **kwargs):
        calls.append("called")
        raise RuntimeError("unavailable")

    monkeypatch.setattr("ibf.pipeline.executor.generate_forecast_text", fail_generate)
    monkeypatch.setattr("ibf.pipeline.executor.consume_last_cost_cents", lambda: 0.0)
    monkeypatch.setattr("ibf.pipeline.executor._snapshot_prompt", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="All LLM attempts failed"):
        _generate_text_with_fallback(
            config,
            "prompt",
            "system",
            primary_choice=None,
            primary_settings=already_resolved,
            fallback_choice=config.translation_llm_fallback,
            snapshot_kind="test",
            snapshot_name="test",
            operation_label="test translation",
        )

    assert calls == ["called"]
