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


def test_warns_on_unknown_area_locations(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
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

    caplog.set_level(logging.WARNING)
    load_config(path)

    assert "references location" in caplog.text
