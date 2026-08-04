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


def test_standard_prompt_does_not_append_compact_style_stack() -> None:
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

    prompts = [
        build_spot_system_prompt(units, model_kind="ensemble"),
        build_spot_system_prompt(units, model_kind="deterministic"),
        build_area_system_prompt(units, model_kind="ensemble"),
        build_area_system_prompt(units, model_kind="deterministic"),
        build_regional_system_prompt(units, model_kind="ensemble"),
        build_regional_system_prompt(units, model_kind="deterministic"),
    ]

    for prompt in prompts:
        assert "COMPACT DAILY SIGNALS" not in prompt
        assert "#FINAL METEOROLOGICAL WORDING CHECK" not in prompt
        assert "#METEOROLOGICAL WRITING" not in prompt


@pytest.mark.parametrize(
    ("temperature_unit", "wind_unit", "temperature_symbol", "thresholds"),
    [
        ("celsius", "kph", "°C", "20 km/h"),
        ("fahrenheit", "mph", "°F", "15 mph"),
        ("celsius", "kt", "°C", "10 kt"),
        ("celsius", "mps", "°C", "5 m/s"),
    ],
)
def test_compact_spot_prompt_is_generalized_and_unit_aware(
    temperature_unit: str,
    wind_unit: str,
    temperature_symbol: str,
    thresholds: str,
) -> None:
    units = UnitInstructions(
        temperature_primary=temperature_unit,
        temperature_secondary=None,
        precipitation_primary="in" if temperature_unit == "fahrenheit" else "mm",
        precipitation_secondary=None,
        snowfall_primary="in" if temperature_unit == "fahrenheit" else "cm",
        snowfall_secondary=None,
        windspeed_primary=wind_unit,
        windspeed_secondary=None,
    )

    prompt = build_spot_system_prompt(
        units,
        model_kind="deterministic",
        prompt_profile="compact",
    )

    assert "general-audience radio bulletin" in prompt
    assert "UK or New Zealand English" in prompt
    assert "COMPACT DAILY SIGNALS" in prompt
    assert "at most two broad sky descriptions" in prompt
    assert thresholds in prompt
    assert f"repeating {temperature_symbol} on both values" in prompt
    assert "ACTIVE ALERTS" in prompt
    assert '"snow down to about X"' in prompt
    assert 'Never say winds "will be present" or "will persist"' in prompt
    assert 'Start it exactly as "**[supplied label]:**"' in prompt
    assert "#TARGET SHAPE" in prompt


def test_compact_profile_does_not_change_ensemble_spot_prompt() -> None:
    units = UnitInstructions("celsius", None, "mm", None, "cm", None, "kph", None)

    standard = build_spot_system_prompt(units, model_kind="ensemble")
    compact_requested = build_spot_system_prompt(
        units,
        model_kind="ensemble",
        prompt_profile="compact",
    )

    assert compact_requested == standard


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


def test_spot_output_contract_is_the_last_user_prompt_section() -> None:
    formatted_dataset = """Date: TUESDAY 4 AUGUST
midnight 10° Light rain S 20 gust 50
 Low 7°C, High 10°C
 Total rainfall: 2 mm.
"""
    prompt = build_spot_user_prompt(
        formatted_dataset,
        location_name="Wellington",
        latitude=-41.3,
        longitude=174.8,
        season="winter",
        wordiness="normal",
        model_kind="deterministic",
    )

    assert prompt.index("<END>") < prompt.index("--- MANDATORY OUTPUT CONTRACT ---")
    assert "TUESDAY 4 AUGUST: low 7°C; high 10°C; rainfall 2 mm (must be stated)" in prompt
    assert "--- FINAL STYLE GUIDE ---" not in prompt
    assert prompt.rstrip().endswith("Return only the forecast paragraphs.")


def test_deterministic_spot_prompt_preserves_context_and_facts() -> None:
    prompt = build_spot_user_prompt(
        """Date: TUESDAY 4 AUGUST
midnight 10° Light rain S 20 gust 50
 Low 7°C, High 10°C
 Total rainfall: 2 mm.
""",
        location_name="Wellington",
        latitude=-41.3,
        longitude=174.8,
        season="winter",
        wordiness="normal",
        model_kind="deterministic",
        user_extra_context="Use the supplied official warning context if relevant.",
        impact_context="MetService Heavy Rain Watch applies Tuesday afternoon.",
        prompt_profile="compact",
    )

    assert "IMPORTANT USER CONTEXT" in prompt
    assert "Use the supplied official warning context if relevant." in prompt
    assert "ADDITIONAL CONTEXT" in prompt
    assert "MetService Heavy Rain Watch applies Tuesday afternoon." in prompt
    assert "TUESDAY 4 AUGUST: low 7°C; high 10°C; rainfall 2 mm (must be stated)" in prompt
