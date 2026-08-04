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


def _compact_test_dataset() -> list[dict]:
    hours = []
    directions = ["SW", "N", "E", "NW", "S", "W", "NE", "SE"]
    clouds = [5, 98, 5, 99, 8, 95, 10, 100]
    for index, (direction, cloud) in enumerate(zip(directions, clouds), start=6):
        hours.append(
            {
                "hour": f"{index:02d}:00",
                "ensemble_members": {
                    "member00": {
                        "temperature": float(index),
                        "precipitation": 0.0,
                        "snowfall": 0.0,
                        "weather": "clear sky" if cloud < 50 else "overcast",
                        "cloud_cover": cloud,
                        "wind_direction": direction,
                        "wind_speed": 10.0,
                        "wind_gust": 25.0,
                        "snow_level": 1500.0,
                    }
                },
            }
        )
    return [
        {
            "date": "2024-01-10",
            "year": 2024,
            "month": 1,
            "day": 10,
            "dayofweek": "Wednesday",
            "hours": hours,
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


def test_compact_formatter_replaces_hourly_sky_and_wind_noise_with_daily_signals() -> None:
    output = format_location_dataset(
        _compact_test_dataset(),
        [],
        "UTC",
        temperature_unit="celsius",
        precipitation_unit="mm",
        snowfall_unit="cm",
        windspeed_unit="kph",
        compact=True,
    )

    assert "COMPACT DAILY SIGNALS" in output
    assert "write exactly 'Light winds.'" in output
    assert "every supplied level is above the relevance limit" in output
    assert "cc98" not in output
    assert "SW 10" not in output
    assert "Clear sky" not in output
    assert "Overcast" not in output


def test_formatter_omits_sub_millimetre_daily_rainfall_total() -> None:
    dataset = _single_hour_dataset(snow_level_m=500.0)
    member = dataset[0]["hours"][0]["ensemble_members"]["member00"]
    member["precipitation"] = 0.5
    member["snowfall"] = 0.0
    member["weather"] = "light drizzle"

    output = format_location_dataset(
        dataset,
        [],
        "UTC",
        temperature_unit="celsius",
        precipitation_unit="mm",
        snowfall_unit="cm",
        windspeed_unit="kph",
    )

    assert "Total rainfall" not in output


def test_formatter_removes_relative_tomorrow_from_cached_day_label() -> None:
    dataset = _single_hour_dataset(snow_level_m=500.0)
    dataset[0]["dayofweek"] = "Tomorrow, Wednesday"

    output = format_location_dataset(
        dataset,
        [],
        "UTC",
        temperature_unit="celsius",
        precipitation_unit="mm",
        snowfall_unit="cm",
        windspeed_unit="kph",
    )

    assert output.startswith("Date: WEDNESDAY 10 JANUARY")
    assert "TOMORROW" not in output


def test_compact_partial_period_uses_hourly_temperature_trend_not_full_day_summary() -> None:
    dataset = _single_hour_dataset(snow_level_m=500.0)
    dataset[0]["dayofweek"] = "Rest of today, Wednesday"

    output = format_location_dataset(
        dataset,
        [],
        "UTC",
        temperature_unit="celsius",
        precipitation_unit="mm",
        snowfall_unit="cm",
        windspeed_unit="kph",
        compact=True,
    )

    assert output.startswith("Date: REST OF TODAY, WEDNESDAY 10 JANUARY")
    assert " Low 0°C, High 0°C" not in output


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


def test_range_summary_collapses_one_degree_celsius_spans_to_median() -> None:
    summary = calculate_range_summary(
        [23.0, 23.4, 23.8, 24.0],
        [29.0, 29.4, 29.8, 30.0],
        [],
        [],
        "C",
        "mm",
        "cm",
        False,
        False,
    )

    assert "Likely low 24°C" in summary
    assert "Likely high 30°C" in summary
    assert "23°C to 24°C" not in summary


def test_range_summary_collapses_two_degree_fahrenheit_spans_to_median() -> None:
    summary = calculate_range_summary(
        [68.0, 69.0, 70.0],
        [75.0, 76.0, 77.0],
        [],
        [],
        "F",
        "inch",
        "inch",
        False,
        False,
    )

    assert "Likely low 69°F" in summary
    assert "Likely high 76°F" in summary
    assert "68°F to 70°F" not in summary


def test_range_summary_preserves_wider_temperature_spans() -> None:
    celsius_summary = calculate_range_summary(
        [23.0, 25.0],
        [29.0, 32.0],
        [],
        [],
        "C",
        "mm",
        "cm",
        False,
        False,
    )
    fahrenheit_summary = calculate_range_summary(
        [68.0, 71.0],
        [75.0, 78.0],
        [],
        [],
        "F",
        "inch",
        "inch",
        False,
        False,
    )

    assert "Likely low 23°C to 25°C" in celsius_summary
    assert "Likely high 29°C to 32°C" in celsius_summary
    assert "Likely low 68°F to 71°F" in fahrenheit_summary
    assert "Likely high 75°F to 78°F" in fahrenheit_summary


def test_partial_period_range_summary_uses_only_collapsed_low() -> None:
    summary = calculate_range_summary(
        [10.0, 11.0],
        [15.0, 18.0],
        [],
        [],
        "C",
        "mm",
        "cm",
        True,
        False,
    )

    assert summary == "Likely low 11°C"


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


def test_range_summary_uses_up_to_when_precipitation_lower_end_rounds_to_zero() -> None:
    summary = calculate_range_summary(
        [10, 11, 12],
        [20, 21, 22],
        [0, 0, 0.1, 0.2, 0.3, 3.0, 3.2],
        [0, 0, 0, 0, 0, 0, 0],
        "C",
        "mm",
        "cm",
        False,
        False,
    )

    assert "Likely precipitation up to 3 mm" in summary
    assert "0 mm to 3 mm" not in summary


def test_range_summary_uses_up_to_for_subunit_inch_lower_end() -> None:
    summary = calculate_range_summary(
        [50, 51, 52],
        [70, 71, 72],
        [0, 0.001, 0.01, 0.02, 0.1, 0.12],
        [0, 0, 0, 0, 0, 0],
        "F",
        "inch",
        "inch",
        False,
        False,
    )

    assert "Likely precipitation up to 0.1 in" in summary
    assert "0.0 in to 0.1 in" not in summary


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
