import json
from pathlib import Path

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
    config = {
        "locations": [
            {
                "name": "Test City",
                "lang": "Spanish",
                "units": {"temperature_unit": "celsius", "precipitation_unit": "mm", "windspeed_unit": "kph"},
            },
            {
                "name": "Second City",
                "lang": "French",
                "units": {"temperature_unit": "celsius", "precipitation_unit": "mm", "windspeed_unit": "kph"},
            },
        ],
        "areas": [
            {
                "name": "Sample Area",
                "lang": "Spanish",
                "locations": ["Test City", "Second City"],
            },
            {
                "name": "Sample Regional",
                "mode": "regional",
                "lang": "French",
                "locations": ["Test City", "Second City"],
            },
        ],
        "web_root": str(web_root),
        "llm": "mock-model",
        "translation_llm": "mock-translation",
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return {"path": path, "web_root": web_root}

