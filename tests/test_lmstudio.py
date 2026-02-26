from types import SimpleNamespace

import pytest

from ibf.config import ForecastConfig
from ibf.llm.client import consume_last_cost_cents, generate_forecast_text
import ibf.llm.client as llm_client
from ibf.llm.settings import LM_STUDIO_DEFAULT_BASE_URL, LLMSettings, resolve_llm_settings


def _config(llm: str, *, lm_studio_base_url: str | None = None) -> ForecastConfig:
    payload = {
        "llm": llm,
        "locations": [{"name": "Test City"}],
    }
    if lm_studio_base_url is not None:
        payload["lm_studio_base_url"] = lm_studio_base_url
    return ForecastConfig.model_validate(payload)


def test_resolve_lmstudio_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LM_STUDIO_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("LM_STUDIO_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    settings = resolve_llm_settings(_config("lm:qwen3.5"))

    assert settings.provider == "lmstudio"
    assert settings.model == "qwen3.5"
    assert settings.base_url == LM_STUDIO_DEFAULT_BASE_URL
    assert settings.api_key == "lm-studio"


def test_resolve_lmstudio_with_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LM_STUDIO_BASE_URL", "http://192.168.1.50:1234/v1")
    monkeypatch.setenv("LM_STUDIO_API_KEY", "secret")

    settings = resolve_llm_settings(_config("lm:qwen3.5"))

    assert settings.provider == "lmstudio"
    assert settings.base_url == "http://192.168.1.50:1234/v1"
    assert settings.api_key == "secret"


def test_resolve_lmstudio_uses_config_url_and_normalizes_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LM_STUDIO_BASE_URL", "http://192.168.1.50:1234/v1")

    settings = resolve_llm_settings(_config("lm:qwen3.5", lm_studio_base_url="http://192.168.1.79:1234"))

    assert settings.provider == "lmstudio"
    assert settings.base_url == "http://192.168.1.79:1234/v1"


def test_rejects_empty_lmstudio_model() -> None:
    with pytest.raises(RuntimeError, match="lm:<model_name>"):
        resolve_llm_settings(_config("lm:"))


def test_lmstudio_calls_report_zero_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeCompletions:
        def create(self, **kwargs):  # noqa: ANN003
            message = SimpleNamespace(content="Local model reply")
            choice = SimpleNamespace(message=message, finish_reason="stop")
            usage = {
                "prompt_tokens": 2000,
                "completion_tokens": 1000,
                "total_tokens": 3000,
            }
            return SimpleNamespace(choices=[choice], usage=usage)

    class _FakeOpenAI:
        def __init__(self, **kwargs):  # noqa: ANN003
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    monkeypatch.setattr(llm_client, "OpenAI", _FakeOpenAI)

    # Reset any previous carry-over from earlier tests.
    consume_last_cost_cents()
    settings = LLMSettings(
        model="gpt-4o-mini",
        api_key="lm-studio",
        provider="lmstudio",
        base_url="http://localhost:1234/v1",
    )
    text = generate_forecast_text("Weather prompt", "System prompt", settings)

    assert text == "Local model reply"
    assert consume_last_cost_cents() == 0.0
