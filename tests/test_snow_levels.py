from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ibf.pipeline.dataset import build_processed_days
from ibf.pipeline import executor


def _iso_times(hours: int = 1) -> list[str]:
    base = datetime.now(timezone.utc) + timedelta(hours=hours)
    return [base.isoformat()]


def _base_forecast_raw(*, freezing_level: float | None) -> dict:
    times = _iso_times(1)
    hourly = {
        "time": times,
        # Keep these comfortably above 1C so the simple freezing-level routine
        # returns a finite, positive snow level.
        "temperature_2m": [4.0],
        "dewpoint_2m": [3.0],
        "precipitation": [1.0],
        "snowfall": [0.0],
        "weather_code": [61],
        "cloud_cover": [50],
        "wind_speed_10m": [10.0],
        "wind_direction_10m": [90.0],
        "wind_gusts_10m": [15.0],
    }
    if freezing_level is not None:
        hourly["freezing_level_height"] = [freezing_level]
    return {
        "hourly_units": {"time": "iso8601", "temperature_2m": "°C"},
        "hourly": hourly,
    }


def test_snow_levels_disabled_ignores_freezing_level() -> None:
    raw = _base_forecast_raw(freezing_level=900.0)
    days = build_processed_days(
        raw,
        timezone_name="UTC",
        temperature_unit="celsius",
        precipitation_unit="mm",
        windspeed_unit="kph",
        thin_select=1,
        location_altitude=0.0,
        snow_levels_enabled=False,
        highest_terrain_m=2500.0,
        pressure_levels_hpa=[1000, 925, 850, 700, 600, 500],
    )
    member = days[0]["hours"][0]["ensemble_members"]["member00"]
    assert member.get("snow_level") is None


def test_snow_levels_uses_freezing_level_when_present() -> None:
    raw = _base_forecast_raw(freezing_level=1200.0)
    days = build_processed_days(
        raw,
        timezone_name="UTC",
        temperature_unit="celsius",
        precipitation_unit="mm",
        windspeed_unit="kph",
        thin_select=1,
        location_altitude=0.0,
        snow_levels_enabled=True,
        highest_terrain_m=3500.0,
        pressure_levels_hpa=[1000, 925, 850, 700, 600, 500],
    )
    member = days[0]["hours"][0]["ensemble_members"]["member00"]
    snow_level = member.get("snow_level")
    assert isinstance(snow_level, (int, float)) and snow_level > 0


def test_snow_levels_uses_profile_when_no_freezing_level() -> None:
    raw = _base_forecast_raw(freezing_level=None)
    raw["hourly"]["surface_pressure"] = [1013.0]

    # Minimal pressure-level profile expected by util.snow.extract_pressure_profile.
    # Keep the profile cold enough aloft to generate a plausible wet-bulb zero.
    for level, t, rh, z in [
        (1000, 3.0, 90.0, 100.0),
        (925, 1.0, 90.0, 800.0),
        (850, -1.0, 90.0, 1500.0),
        (700, -6.0, 90.0, 3000.0),
        # Simulate a missing level (e.g. some models omit 600 hPa). The extractor
        # should skip it and still compute snow level using remaining levels.
        (600, None, None, None),
        (500, -16.0, 90.0, 5600.0),
    ]:
        raw["hourly"][f"temperature_{level}hPa"] = [t]
        raw["hourly"][f"relative_humidity_{level}hPa"] = [rh]
        raw["hourly"][f"geopotential_height_{level}hPa"] = [z]

    days = build_processed_days(
        raw,
        timezone_name="UTC",
        temperature_unit="celsius",
        precipitation_unit="mm",
        windspeed_unit="kph",
        thin_select=1,
        location_altitude=0.0,
        snow_levels_enabled=True,
        highest_terrain_m=3500.0,
        pressure_levels_hpa=[1000, 925, 850, 700, 600, 500],
    )
    member = days[0]["hours"][0]["ensemble_members"]["member00"]
    snow_level = member.get("snow_level")
    assert isinstance(snow_level, (int, float)) and snow_level > 0


def test_snow_levels_fahrenheit_inputs_normalized() -> None:
    raw = _base_forecast_raw(freezing_level=1200.0)
    raw["hourly"]["temperature_2m"] = [40.0]
    raw["hourly"]["dewpoint_2m"] = [38.0]
    raw["hourly"]["precipitation"] = [0.1]
    raw["hourly_units"]["temperature_2m"] = "°F"
    raw["hourly_units"]["precipitation"] = "in"

    days = build_processed_days(
        raw,
        timezone_name="UTC",
        temperature_unit="fahrenheit",
        precipitation_unit="inch",
        windspeed_unit="mph",
        thin_select=1,
        location_altitude=0.0,
        snow_levels_enabled=True,
        highest_terrain_m=3500.0,
        pressure_levels_hpa=[1000, 925, 850, 700, 600, 500],
    )
    member = days[0]["hours"][0]["ensemble_members"]["member00"]
    assert abs(float(member.get("temperature")) - 4.4) < 0.2
    assert abs(float(member.get("precipitation")) - 2.54) < 0.05


def test_snow_levels_freezing_level_units_in_feet_are_converted() -> None:
    raw = _base_forecast_raw(freezing_level=5000.0)
    raw["hourly_units"]["freezing_level_height"] = "ft"
    raw["hourly_units"]["temperature_2m"] = "°F"
    raw["hourly"]["temperature_2m"] = [35.0]
    raw["hourly"]["dewpoint_2m"] = [33.0]
    raw["hourly"]["precipitation"] = [0.1]

    days = build_processed_days(
        raw,
        timezone_name="UTC",
        temperature_unit="fahrenheit",
        precipitation_unit="inch",
        windspeed_unit="mph",
        thin_select=1,
        location_altitude=0.0,
        snow_levels_enabled=True,
        highest_terrain_m=3500.0,
        pressure_levels_hpa=[1000, 925, 850, 700, 600, 500],
    )
    member = days[0]["hours"][0]["ensemble_members"]["member00"]
    snow_level = member.get("snow_level")
    assert isinstance(snow_level, (int, float)) and snow_level > 0


def test_executor_profile_gate_uses_temperature_cutoff() -> None:
    # Temp above cutoff -> should not fetch profile.
    raw = {
        "hourly": {
            "time": _iso_times(1),
            "temperature_2m": [16.0],
            "precipitation": [2.0],
            "weather_code": [61],
        }
    }
    assert executor._needs_snow_profile_request(raw) is False

    # Temp below cutoff + precip -> should fetch profile.
    raw["hourly"]["temperature_2m"] = [5.0]
    assert executor._needs_snow_profile_request(raw) is True
