from __future__ import annotations

from datetime import datetime, timedelta
import os
import time
from zoneinfo import ZoneInfo

import pytest

from ibf.pipeline.dataset import _classify_day, _parse_timestamp, build_processed_days


@pytest.fixture
def process_timezone(monkeypatch: pytest.MonkeyPatch):
    if not hasattr(time, "tzset"):
        pytest.skip("process timezone switching requires time.tzset")

    original = os.environ.get("TZ")

    def set_timezone(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        time.tzset()

    yield set_timezone

    if original is None:
        monkeypatch.delenv("TZ", raising=False)
    else:
        monkeypatch.setenv("TZ", original)
    time.tzset()


def test_naive_open_meteo_times_are_forecast_local_not_process_local(process_timezone) -> None:
    process_timezone("Pacific/Auckland")
    forecast_tz = ZoneInfo("Europe/Zurich")

    parsed = _parse_timestamp("2030-01-02T15:00", forecast_tz)

    assert parsed is not None
    assert parsed.tzinfo is forecast_tz
    assert parsed.strftime("%Y-%m-%d %H:%M") == "2030-01-02 15:00"


def test_offset_open_meteo_times_are_converted_to_forecast_timezone(process_timezone) -> None:
    process_timezone("Pacific/Auckland")
    forecast_tz = ZoneInfo("Europe/Zurich")

    parsed = _parse_timestamp("2030-01-02T14:00Z", forecast_tz)

    assert parsed is not None
    assert parsed.tzinfo is forecast_tz
    assert parsed.strftime("%Y-%m-%d %H:%M") == "2030-01-02 15:00"


def test_tomorrow_uses_stable_absolute_weekday_label() -> None:
    timezone = ZoneInfo("Pacific/Auckland")
    current = datetime(2030, 1, 2, 9, 0, tzinfo=timezone)
    forecast = current + timedelta(days=1)

    assert _classify_day(forecast, current) == forecast.strftime("%A")


def test_processed_days_keep_naive_hour_in_location_timezone(process_timezone) -> None:
    process_timezone("Pacific/Auckland")
    forecast_tz = ZoneInfo("Europe/Zurich")
    forecast_date = (datetime.now(forecast_tz) + timedelta(days=3)).date()
    local_time = f"{forecast_date.isoformat()}T15:00"

    days = build_processed_days(
        {
            "hourly_units": {"time": "iso8601", "temperature_2m": "degC"},
            "hourly": {
                "time": [local_time],
                "temperature_2m": [12.0],
                "precipitation": [0.0],
                "snowfall": [0.0],
                "weather_code": [1],
                "cloud_cover": [20],
                "wind_speed_10m": [10.0],
                "wind_direction_10m": [180.0],
            },
        },
        timezone_name="Europe/Zurich",
        thin_select=1,
    )

    assert days
    assert days[0]["date"] == forecast_date.isoformat()
    assert days[0]["hours"][0]["hour"] == "15:00"
