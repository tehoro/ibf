import hashlib
import json
from pathlib import Path
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
        highest_terrain_m=None,
        model_id="ecmwf_ifs025",
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
    monkeypatch.setattr(executor, "generate_forecast_text", lambda *args, **kwargs: "Mock forecast text")
    monkeypatch.setattr(
        executor,
        "fetch_impact_context",
        lambda name, **_: SimpleNamespace(content=f"Impact context for {name}", cost_cents=0.0),
    )

    def fake_generate_maps(config, **kwargs):
        root = Path(kwargs.get("output_dir") or config.web_root)
        maps_dir = root / "maps"
        maps_dir.mkdir(parents=True, exist_ok=True)
        filters = kwargs.get("area_filters")
        filter_names = {name.lower() for name in filters} if filters else None
        for area in config.areas:
            if filter_names and area.name.lower() not in filter_names:
                continue
            slug = slugify(area.name)
            (maps_dir / f"{slug}.png").write_bytes(b"fake")
        return SimpleNamespace(
            root=maps_dir,
            generated={area.name: maps_dir / f"{slugify(area.name)}.png" for area in config.areas},
            failures={},
            summary_lines=lambda: ["Output directory: fake", "Maps created: 2"],
        )

    monkeypatch.setattr(cli, "generate_area_maps", fake_generate_maps)

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

    area_page = web_root / slugify("Sample Area") / "index.html"
    html = area_page.read_text(encoding="utf-8")
    assert "Show map for Sample Area" in html
    assert f"../maps/{slugify('Sample Area')}.png" in html

    hash_file = web_root / ".ibf_maps_hash"
    assert hash_file.exists()
    state = json.loads(hash_file.read_text(encoding="utf-8"))
    expected_slugs = {slugify("Sample Area"), slugify("Sample Regional")}
    assert set(state["areas"].keys()) == expected_slugs

    def expected_area_hash(name: str, locations: list[str]) -> str:
        payload = {"name": name, "locations": locations}
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    assert state["areas"][slugify("Sample Area")] == expected_area_hash("Sample Area", ["Test City", "Second City"])
    assert state["areas"][slugify("Sample Regional")] == expected_area_hash(
        "Sample Regional", ["Test City", "Second City"]
    )
