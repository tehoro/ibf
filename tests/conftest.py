from pathlib import Path
import textwrap

import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner(mix_stderr=False)


@pytest.fixture
def sample_config(tmp_path: Path) -> dict:
    """
    Write a small configuration file for tests and return metadata.
    """
    web_root = tmp_path / "site"
    config_text = textwrap.dedent(
        f"""
        web_root = "{web_root}"
        llm = "mock-model"
        translation_llm = "mock-translation"

        [[location]]
        name = "Test City"
        translation_language = "Spanish"
        temperature_unit = "celsius"
        precipitation_unit = "mm"
        windspeed_unit = "kph"

        [[location]]
        name = "Second City"
        translation_language = "French"
        temperature_unit = "celsius"
        precipitation_unit = "mm"
        windspeed_unit = "kph"

        [[area]]
        name = "Sample Area"
        translation_language = "Spanish"
        locations = ["Test City", "Second City"]

        [[area]]
        name = "Sample Regional"
        mode = "regional"
        translation_language = "French"
        locations = ["Test City", "Second City"]
        """
    ).strip()
    path = tmp_path / "config.toml"
    path.write_text(config_text + "\n", encoding="utf-8")
    return {"path": path, "web_root": web_root}
