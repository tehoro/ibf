from __future__ import annotations

import pytest

from ibf.llm.client import _build_gemini_config
from ibf.llm.settings import LLMSettings


class _FakeGenerateContentConfig:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class _FakeTypes:
    GenerateContentConfig = _FakeGenerateContentConfig


def _settings(model: str) -> LLMSettings:
    return LLMSettings(
        model=model,
        api_key="test-key",
        provider="gemini",
        is_google=True,
    )


@pytest.mark.parametrize(
    "model",
    ["gemini-3.5-flash-lite", "gemini-3.7-flash", "google/gemini-3.7-flash"],
)
def test_recent_gemini_flash_models_omit_deprecated_sampling_parameters(
    model: str,
) -> None:
    config = _build_gemini_config(
        _FakeTypes,
        "system",
        _settings(model),
    )

    assert "temperature" not in config.kwargs
    assert config.kwargs["max_output_tokens"] == 8000


def test_legacy_gemini_models_keep_temperature() -> None:
    config = _build_gemini_config(
        _FakeTypes,
        "system",
        _settings("gemini-3-flash-preview"),
    )

    assert config.kwargs["temperature"] == 0.2
