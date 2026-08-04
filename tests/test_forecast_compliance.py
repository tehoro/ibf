from __future__ import annotations

import pytest

from ibf.config.models import ForecastConfig, LocationConfig
from ibf.llm.compliance import (
    build_spot_correction_prompts,
    correction_preserves_other_numeric_facts,
    format_spot_output_contract,
    parse_spot_output_requirements,
    validate_spot_forecast,
)
from ibf.llm.settings import LLMSettings
from ibf.pipeline import executor
from ibf.pipeline.executor import LocationUnits


DETERMINISTIC_DATA = """Date: TOMORROW, MONDAY 3 AUGUST

midnight 10° Clear sky N 20 gust 40
 Low 10°C, High 15°C

Date: TUESDAY 4 AUGUST

midnight 10° Light rain S 20 gust 50
 Low 7°C, High 10°C
 Total rainfall: 2 mm.

Date: WEDNESDAY 5 AUGUST

midnight 8° Partly cloudy S 40 gust 90
 Low 4°C, High 9°C
"""


ENSEMBLE_DATA = """Date: WEDNESDAY 5 AUGUST

Scenario 00:
midnight 18° Light rain NW 5 gust 10
 Low 17°C, High 24°C
 Total rainfall: 7 mm.

Scenario 01:
midnight 16° Clear sky W 5
 Low 14°C, High 23°C

RANGE SUMMARY:
Likely low 14°C to 17°C
Likely high 23°C
Estimated probability of precipitation: 40%
"""


TEST_UNITS = LocationUnits(
    temperature_primary="celsius",
    temperature_secondary=None,
    precipitation_primary="mm",
    precipitation_secondary=None,
    snowfall_primary="cm",
    snowfall_secondary=None,
    windspeed_primary="kph",
    windspeed_secondary=None,
    altitude_m=0.0,
)


def test_deterministic_contract_requires_supplied_total_and_temperatures() -> None:
    requirements = parse_spot_output_requirements(
        DETERMINISTIC_DATA,
        model_kind="deterministic",
    )
    contract = format_spot_output_contract(requirements)

    assert "TUESDAY 4 AUGUST: low 7°C; high 10°C; rainfall 2 mm (must be stated)" in contract
    assert "MONDAY 3 AUGUST: low 10°C; high 15°C; no reportable rainfall amount supplied" in contract

    forecast = """**Monday, 3 August:** Clear. Northerlies 20 km/h. The high will be 15°C and the low will be 10°C.

**Tuesday, 4 August:** Light rain. Southerlies 20 km/h. The high will be 10°C and the low will be 7°C.

**Wednesday, 5 August:** Partly cloudy. Southerlies 40 km/h. The high will be 9°C and the low will be 4°C."""

    violations = validate_spot_forecast(forecast, requirements)

    assert "TUESDAY 4 AUGUST must state the supplied rainfall amount 2 mm." in violations


def test_deterministic_contract_accepts_supplied_total() -> None:
    requirements = parse_spot_output_requirements(
        DETERMINISTIC_DATA,
        model_kind="deterministic",
    )
    forecast = """**Monday, 3 August:** Clear. Northerlies 20 km/h. The high will be 15°C and the low will be 10°C.

**Tuesday, 4 August:** Light rain totalling 2 mm. Southerlies 20 km/h. The high will be 10°C and the low will be 7°C.

**Wednesday, 5 August:** Partly cloudy. Southerlies 40 km/h. The high will be 9°C and the low will be 4°C."""

    assert validate_spot_forecast(forecast, requirements) == []


def test_contract_suppresses_sub_reportable_rainfall() -> None:
    requirements = parse_spot_output_requirements(
        """Date: TUESDAY 4 AUGUST
6 am 8° Light drizzle 0.5 mm/h S 10
 Low 7°C, High 10°C
 Total rainfall: 0.5 mm.
""",
        model_kind="deterministic",
    )
    contract = format_spot_output_contract(requirements)

    assert requirements[0].rainfall is None
    assert "rainfall 0.5 mm" not in contract
    assert "no reportable rainfall amount supplied" in contract


def test_partial_period_contract_does_not_force_full_day_temperatures() -> None:
    requirements = parse_spot_output_requirements(
        """Date: REST OF TODAY, TUESDAY 4 AUGUST
6 pm 8° Clear S 10
 Low 4°C, High 12°C
""",
        model_kind="deterministic",
    )
    contract = format_spot_output_contract(requirements)

    assert requirements[0].partial is True
    assert "low 4°C" not in contract
    assert "high 12°C" not in contract
    assert "overrides any request to be brief" not in contract
    assert "FINAL STYLE GUIDE" not in contract


def test_validator_can_check_facts_without_wording() -> None:
    requirements = parse_spot_output_requirements(
        DETERMINISTIC_DATA,
        model_kind="deterministic",
    )
    forecast = """**Monday, 3 August:** Clear. Northerlies will be present at 20 km/h. The high will be 15°C and the low will be 10°C.

**Tuesday, 4 August:** Light rain totalling 2 mm. Southerlies will persist at 20 km/h. The high will be 10°C and the low will be 7°C.

**Wednesday, 5 August:** Partly cloudy. Southerlies 40 km/h. The high will be 9°C and the low will be 4°C."""

    assert validate_spot_forecast(forecast, requirements)
    assert validate_spot_forecast(forecast, requirements, check_wording=False) == []
    assert validate_spot_forecast(
        forecast.replace("high will be 9°C", "high will be 8°C"),
        requirements,
        check_wording=False,
    )


def test_validator_rejects_wrong_or_duplicate_full_day_headings() -> None:
    requirements = parse_spot_output_requirements(
        DETERMINISTIC_DATA,
        model_kind="deterministic",
    )
    forecast = """**Sunday, 3 August:** Clear. The high will be 15°C and the low will be 10°C.

**Tuesday, 4 August:** Rain totalling 2 mm. The high will be 10°C and the low will be 7°C.

**Tuesday, 4 August:** Rain totalling 2 mm. The high will be 10°C and the low will be 7°C.

**Wednesday, 5 August:** Clear. The high will be 9°C and the low will be 4°C."""

    violations = validate_spot_forecast(forecast, requirements)

    assert "TOMORROW, MONDAY 3 AUGUST heading must use the supplied weekday Monday." in violations
    assert "Duplicate forecast period for 4 august." in violations


def test_ensemble_contract_rejects_unapproved_scenario_total() -> None:
    requirements = parse_spot_output_requirements(ENSEMBLE_DATA, model_kind="ensemble")
    contract = format_spot_output_contract(requirements)

    assert requirements[0].forbidden_rainfall == ("7 mm",)
    assert "no approved rainfall amount; do not use an individual scenario total" in contract

    forecast = (
        "**Wednesday, 5 August:** There is a 40% chance of rain, potentially bringing 7 mm. "
        "Northwesterlies 10 km/h. The high will be 23°C and the low will be 14°C to 17°C."
    )

    violations = validate_spot_forecast(forecast, requirements)

    assert "WEDNESDAY 5 AUGUST uses unapproved Scenario rainfall amount 7 mm." in violations


@pytest.mark.parametrize(
    ("bad_text", "message"),
    [
        ("Northerlies will be present during the day.", 'Use direct wording instead of "will be present".'),
        (
            "Southwesterly winds will persist.",
            'Use direct wind wording instead of saying winds "will persist".',
        ),
        (
            "Gusts reaching up to 60 km/h.",
            'Replace "gusts reaching/hitting up to" with "gusts up to" or "gusting".',
        ),
        (
            "Heavy wind gusts up to 110 km/h will be a major factor.",
            "Let wind values convey strength; remove vague or inflated wind wording.",
        ),
        (
            "Southwesterlies 20 km/h will be common.",
            'Use direct wind wording instead of saying winds "will be common".',
        ),
        (
            "Strong winds during the morning and afternoon.",
            'Compress an unchanged "morning and afternoon" period to "during/through the day".',
        ),
        (
            "Rain during the evening and late evening.",
            'Do not pair the nested timing labels "evening and late evening".',
        ),
        (
            "Light rain may return late in the night.",
            'Use "late evening" before midnight or "early morning" after midnight, not "tonight/late in the night".',
        ),
    ],
)
def test_wording_validator_flags_observed_gemma_failures(bad_text: str, message: str) -> None:
    assert message in validate_spot_forecast(bad_text, [])


def test_correction_prompt_is_bounded_and_preserves_non_precip_facts() -> None:
    original = (
        "**Tuesday, 4 August:** Light rain. Southerlies will be present at 20 km/h, "
        "gusts reaching up to 60 km/h. The high will be 10°C and the low will be 7°C."
    )
    corrected = (
        "**Tuesday, 4 August:** Light rain totalling 2 mm. Southerlies 20 km/h, "
        "gusting 60 km/h. The high will be 10°C and the low will be 7°C."
    )
    system_prompt, user_prompt = build_spot_correction_prompts(
        original,
        "--- MANDATORY OUTPUT CONTRACT ---\n- TUESDAY 4 AUGUST: rainfall 2 mm.",
        ["Missing rainfall total."],
    )

    assert "do not perform extended reasoning" in system_prompt
    assert "Correct only the listed violations" in user_prompt
    assert correction_preserves_other_numeric_facts(original, corrected)
    assert not correction_preserves_other_numeric_facts(original, corrected.replace("60 km/h", "70 km/h"))
    assert not correction_preserves_other_numeric_facts(
        original.replace("20 km/h", "10 to 20 km/h"),
        corrected.replace("20 km/h", "15 to 20 km/h"),
    )


def test_location_generation_runs_one_non_reasoning_correction(monkeypatch) -> None:
    payload = type(
        "Payload",
        (),
        {
            "name": "Wellington",
            "formatted_dataset": DETERMINISTIC_DATA,
            "dataset": [],
            "model_kind": "deterministic",
            "alerts": [],
            "units": TEST_UNITS,
            "geocode": type("Geocode", (), {"latitude": -41.3, "longitude": 174.8, "timezone": "UTC"})(),
        },
    )()
    location = LocationConfig(name="Wellington")
    config = ForecastConfig(llm="lms:test-model", location_wordiness="normal")
    settings = LLMSettings(
        model="test-model",
        api_key="local",
        provider="lmstudio",
        base_url="http://localhost:1234/v1",
    )
    initial = """**Monday, 3 August:** Clear. Northerlies 20 km/h. The high will be 15°C and the low will be 10°C.

**Tuesday, 4 August:** Light rain. Southerlies will be present at 20 km/h, gusts reaching up to 50 km/h. The high will be 10°C and the low will be 7°C.

**Wednesday, 5 August:** Partly cloudy. Southerlies 40 km/h. The high will be 9°C and the low will be 4°C."""
    corrected = initial.replace("Light rain.", "Light rain totalling 2 mm.").replace(
        "Southerlies will be present at 20 km/h, gusts reaching up to 50 km/h",
        "Southerlies 20 km/h, gusting 50 km/h",
    )
    correction_calls = []

    monkeypatch.setattr(
        executor,
        "_generate_text_with_fallback",
        lambda *args, **kwargs: (initial, settings, 1.5),
    )

    def fake_correction(prompt, system_prompt, correction_settings, **kwargs):
        correction_calls.append((prompt, system_prompt, correction_settings, kwargs))
        return corrected

    monkeypatch.setattr(executor, "generate_forecast_text", fake_correction)
    monkeypatch.setattr(executor, "consume_last_cost_cents", lambda: 0.25)
    monkeypatch.setattr(executor, "_snapshot_prompt", lambda *args, **kwargs: None)

    text, used_settings, cost = executor._generate_location_text_with_adaptive_thinning(
        location,
        config,
        payload,
        ibf_context="",
        impact_enabled=False,
    )

    assert text == corrected
    assert used_settings is settings
    assert cost == 1.75
    assert len(correction_calls) == 1
    assert correction_calls[0][2].temperature == 0.0
    assert correction_calls[0][2].max_tokens == 2000
    assert correction_calls[0][3] == {"reasoning": None, "thinking_level": None}


def test_compact_profile_is_selected_only_for_deterministic_spot_generation(monkeypatch) -> None:
    payload = type(
        "Payload",
        (),
        {
            "name": "Wellington",
            "formatted_dataset": DETERMINISTIC_DATA,
            "dataset": [],
            "model_kind": "deterministic",
            "alerts": [],
            "units": TEST_UNITS,
            "geocode": type(
                "Geocode",
                (),
                {"latitude": -41.3, "longitude": 174.8, "timezone": "UTC"},
            )(),
        },
    )()
    location = LocationConfig(name="Wellington")
    config = ForecastConfig(
        llm="lms:test-model",
        location_wordiness="normal",
        prompt_profile="compact",
    )
    settings = LLMSettings(model="test-model", api_key="local", provider="lmstudio")
    valid = """**Monday, 3 August:** Clear. Light winds. A high of 15°C and a low of 10°C.

**Tuesday, 4 August:** Light rain totalling 2 mm. Light winds. A high of 10°C and a low of 7°C.

**Wednesday, 5 August:** Partly cloudy. Southerlies 40 km/h. A high of 9°C and a low of 4°C."""
    captured = {}

    def fake_format(_payload, _dataset, *, compact=False):
        captured["compact"] = compact
        return DETERMINISTIC_DATA

    def fake_generate(_config, prompt, system_prompt, **_kwargs):
        captured["prompt"] = prompt
        captured["system_prompt"] = system_prompt
        return valid, settings, 0.0

    monkeypatch.setattr(executor, "_format_location_payload", fake_format)
    monkeypatch.setattr(executor, "_generate_text_with_fallback", fake_generate)

    text, used_settings, _cost = executor._generate_location_text_with_adaptive_thinning(
        location,
        config,
        payload,
        ibf_context="",
        impact_enabled=False,
    )

    assert text == valid
    assert used_settings is settings
    assert captured["compact"] is True
    assert "COMPACT DAILY SIGNALS" in captured["system_prompt"]
    assert captured["prompt"].startswith("Write the deterministic spoken spot forecast")


def test_location_generation_fails_closed_after_bad_correction(monkeypatch) -> None:
    payload = type(
        "Payload",
        (),
        {
            "name": "Wellington",
            "formatted_dataset": DETERMINISTIC_DATA,
            "dataset": [],
            "model_kind": "deterministic",
            "alerts": [],
            "units": TEST_UNITS,
            "geocode": type("Geocode", (), {"latitude": -41.3, "longitude": 174.8, "timezone": "UTC"})(),
        },
    )()
    location = LocationConfig(name="Wellington")
    config = ForecastConfig(llm="lms:test-model", location_wordiness="normal")
    settings = LLMSettings(model="test-model", api_key="local", provider="lmstudio")
    invalid = "**Tuesday, 4 August:** Southerlies will be present."

    monkeypatch.setattr(
        executor,
        "_generate_text_with_fallback",
        lambda *args, **kwargs: (invalid, settings, 0.0),
    )
    monkeypatch.setattr(executor, "generate_forecast_text", lambda *args, **kwargs: invalid)
    monkeypatch.setattr(executor, "consume_last_cost_cents", lambda: 0.0)
    monkeypatch.setattr(executor, "_snapshot_prompt", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="still violates the output contract"):
        executor._generate_location_text_with_adaptive_thinning(
            location,
            config,
            payload,
            ibf_context="",
            impact_enabled=False,
        )


def test_location_generation_publishes_factual_forecast_when_wording_remains(monkeypatch) -> None:
    payload = type(
        "Payload",
        (),
        {
            "name": "Wellington",
            "formatted_dataset": DETERMINISTIC_DATA,
            "dataset": [],
            "model_kind": "deterministic",
            "alerts": [],
            "units": TEST_UNITS,
            "geocode": type("Geocode", (), {"latitude": -41.3, "longitude": 174.8, "timezone": "UTC"})(),
        },
    )()
    location = LocationConfig(name="Wellington")
    config = ForecastConfig(llm="lms:test-model", location_wordiness="normal")
    settings = LLMSettings(model="test-model", api_key="local", provider="lmstudio")
    initial = """**Monday, 3 August:** Clear. Northerlies will be present at 20 km/h. The high will be 15°C and the low will be 10°C.

**Tuesday, 4 August:** Light rain totalling 2 mm. Southerlies will persist at 20 km/h. The high will be 10°C and the low will be 7°C.

**Wednesday, 5 August:** Partly cloudy. Southerlies 40 km/h. The high will be 9°C and the low will be 4°C."""
    correction_calls = []

    monkeypatch.setattr(
        executor,
        "_generate_text_with_fallback",
        lambda *args, **kwargs: (initial, settings, 1.5),
    )

    def fake_correction(prompt, system_prompt, correction_settings, **kwargs):
        correction_calls.append((prompt, system_prompt, correction_settings, kwargs))
        return initial

    monkeypatch.setattr(executor, "generate_forecast_text", fake_correction)
    monkeypatch.setattr(executor, "consume_last_cost_cents", lambda: 0.25)
    monkeypatch.setattr(executor, "_snapshot_prompt", lambda *args, **kwargs: None)

    text, used_settings, cost = executor._generate_location_text_with_adaptive_thinning(
        location,
        config,
        payload,
        ibf_context="",
        impact_enabled=False,
    )

    assert text == initial
    assert used_settings is settings
    assert cost == 1.75
    assert len(correction_calls) == 1
