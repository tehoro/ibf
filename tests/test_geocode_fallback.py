from __future__ import annotations

import pytest
import requests

from ibf.api import geocode as geocode_module
from ibf.config.settings import Secrets


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.url = "https://geocoding-api.open-meteo.com/v1/search"

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_geocode_without_google_key_uses_open_meteo(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    payload = {
        "results": [
            {
                "name": "Test City",
                "latitude": 10.0,
                "longitude": 20.0,
                "timezone": "UTC",
                "country_code": "TC",
                "country": "Test Country",
                "admin1": "Test Region",
                "admin2": "Test District",
            }
        ]
    }

    def fake_get(*_args, **_kwargs):
        return _FakeResponse(payload)

    monkeypatch.setattr(geocode_module, "get_secrets", lambda: Secrets(google_api_key=None))
    monkeypatch.setattr(geocode_module, "CACHE_PATH", tmp_path / "search_cache.json")
    monkeypatch.setattr(geocode_module.requests, "get", fake_get)
    monkeypatch.setattr(geocode_module, "_google_geocode", lambda *_args, **_kwargs: pytest.fail("Google fallback called"))

    result = geocode_module.geocode_name("Test City")
    assert result is not None
    assert result.name == "Test City"
    assert result.admin1 == "Test Region"
    assert result.admin2 == "Test District"


def test_geocode_without_google_key_and_no_open_meteo_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    def fake_get(*_args, **_kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr(geocode_module, "get_secrets", lambda: Secrets(google_api_key=None))
    monkeypatch.setattr(geocode_module, "CACHE_PATH", tmp_path / "search_cache.json")
    monkeypatch.setattr(geocode_module.requests, "get", fake_get)

    result = geocode_module.geocode_name("Missing City")
    assert result is None


def test_legacy_geocode_cache_is_enriched_without_moving_forecast_point(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    cache_path = tmp_path / "search_cache.json"
    cache_path.write_text(
        """{"otaki, new zealand":{"name":"Ōtaki, New Zealand","latitude":-40.7603,
        "longitude":175.1577,"timezone":"Pacific/Auckland","country_code":"NZ",
        "altitude":13.6}}"""
    )
    payload = {
        "results": [
            {
                "name": "Ōtaki",
                "latitude": -40.7583,
                "longitude": 175.15,
                "country": "New Zealand",
                "country_code": "NZ",
                "admin1": "Wellington Region",
                "admin2": "Kapiti Coast District",
            },
            {
                "name": "Ōtaki",
                "latitude": 35.2878,
                "longitude": 140.2436,
                "country": "Japan",
                "country_code": "JP",
                "admin1": "Chiba",
            },
        ]
    }
    monkeypatch.setattr(geocode_module, "CACHE_PATH", cache_path)
    monkeypatch.setattr(geocode_module.requests, "get", lambda *args, **kwargs: _FakeResponse(payload))

    result = geocode_module.geocode_name("Otaki, New Zealand")

    assert result is not None
    assert result.latitude == -40.7603
    assert result.longitude == 175.1577
    assert result.altitude == 13.6
    assert result.admin1 == "Wellington Region"
    assert result.admin2 == "Kapiti Coast District"
