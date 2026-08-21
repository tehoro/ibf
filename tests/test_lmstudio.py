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


def test_lms_settings_preserve_compact_prompt_profile() -> None:
    settings = resolve_llm_settings(
        ForecastConfig(
            llm="lms:qwen3.8-27b-mlx",
            prompt_profile="compact",
        )
    )

    assert settings.prompt_profile == "compact"


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


@pytest.mark.parametrize(
    ("model_id", "expected_user_prompt"),
    [
        ("Qwen3-30B-A3B-MLX", "prompt"),
        ("lmstudio-community/Qwen3.5-27B-MLX-6bit", "prompt"),
        ("qwen3.8-27b-mlx", "prompt\n\n/no_think"),
    ],
)
def test_lm_studio_qwen3_family_disables_thinking_in_request_payload(
    monkeypatch,
    caplog,
    model_id,
    expected_user_prompt,
) -> None:
    _validate_lm_studio_model.cache_clear()
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Forecast text"),
                    finish_reason="stop",
                )
            ],
        )

    fake_client = SimpleNamespace(
        models=SimpleNamespace(
            list=lambda: SimpleNamespace(data=[SimpleNamespace(id=model_id)])
        ),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    monkeypatch.setattr("ibf.llm.client.OpenAI", lambda **kwargs: fake_client)
    settings = LLMSettings(
        model=model_id,
        api_key="lm-studio",
        provider="lmstudio",
        base_url="http://localhost:1234/v1",
        max_tokens=8000,
        prompt_profile="compact",
    )

    with caplog.at_level("INFO", logger="ibf.llm.client"):
        text = generate_forecast_text("prompt", "system", settings)

    assert text == "Forecast text"
    assert captured["max_tokens"] == 8000
    assert captured["extra_body"] == {
        "chat_template_kwargs": {
            "enable_thinking": False,
            "preserve_thinking": False,
        }
    }
    assert captured["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": expected_user_prompt},
    ]
    assert (
        f"LM Studio reasoning mode – model={model_id} "
        "enable_thinking=false preserve_thinking=false"
    ) in caplog.text
    if "qwen3.8" in model_id.lower():
        assert (
            f"LM Studio Qwen no-think safeguard – model={model_id} "
            "appended_no_think=true"
        ) in caplog.text
    else:
        assert "appended_no_think=true" not in caplog.text


def test_standard_profile_qwen3_has_no_thinking_override(monkeypatch) -> None:
    _validate_lm_studio_model.cache_clear()
    captured = {}
    model_id = "qwen3.8-27b-mlx"

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Forecast text"),
                    finish_reason="stop",
                )
            ],
        )

    fake_client = SimpleNamespace(
        models=SimpleNamespace(
            list=lambda: SimpleNamespace(data=[SimpleNamespace(id=model_id)])
        ),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    monkeypatch.setattr("ibf.llm.client.OpenAI", lambda **kwargs: fake_client)
    settings = LLMSettings(
        model=model_id,
        api_key="lm-studio",
        provider="lmstudio",
        base_url="http://localhost:1234/v1",
        prompt_profile="standard",
    )

    assert generate_forecast_text("prompt", "system", settings) == "Forecast text"
    assert "extra_body" not in captured
    assert captured["messages"][1]["content"] == "prompt"


def test_non_qwen_lm_studio_model_has_no_thinking_override(monkeypatch) -> None:
    _validate_lm_studio_model.cache_clear()
    captured = {}
    model_id = "gemma-4-26b-a4b-it-mlx"

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Forecast text"),
                    finish_reason="stop",
                )
            ],
        )

    fake_client = SimpleNamespace(
        models=SimpleNamespace(
            list=lambda: SimpleNamespace(data=[SimpleNamespace(id=model_id)])
        ),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    monkeypatch.setattr("ibf.llm.client.OpenAI", lambda **kwargs: fake_client)
    settings = LLMSettings(
        model=model_id,
        api_key="lm-studio",
        provider="lmstudio",
        base_url="http://localhost:1234/v1",
        prompt_profile="compact",
    )

    assert generate_forecast_text("prompt", "system", settings) == "Forecast text"
    assert "extra_body" not in captured
    assert captured["messages"][1]["content"] == "prompt"


def test_cloud_qwen_model_has_no_lm_studio_thinking_override(monkeypatch) -> None:
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Forecast text"),
                    finish_reason="stop",
                )
            ],
        )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr("ibf.llm.client.OpenAI", lambda **kwargs: fake_client)
    settings = LLMSettings(
        model="qwen/qwen3.8-27b",
        api_key="cloud-key",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        prompt_profile="compact",
    )

    assert generate_forecast_text("prompt", "system", settings) == "Forecast text"
    assert "extra_body" not in captured
    assert captured["messages"][1]["content"] == "prompt"


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
