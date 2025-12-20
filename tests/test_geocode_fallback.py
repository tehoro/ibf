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


def test_geocode_without_google_key_uses_open_meteo(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "results": [
            {
                "name": "Test City",
                "latitude": 10.0,
                "longitude": 20.0,
                "timezone": "UTC",
                "country_code": "TC",
            }
        ]
    }

    def fake_get(*_args, **_kwargs):
        return _FakeResponse(payload)

    monkeypatch.setattr(geocode_module, "get_secrets", lambda: Secrets(google_api_key=None))
    monkeypatch.setattr(geocode_module.requests, "get", fake_get)
    monkeypatch.setattr(geocode_module, "_google_geocode", lambda *_args, **_kwargs: pytest.fail("Google fallback called"))

    result = geocode_module.geocode_name("Test City")
    assert result is not None
    assert result.name == "Test City"


def test_geocode_without_google_key_and_no_open_meteo_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(*_args, **_kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr(geocode_module, "get_secrets", lambda: Secrets(google_api_key=None))
    monkeypatch.setattr(geocode_module.requests, "get", fake_get)

    result = geocode_module.geocode_name("Missing City")
    assert result is None
