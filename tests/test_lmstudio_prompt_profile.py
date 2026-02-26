from ibf.llm.settings import LLMSettings
from ibf.pipeline.executor import _apply_provider_prompt_profile


def test_prompt_profile_unchanged_for_non_lmstudio() -> None:
    base = "Base system prompt."
    settings = LLMSettings(model="gpt-4o-mini", api_key="x", provider="openai")

    result = _apply_provider_prompt_profile(base, settings)

    assert result == base


def test_prompt_profile_adds_lmstudio_rules() -> None:
    base = "Base system prompt."
    settings = LLMSettings(model="qwen3.5", api_key="x", provider="lmstudio")

    result = _apply_provider_prompt_profile(base, settings)

    assert result.startswith(base)
    assert "Use digits for all numeric values" in result
    assert "Do not spell numbers out in words" in result
