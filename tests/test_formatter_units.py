from __future__ import annotations

import logging

from ibf.api.alerts import AlertSummary
from ibf.llm.formatter import _format_alerts, calculate_range_summary, format_location_dataset


def _single_hour_dataset(*, snow_level_m: float) -> list[dict]:
    return [
        {
            "date": "2024-01-10",
            "year": 2024,
            "month": 1,
            "day": 10,
            "dayofweek": "Wednesday",
            "hours": [
                {
                    "hour": "06:00",
                    "ensemble_members": {
                        "member00": {
                            "temperature": 0.0,
                            "precipitation": 10.0,
                            "snowfall": 2.0,
                            "weather": "snow",
                            "cloud_cover": 50,
                            "wind_direction": "E",
                            "wind_speed": 20.0,
                            "wind_gust": 35.0,
                            "snow_level": snow_level_m,
                        }
                    },
                }
            ],
        }
    ]


def test_formatter_converts_to_imperial_units() -> None:
    dataset = _single_hour_dataset(snow_level_m=1500.0)
    output = format_location_dataset(
        dataset,
        [],
        "UTC",
        temperature_unit="fahrenheit",
        precipitation_unit="inch",
        snowfall_unit="inch",
        windspeed_unit="mph",
    )

    assert "32°" in output
    assert "0.4 in/h" in output
    assert "(snow down to about 5000 ft)" in output
    assert "E 10 gust 20" in output


def test_formatter_rounds_snow_level_metric() -> None:
    dataset = _single_hour_dataset(snow_level_m=1450.0)
    output = format_location_dataset(
        dataset,
        [],
        "UTC",
        temperature_unit="celsius",
        precipitation_unit="mm",
        snowfall_unit="cm",
        windspeed_unit="kph",
    )

    assert "(snow down to about 1400 m)" in output


def test_range_summary_collapses_identical_temperature_endpoints() -> None:
    summary = calculate_range_summary(
        [12, 12, 12],
        [24, 24, 24],
        [0, 0, 0],
        [0, 0, 0],
        "C",
        "mm",
        "cm",
        False,
        False,
    )

    assert "Likely low 12°C" in summary
    assert "Likely high 24°C" in summary
    assert "12°C to 12°C" not in summary
    assert "24°C to 24°C" not in summary


def test_range_summary_collapses_rounded_precipitation_and_snowfall_endpoints() -> None:
    summary = calculate_range_summary(
        [10, 11, 12],
        [20, 21, 22],
        [5.1, 5.2, 5.3],
        [2.1, 2.2, 2.3],
        "C",
        "mm",
        "cm",
        False,
        False,
    )

    assert "Likely precipitation around 5 mm" in summary
    assert "Likely snowfall around 2 cm" in summary
    assert "5 mm to 5 mm" not in summary
    assert "2 cm to 2 cm" not in summary


def test_range_summary_omits_precipitation_amount_when_upper_end_rounds_to_zero() -> None:
    summary = calculate_range_summary(
        [10, 11, 12],
        [20, 21, 22],
        [0, 0, 0.1, 0.2, 0.3],
        [0, 0, 0, 0, 0],
        "C",
        "mm",
        "cm",
        False,
        False,
    )

    assert "Estimated probability of precipitation:" in summary
    assert "Likely precipitation" not in summary
    assert "0 mm" not in summary


def test_formatter_skips_alert_with_invalid_timestamps(caplog) -> None:
    alert = AlertSummary(
        title="Invalid alert",
        description="Test",
        onset="not-a-timestamp",
        expires="also-not-a-timestamp",
    )

    with caplog.at_level(logging.WARNING):
        output = _format_alerts([alert], [{"date": "2026-07-27"}], "UTC")

    assert output == ""
    assert "Skipping alert with invalid timestamps" in caplog.text
