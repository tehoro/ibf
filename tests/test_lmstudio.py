from __future__ import annotations

from types import SimpleNamespace

import pytest

from ibf.config.models import ForecastConfig
from ibf.llm.client import _validate_lm_studio_model, generate_forecast_text
from ibf.llm.settings import LLMSettings, resolve_llm_settings


def test_lms_settings_preserve_exact_model_id_and_normalize_network_url(monkeypatch) -> None:
    monkeypatch.setenv("LM_STUDIO_API_KEY", "local-token")
    config = ForecastConfig(
        llm="lms:gemma-4-26b-a4b-it-mlx",
        lm_studio_base_url="192.168.1.79:1234",
    )

    settings = resolve_llm_settings(config)

    assert settings.provider == "lmstudio"
    assert settings.model == "gemma-4-26b-a4b-it-mlx"
    assert settings.base_url == "http://192.168.1.79:1234/v1"
    assert settings.api_key == "local-token"
    assert settings.timeout_seconds == 3600.0


def test_lms_settings_use_environment_url_when_config_omits_it(monkeypatch) -> None:
    monkeypatch.setenv("LM_STUDIO_BASE_URL", "http://lm-host.local:1234/v1/")
    settings = resolve_llm_settings(ForecastConfig(llm="lms:weather-writer"))

    assert settings.base_url == "http://lm-host.local:1234/v1"


def test_lms_rejects_blank_model_id() -> None:
    with pytest.raises(RuntimeError, match="non-empty model id"):
        resolve_llm_settings(ForecastConfig(llm="lms:"))


def test_lm_studio_checks_exact_model_before_generation(monkeypatch) -> None:
    _validate_lm_studio_model.cache_clear()
    fake_client = SimpleNamespace(
        models=SimpleNamespace(
            list=lambda: SimpleNamespace(
                data=[SimpleNamespace(id="other-model"), SimpleNamespace(id="weather-writer")]
            )
        ),
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content="Forecast text"),
                            finish_reason="stop",
                        )
                    ],
                )
            )
        ),
    )
    monkeypatch.setattr("ibf.llm.client.OpenAI", lambda **kwargs: fake_client)
    settings = LLMSettings(
        model="weather-writer",
        api_key="lm-studio",
        provider="lmstudio",
        base_url="http://localhost:1234/v1",
    )

    assert generate_forecast_text("prompt", "system", settings) == "Forecast text"


def test_lm_studio_missing_model_error_lists_visible_models(monkeypatch) -> None:
    _validate_lm_studio_model.cache_clear()
    fake_client = SimpleNamespace(
        models=SimpleNamespace(
            list=lambda: SimpleNamespace(
                data=[SimpleNamespace(id="model-a"), SimpleNamespace(id="model-b")]
            )
        )
    )
    monkeypatch.setattr("ibf.llm.client.OpenAI", lambda **kwargs: fake_client)
    settings = LLMSettings(
        model="missing-model",
        api_key="lm-studio",
        provider="lmstudio",
        base_url="http://lm-host:1234/v1",
    )

    with pytest.raises(RuntimeError, match="missing-model") as exc_info:
        generate_forecast_text("prompt", "system", settings)

    assert "model-a" in str(exc_info.value)
    assert "model-b" in str(exc_info.value)
    assert "http://lm-host:1234/v1" in str(exc_info.value)
