from ibf.llm.prompts import (
    UnitInstructions,
    build_area_system_prompt,
    build_regional_system_prompt,
    build_spot_system_prompt,
    build_spot_user_prompt,
)


_UNITS = UnitInstructions(
    temperature_primary="celsius",
    temperature_secondary=None,
    precipitation_primary="mm",
    precipitation_secondary=None,
    snowfall_primary="cm",
    snowfall_secondary=None,
    windspeed_primary="kph",
    windspeed_secondary=None,
)


def test_ensemble_spot_prompt_keeps_scenarios_internal() -> None:
    system_prompt = build_spot_system_prompt(_UNITS, model_kind="ensemble")
    user_prompt = build_spot_user_prompt(
        "Scenario 01: sample data",
        location_name="Example",
        latitude=-41.0,
        longitude=174.0,
        season="winter",
        wordiness="brief",
        model_kind="ensemble",
    )

    assert "Scenario labels are internal data labels only" in system_prompt
    assert "different possible futures at that one location" in system_prompt
    for spatial_phrase in ('"in some areas"', '"in places"', '"elsewhere"', '"locally"'):
        assert spatial_phrase in system_prompt
    assert "Mention geography only when it is explicitly supported" in system_prompt
    assert "An estimated probability shown in the supplied RANGE SUMMARY is a valid estimate" in system_prompt
    assert "if the RANGE SUMMARY gives one value, report one value" in system_prompt
    assert "--- FINAL ENSEMBLE RULES ---" in user_prompt
    assert 'Never use spatial wording such as "in some areas"' in user_prompt
    assert "Use every supplied Date block once as its own forecast period" in user_prompt
    assert user_prompt.index("<END>") < user_prompt.index("--- FINAL ENSEMBLE RULES ---")


def test_deterministic_spot_prompt_does_not_add_ensemble_tail_rules() -> None:
    user_prompt = build_spot_user_prompt(
        "sample data",
        location_name="Example",
        latitude=-41.0,
        longitude=174.0,
        season="winter",
        wordiness="brief",
        model_kind="deterministic",
    )

    assert "--- FINAL ENSEMBLE RULES ---" not in user_prompt


def test_area_and_regional_ensemble_prompts_avoid_process_jargon() -> None:
    for system_prompt in (
        build_area_system_prompt(_UNITS, model_kind="ensemble"),
        build_regional_system_prompt(_UNITS, model_kind="ensemble"),
    ):
        assert "Never mention models, scenarios, members, runs, ensembles" in system_prompt
        assert "An estimated probability shown in a supplied RANGE SUMMARY is valid" in system_prompt
