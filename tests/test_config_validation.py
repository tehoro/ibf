from pathlib import Path
import textwrap
import logging

import pytest

from ibf.config import ConfigError, load_config


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")
    return path


def test_rejects_unknown_keys(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        web_root = "./outputs"
        unexpected = "nope"

        [[location]]
        name = "Test City"
        """,
    )

    with pytest.raises(ConfigError) as exc:
        load_config(path)

    assert "extra" in str(exc.value).lower()


def test_rejects_invalid_units(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        temperature_unit = "kelvin"

        [[location]]
        name = "Test City"
        """,
    )

    with pytest.raises(ConfigError) as exc:
        load_config(path)

    assert "temperature_unit" in str(exc.value)


def test_prompt_profile_defaults_to_standard_and_accepts_compact(tmp_path: Path) -> None:
    standard = load_config(
        _write_config(
            tmp_path,
            """
            [[location]]
            name = "Test City"
            """,
        )
    )
    compact = load_config(
        _write_config(
            tmp_path,
            """
            prompt_profile = "compact"

            [[location]]
            name = "Test City"
            """,
        )
    )

    assert standard.prompt_profile == "standard"
    assert compact.prompt_profile == "compact"


def test_rejects_unknown_prompt_profile(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        prompt_profile = "local"

        [[location]]
        name = "Test City"
        """,
    )

    with pytest.raises(ConfigError) as exc:
        load_config(path)

    assert "prompt_profile" in str(exc.value)


def test_rejects_openrouter_context_llm(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        context_llm = "or:openai/gpt-4o"

        [[location]]
        name = "Test City"
        """,
    )

    with pytest.raises(ConfigError) as exc:
        load_config(path)

    assert "context_llm" in str(exc.value)


@pytest.mark.parametrize("context_llm", ["gpt-5.6-luna", "gpt-5.6-terra"])
def test_llm_search_accepts_openai_gpt_context_models(
    tmp_path: Path,
    context_llm: str,
) -> None:
    path = _write_config(
        tmp_path,
        f'''\
        context_provider = "llm-search"
        context_llm = "{context_llm}"

        [[location]]
        name = "Test City"
        ''',
    )

    config = load_config(path)

    assert config.context_provider == "llm-search"
    assert config.context_llm == context_llm


def test_reasoning_defaults_to_high_with_separate_master_switch(tmp_path: Path) -> None:
    config = load_config(
        _write_config(
            tmp_path,
            """
            enable_reasoning = true

            [[location]]
            name = "Test City"
            """,
        )
    )

    assert config.enable_reasoning is True
    assert config.location_reasoning == "high"
    assert config.area_reasoning == "high"


def test_explicit_legacy_reasoning_levels_are_preserved(tmp_path: Path) -> None:
    config = load_config(
        _write_config(
            tmp_path,
            """
            enable_reasoning = false
            location_reasoning = "low"
            area_reasoning = "auto"

            [[location]]
            name = "Test City"
            """,
        )
    )

    assert config.enable_reasoning is False
    assert config.location_reasoning == "low"
    assert config.area_reasoning == "auto"


@pytest.mark.parametrize("context_llm", ["or:openai/gpt-5-mini", "lms:local-context"])
def test_brave_context_accepts_any_supported_synthesis_model(
    tmp_path: Path,
    context_llm: str,
) -> None:
    path = _write_config(
        tmp_path,
        f'''\
        context_provider = "brave"
        context_llm = "{context_llm}"

        [[location]]
        name = "Test City"
        ''',
    )

    config = load_config(path)

    assert config.context_llm == context_llm


def test_rejects_non_search_context_fallback(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
        context_provider = "brave"
        context_llm = "lms:local-context"
        context_fallback_llm = "or:openai/gpt-5-mini"

        [[location]]
        name = "Test City"
        """,
    )

    with pytest.raises(ConfigError) as exc:
        load_config(path)

    assert "context_fallback_llm" in str(exc.value)


def test_logs_unknown_area_locations(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = _write_config(
        tmp_path,
        """
        [[location]]
        name = "Known City"

        [[area]]
        name = "Sample Area"
        locations = ["Known City", "Unknown City"]
        """,
    )

    caplog.set_level(logging.DEBUG)
    load_config(path)

    assert "references location" in caplog.text
