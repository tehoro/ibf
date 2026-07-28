from __future__ import annotations

from ibf.util.meteo import wmo_weather


def test_strong_thunderstorm_codes_do_not_claim_hail() -> None:
    assert wmo_weather(95) == "thunderstorm"
    assert wmo_weather(96) == "strong thunderstorm"
    assert wmo_weather(99) == "severe thunderstorm"

    for code in (96, 99):
        assert "hail" not in wmo_weather(code)
