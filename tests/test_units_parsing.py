from ibf.config.models import LocationConfig
from ibf.pipeline.executor import _resolve_units


def test_units_parentheses_secondary_parsed() -> None:
    location = LocationConfig(
        name="Test",
        units={
            "temperature_unit": "celsius(fahrenheit)",
            "windspeed_unit": "mph(kph)",
            "precipitation_unit": "inch(mm)",
        },
    )
    resolved = _resolve_units(location)

    assert resolved.temperature_primary == "celsius"
    assert resolved.temperature_secondary == "fahrenheit"
    assert resolved.windspeed_primary == "mph"
    assert resolved.windspeed_secondary == "kph"
    assert resolved.precipitation_primary == "inch"
    assert resolved.precipitation_secondary == "mm"
    # Snowfall should inherit inches when precipitation is inches and no snowfall_unit is set.
    assert resolved.snowfall_primary == "inch"
