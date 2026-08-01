from __future__ import annotations

import pytest

from ibf.llm.prompts import (
    UnitInstructions,
    build_area_system_prompt,
    build_area_user_prompt,
    build_regional_system_prompt,
    build_regional_user_prompt,
    build_spot_system_prompt,
    build_spot_user_prompt,
)


@pytest.mark.parametrize(
    ("builder", "model_kind"),
    [
        (build_spot_system_prompt, "ensemble"),
        (build_spot_system_prompt, "deterministic"),
        (build_area_system_prompt, "ensemble"),
        (build_area_system_prompt, "deterministic"),
        (build_regional_system_prompt, "ensemble"),
        (build_regional_system_prompt, "deterministic"),
    ],
)
def test_forecast_prompts_do_not_require_invented_precipitation_totals(
    builder,
    model_kind: str,
) -> None:
    units = UnitInstructions(
        temperature_primary="celsius",
        temperature_secondary=None,
        precipitation_primary="mm",
        precipitation_secondary=None,
        snowfall_primary="cm",
        snowfall_secondary=None,
        windspeed_primary="kph",
        windspeed_secondary=None,
    )

    prompt = builder(units, model_kind=model_kind)

    assert "If no daily total or range is provided" in prompt
    assert "Never invent or infer an amount" in prompt
    assert "never report a zero total" in prompt
    assert "Do not mention precipitation without giving an amount" not in prompt


@pytest.mark.parametrize(
    ("builder", "model_kind"),
    [
        (build_spot_system_prompt, "ensemble"),
        (build_spot_system_prompt, "deterministic"),
        (build_area_system_prompt, "ensemble"),
        (build_area_system_prompt, "deterministic"),
        (build_regional_system_prompt, "ensemble"),
        (build_regional_system_prompt, "deterministic"),
    ],
)
def test_forecast_prompts_assign_late_night_hours_to_the_correct_day(
    builder,
    model_kind: str,
) -> None:
    units = UnitInstructions(
        temperature_primary="celsius",
        temperature_secondary=None,
        precipitation_primary="mm",
        precipitation_secondary=None,
        snowfall_primary="cm",
        snowfall_secondary=None,
        windspeed_primary="kph",
        windspeed_secondary=None,
    )

    prompt = builder(units, model_kind=model_kind)

    assert 'Do not use "overnight" as a forecast timing label' in prompt
    assert "midnight through before sunrise" in prompt
    assert "preceding day are late evening" in prompt
    assert "overnight; towards dawn" not in prompt


@pytest.mark.parametrize(
    ("builder", "model_kind"),
    [
        (build_spot_system_prompt, "ensemble"),
        (build_spot_system_prompt, "deterministic"),
        (build_area_system_prompt, "ensemble"),
        (build_area_system_prompt, "deterministic"),
        (build_regional_system_prompt, "ensemble"),
        (build_regional_system_prompt, "deterministic"),
    ],
)
def test_forecast_prompts_use_direct_wind_and_correct_snow_level_wording(
    builder,
    model_kind: str,
) -> None:
    units = UnitInstructions(
        temperature_primary="celsius",
        temperature_secondary=None,
        precipitation_primary="mm",
        precipitation_secondary=None,
        snowfall_primary="cm",
        snowfall_secondary=None,
        windspeed_primary="kph",
        windspeed_secondary=None,
    )

    prompt = builder(units, model_kind=model_kind)

    assert "Keep wind wording concise and meteorological" in prompt
    assert '"winds will be present"' in prompt
    assert '"winds will persist"' in prompt
    assert "Strong southerlies 40 to 50 km/h, gusting 110 km/h through the afternoon" in prompt
    assert '"powerful gusts"' in prompt
    assert 'never "gusts reaching up to" or "gusts hitting up to"' in prompt
    assert '"evening and late evening"' in prompt
    assert '"mostly clear or mainly clear"' in prompt
    assert 'Use "sunny" or "bright" only for daylight' in prompt
    assert "snow may fall on terrain around 600 metres and above" in prompt
    assert 'never means snow below 600 metres or on "lower ground"' in prompt
    assert '"snow lowering to 600 metres"' in prompt
    assert "the snow level lowers from 1600 to 800 metres" in prompt
    assert 'Never describe snow as confined to an elevation band such as "snow between 800 and 1600 metres"' in prompt
    assert 'never a relative label such as "Tomorrow"' in prompt
    assert 'Do not use clock times or "overnight"' in prompt


def test_detailed_wordiness_prioritizes_meaningful_changes() -> None:
    prompts = [
        build_spot_user_prompt(
            "sample data",
            location_name="Example",
            latitude=-41.0,
            longitude=174.0,
            season="winter",
            wordiness="detailed",
            model_kind="deterministic",
        ),
        build_area_user_prompt(
            "sample data",
            area_name="Example Area",
            location_names=["Example"],
            wordiness="detailed",
        ),
        build_regional_user_prompt(
            "sample data",
            area_name="Example Region",
            location_names=["Example"],
            wordiness="detailed",
        ),
    ]

    for prompt in prompts:
        assert "meaningful weather evolution" in prompt
        assert "Combine adjacent periods" in prompt
        assert "every hourly fluctuation" in prompt
        assert "very detailed" not in prompt
        assert "extremely detailed" not in prompt
