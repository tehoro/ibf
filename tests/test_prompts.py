from __future__ import annotations

import pytest

from ibf.llm.prompts import (
    UnitInstructions,
    build_area_system_prompt,
    build_regional_system_prompt,
    build_spot_system_prompt,
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
