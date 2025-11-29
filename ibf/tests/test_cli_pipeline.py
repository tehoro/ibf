import json
from types import SimpleNamespace
from typing import Dict

import pytest
from typer.testing import CliRunner

from ibf import cli
from ibf.pipeline import executor
from ibf.pipeline.executor import LocationForecastPayload, LocationUnits
from ibf.api.geocode import GeocodeResult
from ibf.llm.settings import LLMSettings
from ibf.util import slugify


def _make_mock_payload(name: str, tmp_json_cache) -> LocationForecastPayload:
    dataset = [
        {
            "date": "2025-05-01",
            "year": 2025,
            "month": 5,
            "day": 1,
            "dayofweek": "MONDAY",
            "hours": [
                {
                    "hour": "12:00",
                    "ensemble_members": {
                        "member00": {
                            "temperature": 24.0,
                            "precipitation": 0.4,
                            "snowfall": 0.0,
                            "weather": "sunny",
                            "wind_speed": 15,
                            "wind_gust": 20,
                            "wind_direction": "N",
                            "snow_level": 0,
                        }
                    },
                }
            ],
        }
    ]
    cache_path = tmp_json_cache / f"{slugify(name)}.json"
    cache_path.write_text(json.dumps(dataset), encoding="utf-8")
    units = LocationUnits(
        temperature_primary="celsius",
        temperature_secondary=None,
        precipitation_primary="mm",
        precipitation_secondary=None,
        snowfall_primary="cm",
        snowfall_secondary=None,
        windspeed_primary="kph",
        windspeed_secondary=None,
        altitude_m=0.0,
    )
    return LocationForecastPayload(
        name=name,
        geocode=GeocodeResult(name=name, latitude=1.0, longitude=2.0, timezone="UTC"),
        alerts=[],
        dataset=dataset,
        dataset_cache=cache_path,
        units=units,
        formatted_dataset=f"Dataset for {name}",
    )


def test_cli_run_generates_forecasts(
    runner: CliRunner,
    sample_config: Dict[str, object],
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    def fake_collect(name: str, **kwargs):
        return _make_mock_payload(name, cache_dir)

    monkeypatch.setattr(executor, "_collect_location_payload", fake_collect)
    monkeypatch.setattr(
        executor,
        "resolve_llm_settings",
        lambda config, override_choice=None: LLMSettings(
            provider="mock", model=(override_choice or "mock-model"), api_key="test", base_url=None
        ),
    )
    monkeypatch.setattr(executor, "generate_forecast_text", lambda prompt, system_prompt, settings: "Mock forecast text")
    monkeypatch.setattr(
        executor,
        "fetch_impact_context",
        lambda name, **_: SimpleNamespace(content=f"Impact context for {name}"),
    )

    result = runner.invoke(cli.app, ["run", "--config", str(sample_config["path"])])
    assert result.exit_code == 0, result.output

    web_root = sample_config["web_root"]
    slugs = [
        slugify("Test City"),
        slugify("Second City"),
        slugify("Sample Area"),
        slugify("Sample Regional"),
    ]
    for slug in slugs:
        page = web_root / slug / "index.html"
        assert page.exists(), f"Expected HTML output at {page}"

