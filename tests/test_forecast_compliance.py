from __future__ import annotations

import pytest

from ibf.config.models import ForecastConfig, LocationConfig
from ibf.llm.compliance import (
    SpotPeriodRequirement,
    build_spot_correction_prompts,
    correction_preserves_other_numeric_facts,
    format_spot_output_contract,
    parse_spot_output_requirements,
    postprocess_compact_spot_output,
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
    assert "MONDAY 3 AUGUST: low 10°C; high 15°C." in contract
    assert "describe precipitation qualitatively" in contract
    assert "no reportable rainfall amount supplied" not in contract

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
    assert "TUESDAY 4 AUGUST: low 7°C; high 10°C." in contract
    assert "no reportable rainfall amount supplied" not in contract


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


def test_validator_rejects_a_different_weekday_inside_period_body() -> None:
    requirements = [
        SpotPeriodRequirement(
            source_label="THIS AFTERNOON AND EVENING, WEDNESDAY 5 AUGUST",
            date_key="5 august",
            partial=True,
            weekday="wednesday",
        )
    ]
    forecast = (
        "**THIS AFTERNOON AND EVENING, WEDNESDAY 5 AUGUST:** Clear skies. "
        "Northwesterlies, turning easterly before returning early Thursday morning."
    )

    violations = validate_spot_forecast(forecast, requirements)

    assert (
        "THIS AFTERNOON AND EVENING, WEDNESDAY 5 AUGUST must not describe Thursday "
        "inside this forecast period."
    ) in violations


def test_validator_allows_cross_day_wording_when_alerts_are_present() -> None:
    requirements = [
        SpotPeriodRequirement(
            source_label="WEDNESDAY 5 AUGUST",
            date_key="5 august",
            partial=False,
            weekday="wednesday",
        )
    ]
    forecast = (
        "**WEDNESDAY 5 AUGUST:** A wind warning remains in force until early Thursday morning."
    )

    assert validate_spot_forecast(forecast, requirements, alerts_present=True) == []


def test_ensemble_contract_rejects_unapproved_scenario_total() -> None:
    requirements = parse_spot_output_requirements(ENSEMBLE_DATA, model_kind="ensemble")
    contract = format_spot_output_contract(requirements)

    assert requirements[0].forbidden_rainfall == ("7 mm",)
    assert "Never use an individual scenario total" in contract
    assert "no approved rainfall amount" not in contract

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


def test_compact_profile_uses_raw_deterministic_spot_data(monkeypatch) -> None:
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
    valid = """**Monday, 3 August:** Clear. Light winds. A low of 10°C and a high of 15°C.

**Tuesday, 4 August:** Light rain totalling 2 mm. Light winds. A low of 7°C and a high of 10°C.

**Wednesday, 5 August:** Partly cloudy. Southerlies 40 km/h. A low of 4°C and a high of 9°C."""
    captured = {}

    def fake_generate(_config, prompt, system_prompt, **_kwargs):
        captured["prompt"] = prompt
        captured["system_prompt"] = system_prompt
        return valid, settings, 0.0

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
    assert "raw hourly rows" in captured["system_prompt"]
    assert "COMPACT DAILY SIGNALS" not in captured["system_prompt"]
    assert captured["prompt"].startswith("Write the deterministic spoken spot forecast")
    assert "midnight 10° Clear sky N 20 gust 40" not in captured["prompt"]
    assert "midnight 10° Light rain S 20 gust 50" not in captured["prompt"]
    assert "midnight 8° Partly cloudy S 40 gust 90" in captured["prompt"]


def test_compact_gust_filter_changes_only_hourly_rows_at_or_below_floor() -> None:
    formatted = """ACTIVE ALERTS:
Description: Severe gusts reaching 50 km/h.

Date: WEDNESDAY 5 AUGUST
1pm 8° Partly cloudy S 20 gust 50
2pm 9° Partly cloudy S 30 gust 60
"""

    filtered = executor._filter_unreportable_compact_gusts(
        formatted,
        gust_reporting_floor=50,
    )

    assert "Description: Severe gusts reaching 50 km/h." in filtered
    assert "1pm 8° Partly cloudy S 20\n" in filtered
    assert "2pm 9° Partly cloudy S 30 gust 60" in filtered


def test_compact_short_dry_period_uses_one_broad_sky_cue() -> None:
    cloud_values = [34, 79, 81, 71, 49, 20, 1]
    weather_values = [
        "Mainly clear",
        "Partly cloudy",
        "Overcast",
        "Partly cloudy",
        "Mainly clear",
        "Mainly clear",
        "Clear sky",
    ]
    hours = [
        {
            "hour": f"{hour}:00",
            "ensemble_members": {
                "member00": {
                    "weather": weather,
                    "cloud_cover": cloud,
                    "precipitation": 0.0,
                    "snowfall": 0.0,
                }
            },
        }
        for hour, weather, cloud in zip(
            range(17, 24),
            weather_values,
            cloud_values,
            strict=True,
        )
    ]
    dataset = [{"dayofweek": "Rest of Today, Thursday", "hours": hours}]
    formatted = """Date: REST OF TODAY, THURSDAY 6 AUGUST

5pm 11° Mainly clear cc34 SE 10
6pm 10° Partly cloudy cc79 S 10
7pm 9° Overcast cc81 S 10
8pm 8° Partly cloudy cc71 S 10
9pm 7° Mainly clear cc49 SW 10
10pm 6° Mainly clear cc20 SW 10
11pm 4° Clear sky cc1 SW 10

Date: FRIDAY 7 AUGUST
midnight 3° Clear sky cc2 SW 10
"""

    prepared = executor._prepare_compact_short_period_sky_data(formatted, dataset)

    assert "First-period broad sky (use instead of hourly sky changes): Mainly clear." in prepared
    first_period, second_period = prepared.split("Date: FRIDAY 7 AUGUST")
    assert "Partly cloudy" not in first_period
    assert "Overcast" not in first_period
    assert "cc" not in first_period
    assert "midnight 3° Clear sky cc2" in second_period


def test_compact_short_period_sky_cue_preserves_wet_or_full_day_data() -> None:
    wet_dataset = [
        {
            "dayofweek": "This Evening, Thursday",
            "hours": [
                {
                    "hour": "18:00",
                    "ensemble_members": {
                        "member00": {
                            "weather": "Light rain",
                            "cloud_cover": 90,
                            "precipitation": 0.2,
                            "snowfall": 0.0,
                        }
                    },
                },
                {
                    "hour": "19:00",
                    "ensemble_members": {
                        "member00": {
                            "weather": "Overcast",
                            "cloud_cover": 90,
                            "precipitation": 0.0,
                            "snowfall": 0.0,
                        }
                    },
                },
            ],
        }
    ]
    formatted = "Date: THIS EVENING, THURSDAY 6 AUGUST\n6pm 10° Light rain cc90 0.2 mm/h"

    assert executor._prepare_compact_short_period_sky_data(formatted, wet_dataset) == formatted

    wet_dataset[0]["dayofweek"] = "Friday"
    assert executor._prepare_compact_short_period_sky_data(formatted, wet_dataset) == formatted


def test_compact_output_postprocessing_enforces_objective_period_rules() -> None:
    forecast = """**THIS AFTERNOON AND EVENING, TUESDAY 4 AUGUST:** Light rain this afternoon. Southwesterlies, gusting to 60 km/h. A low of 3°C and a high of 5°C.

**WEDNESDAY 5 AUGUST:** Overcast early this morning, clearing in the afternoon. Light winds. A high of 7°C and a low of -2°C.

**THURSDAY 6 AUGUST:** Clear, becoming cloudy late this evening. Southwesterly winds, gusting to 30 km/h late in the evening. A low of -4°C, a high of 8°C."""

    processed = postprocess_compact_spot_output(
        forecast,
        gust_reporting_floor=50,
    )

    assert "this afternoon" in processed
    assert "A high of 5°C and a low of 3°C" in processed
    assert "Overcast early in the morning, clearing in the afternoon" in processed
    assert "A low of -2°C and a high of 7°C" in processed
    assert "cloudy late in the evening" in processed
    assert "Southwesterly winds." in processed
    assert "gusting to 30 km/h" not in processed
    assert "A low of -4°C and a high of 8°C" in processed
    assert "gusting to 60 km/h" in processed


def test_compact_output_postprocessing_preserves_alert_gust_facts() -> None:
    forecast = """**WEDNESDAY 5 AUGUST:** A MetService Strong Wind Watch is in force this evening, with gusts reaching 50 km/h. A high of 8°C and a low of 2°C."""

    processed = postprocess_compact_spot_output(
        forecast,
        gust_reporting_floor=50,
        alerts_present=True,
    )

    assert "in force in the evening, with gusts reaching 50 km/h" in processed
    assert "A low of 2°C and a high of 8°C" in processed


def test_compact_output_postprocessing_removes_redundant_steady_timing() -> None:
    forecast = """**THIS AFTERNOON AND EVENING, WEDNESDAY 5 AUGUST:** Mainly clear to clear skies this afternoon, remaining clear through the evening. Southerly winds. A high of 10°C and a low of 3°C.

**THURSDAY 6 AUGUST:** Clear skies all day, with light winds throughout. A low of 0°C and a high of 10°C.

**FRIDAY 7 AUGUST:** Clear skies in the morning, becoming overcast from late morning and remaining so through much of the afternoon and evening. Northerly winds throughout the day. A low of 8°C and a high of 15°C.

**SATURDAY 8 AUGUST:** Mostly clear to partly cloudy in the morning, becoming overcast from late morning through the afternoon and evening. A low of 3°C and a high of 14°C."""

    processed = postprocess_compact_spot_output(
        forecast,
        gust_reporting_floor=50,
    )

    assert "Mainly clear to clear skies. Southerly winds." in processed
    assert "Clear skies with light winds." in processed
    assert "Clear skies at first, becoming overcast from late morning." in processed
    assert "Northerly winds." in processed
    assert "Mostly clear to partly cloudy at first, becoming overcast from late morning." in processed
    assert "throughout the afternoon and evening" not in processed
    assert "through much of the afternoon and evening" not in processed
    assert "throughout the day" not in processed


def test_compact_timing_postprocessing_preserves_precipitation_duration_and_alerts() -> None:
    forecast = """**FRIDAY 7 AUGUST:** Rain throughout the day, easing late evening. A low of 5°C and a high of 9°C."""

    processed = postprocess_compact_spot_output(
        forecast,
        gust_reporting_floor=50,
    )
    alert_processed = postprocess_compact_spot_output(
        "**FRIDAY 7 AUGUST:** A rain warning remains in force throughout the day.",
        gust_reporting_floor=50,
        alerts_present=True,
    )

    assert "Rain throughout the day" in processed
    assert "warning remains in force throughout the day" in alert_processed


def test_compact_output_postprocessing_normalises_final_wording_tweaks() -> None:
    forecast = """**THURSDAY 6 AUGUST:** A clear day from the start, with light winds. A low of -1°C and a high of 11°C.

**FRIDAY 7 AUGUST:** Light rain developing around midday, though snow may fall down to about 500 metres. A low of -4°C and a high of 8°C.

**SATURDAY 8 AUGUST:** Overcast with light rain throughout the day, including a total of 1 mm. Snow mainly settling above 700 metres. A low of 5°C and a high of 11°C."""

    processed = postprocess_compact_spot_output(
        forecast,
        gust_reporting_floor=50,
    )

    assert "Clear with light winds." in processed
    assert "giving 1 mm in total" in processed
    assert "with snow down to about 500 m" in processed
    assert "Snow above about 700 m." in processed
    assert "including a total" not in processed
    assert "mainly settling" not in processed


def test_compact_output_postprocessing_normalises_reported_prose_defects() -> None:
    forecast = """**THIS AFTERNOON AND EVENING, THURSDAY 6 AUGUST:** Clear and mainly clear for the rest of the day, with light winds turning easterly later. A high of 10°C and a low of 3°C.

**FRIDAY 7 AUGUST:** Clear skies, with temperatures rising to a high of 12°C before falling away in the evening. Light winds. A low of 2°C and a high of 12°C.

**SATURDAY 8 AUGUST:** Rain developing in the morning. Easterly winds. Giving 56 mm in total. A low of 7°C and a high of 11°C.

**SUNDAY 9 AUGUST:** Moderate rain and thunderstorms. Strong northeasterly winds will gust to 80 km/h during the afternoon, before clearing in the evening. Northerly winds, strengthening to a north-easterly later. A low of 8°C and a high of 13°C.

**MONDAY 10 AUGUST:** A high chance of rain. Temperatures rise to 14°C in the afternoon. Light winds. A low of 6°C and a high of 14°C."""

    processed = postprocess_compact_spot_output(
        forecast,
        gust_reporting_floor=50,
    )

    assert "Mainly clear with light winds." in processed
    assert "Clear skies. Light winds. A low of 2°C and a high of 12°C." in processed
    assert "temperatures rising to a high" not in processed
    assert "Rain developing in the morning, giving 56 mm in total. Easterly winds." in processed
    assert "Giving 56 mm" not in processed
    assert "easing in the evening as the rain clears" in processed
    assert "strengthening and turning north-easterly later" in processed
    assert "before clearing" not in processed
    assert "strengthening to" not in processed
    assert "A high chance of rain. Light winds. A low of 6°C and a high of 14°C." in processed


def test_compact_output_postprocessing_removes_snow_may_reach_area_wording() -> None:
    forecast = """**SATURDAY 8 AUGUST:** Snow may reach the area, though it is mainly settling above 1800 metres. A low of 7°C and a high of 15°C.

**SUNDAY 9 AUGUST:** Light rain may reach the area, with snow mainly settling above 1500 m to 1800 m. A low of 1°C and a high of 8°C."""

    processed = postprocess_compact_spot_output(
        forecast,
        gust_reporting_floor=50,
    )

    assert "Snow above about 1800 m." in processed
    assert "Snow above about 1500 m." in processed
    assert "may reach the area" not in processed
    assert "1500 m to 1800 m" not in processed


def test_compact_output_postprocessing_removes_will_be_present_only() -> None:
    forecast = """**WEDNESDAY 5 AUGUST:** Southwesterly winds will be present, turning to southerlies later. A low of 2°C and a high of 10°C.

**THURSDAY 6 AUGUST:** Light easterly winds will be present throughout the day, turning to northwesterlies in the afternoon. A low of 1°C and a high of 9°C.

**FRIDAY 7 AUGUST:** Easterly winds will be present in the morning, turning to northwesterlies and then northerlies. A low of 4°C and a high of 11°C."""

    processed = postprocess_compact_spot_output(
        forecast,
        gust_reporting_floor=50,
    )

    assert "will be present" not in processed
    assert "Southwesterly winds, turning to southerlies later." in processed
    assert "Light winds." in processed
    assert "Easterly winds in the morning, turning to northwesterlies and then northerlies" in processed


def test_compact_output_postprocessing_removes_implicit_persistence() -> None:
    forecast = """**FRIDAY 7 AUGUST:** Mainly clear at first, becoming overcast from the morning and remaining so for the rest of the day. Light winds, turning westerly later. A low of 0°C and a high of 11°C.

**SATURDAY 8 AUGUST:** Overcast with light rain developing in the early morning, leading to a period of moderate rain showers throughout the day and evening, giving 15 mm. Winds remain light, becoming easterly later. A low of 6°C and a high of 13°C.

**SUNDAY 9 AUGUST:** Rain developing in the morning and continuing through the afternoon before easing in the evening, giving 12 mm. A low of 7°C and a high of 12°C."""

    processed = postprocess_compact_spot_output(
        forecast,
        gust_reporting_floor=50,
    )

    assert "Mainly clear at first, becoming overcast from the morning." in processed
    assert "turning to moderate rain showers, giving 15 mm" in processed
    assert "Light winds." in processed
    assert "remaining so" not in processed
    assert "throughout the day and evening" not in processed
    assert "continuing through the afternoon before easing in the evening" in processed


def test_compact_output_postprocessing_normalises_latest_observed_wording() -> None:
    forecast = """**SATURDAY 8 AUGUST:** Mainly clear early on, turning overcast from late morning and remaining so for much of the day. A low of 4°C and a high of 12°C.

**SUNDAY 9 AUGUST:** Light rain developing in the early hours and continuing through most of the day, giving 6 mm in total. A low of 9°C and a high of 14°C.

**MONDAY 10 AUGUST:** Rain during the day, giving 8 mm in total, before clearing to a mostly sunny evening. A low of 11°C and a high of 15°C.

**TUESDAY 11 AUGUST:** Mostly cloudy with early clear spells, remaining overcast for the rest of the day. A low of 8°C and a high of 14°C.

**WEDNESDAY 12 AUGUST:** Moderate rain showers in the early morning, clearing to a mostly sunny afternoon, with 4 mm expected. Light winds. A low of 5°C and a high of 13°C."""

    processed = postprocess_compact_spot_output(
        forecast,
        gust_reporting_floor=50,
    )

    assert "turning overcast from late morning." in processed
    assert "Light rain developing in the early hours, giving 6 mm in total." in processed
    assert "clearing to a mostly clear evening" in processed
    assert "Mostly cloudy with early clear spells." in processed
    assert (
        "Moderate rain showers in the early morning, with 4 mm expected, "
        "clearing to a mostly sunny afternoon."
    ) in processed
    assert "remaining so" not in processed
    assert "continuing through most of the day" not in processed
    assert "sunny evening" not in processed


def test_compact_output_postprocessing_keeps_timing_inside_period() -> None:
    forecast = """**THIS EVENING, THURSDAY 6 AUGUST:** Cloudy, clearing late tonight. Temperatures fall to 4°C by early morning. Light rain through the night.

**FRIDAY 7 AUGUST:** Cloudy in the evening before clearing in the early morning. Light rain returning overnight. A low of 4°C and a high of 12°C."""

    processed = postprocess_compact_spot_output(
        forecast,
        gust_reporting_floor=50,
    )

    assert "clearing late this evening" in processed
    assert "by midnight" in processed
    assert "through the evening" in processed
    assert "Cloudy in the evening, then clearing late." in processed
    assert "Light rain returning late in the evening." in processed
    assert "early morning" not in processed
    assert "overnight" not in processed


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
