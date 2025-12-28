from __future__ import annotations

from ibf.llm.formatter import format_location_dataset


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
