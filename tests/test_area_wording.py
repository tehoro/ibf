from copy import deepcopy

import pytest
from pydantic import ValidationError

from ibf.config.models import AreaConfig, ForecastConfig, LocationConfig
from ibf.llm.prompts import (
    UnitInstructions, build_area_system_prompt, build_regional_system_prompt,
    build_area_user_prompt, build_regional_user_prompt,
)
from ibf.llm.wind_descriptions import beaufort_category, format_wind_description_cues


@pytest.mark.parametrize('field,value', [
    ('region_naming_guidance', 'Prefer provinces'), ('windspeed_description', 'beaufort'),
])
def test_wording_options_are_area_only(field, value):
    area = AreaConfig(name='Ireland', locations=['Dublin'], **{field: value})
    assert getattr(area, field) == value
    with pytest.raises(ValidationError):
        ForecastConfig(**{field: value})
    with pytest.raises(ValidationError):
        LocationConfig(name='Dublin', **{field: value})


def test_wording_defaults_and_invalid_mode():
    area = AreaConfig(name='Ireland', locations=['Dublin'])
    assert area.region_naming_guidance is None
    assert area.windspeed_description == 'numeric'
    with pytest.raises(ValidationError):
        AreaConfig(name='Ireland', locations=['Dublin'], windspeed_description='bf')


@pytest.mark.parametrize('force,boundary_mps', enumerate(
    (0.3, 1.6, 3.4, 5.5, 8, 10.8, 13.9, 17.2, 20.8, 24.5, 28.5, 32.7), start=1,
))
def test_unrounded_beaufort_boundaries(force, boundary_mps):
    speed = boundary_mps * 3.6
    assert beaufort_category(speed - 0.00001) == force - 1
    assert beaufort_category(speed) == force


@pytest.mark.parametrize('value', [None, -1, float('nan'), float('inf'), True, '30'])
def test_missing_or_invalid_speed_is_not_calm(value):
    assert beaufort_category(value) is None


def test_exceptional_gust_not_hidden_when_mean_is_also_storm_force():
    data = [{'date': '2026-09-22', 'hours': [
        {'hour': '12:00', 'ensemble_members': {
            'member00': {'wind_speed': 90, 'wind_gust': 100},
        }},
    ]}]
    cue = format_wind_description_cues(data)
    assert 'mean: storm force; exceptional gust: storm force' in cue


def test_gust_cues_preserve_member_time_and_mean():
    data = [{'date': '2026-09-22', 'hours': [
        {'hour': '12:00', 'ensemble_members': {
            'member00': {'wind_speed': 30, 'wind_gust': 65},
            'member01': {'wind_speed': 30, 'wind_gust': 45},
            'member02': {'wind_speed': 55, 'wind_gust': 65},
        }},
        {'hour': '13:00', 'ensemble_members': {'member00': {'wind_speed': 0, 'wind_gust': None}}},
    ]}]
    before = deepcopy(data)
    cues = format_wind_description_cues(data)
    assert 'member00: mean: fresh breeze; notable gust: gale force' in cues
    assert 'member01: mean: fresh breeze' in cues
    assert 'member02: mean: near gale' in cues
    assert cues.count('notable gust:') == 1
    assert 'local hours 13:00: member00: mean: calm' in cues
    assert data == before


@pytest.mark.parametrize('builder', [build_area_system_prompt, build_regional_system_prompt])
@pytest.mark.parametrize('kind', ['deterministic', 'ensemble'])
@pytest.mark.parametrize('wind_unit', ['kph', 'mph', 'kt', 'mps'])
def test_beaufort_contract_replaces_numeric_requirement(builder, kind, wind_unit):
    units = UnitInstructions('celsius', None, 'mm', None, 'cm', None, wind_unit, None)
    normal = builder(units, model_kind=kind)
    assert normal == builder(units, model_kind=kind, windspeed_description='numeric')
    descriptive = builder(units, model_kind=kind, windspeed_description='beaufort')
    assert 'wind (with speed range)' not in descriptive
    assert 'wind direction and speed range using the required unit' not in descriptive
    assert 'DESCRIPTIVE WIND WORDING' in descriptive
    assert 'Never use a gust to describe the sustained wind' in descriptive


@pytest.mark.parametrize('builder', [build_area_user_prompt, build_regional_user_prompt])
def test_naming_guidance_independent_of_impacts_and_blank_is_noop(builder):
    kwargs = dict(area_name='Ireland', location_names=['Dublin'], wordiness='normal')
    baseline = builder('weather data', **kwargs)
    assert builder('weather data', **kwargs, region_naming_guidance='  ') == baseline
    prompt = builder('weather data', **kwargs, region_naming_guidance='Prefer Leinster')
    assert 'Prefer Leinster' in prompt
    assert 'not weather or impact evidence' in prompt
    assert 'ADDITIONAL CONTEXT' not in prompt
