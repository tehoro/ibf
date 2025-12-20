from ibf.config.models import ForecastConfig, LocationConfig, AreaConfig
from ibf.pipeline.executor import _location_translation_language, _area_translation_language


def test_translation_language_precedence() -> None:
    config = ForecastConfig(
        translation_language="German",
        locations=[
            LocationConfig(name="Primary", translation_language="Spanish"),
            LocationConfig(name="Fallback"),
        ],
        areas=[
            AreaConfig(name="Area", locations=["Primary"], lang="French"),
            AreaConfig(name="DefaultArea", locations=["Fallback"]),
        ],
    )

    assert _location_translation_language(config.locations[0], config) == "Spanish"
    assert _location_translation_language(config.locations[1], config) == "German"

    assert _area_translation_language(config.areas[0], config) == "French"
    assert _area_translation_language(config.areas[1], config) == "German"


def test_translation_language_aliases() -> None:
    config = ForecastConfig(translation_lang="Italian")
    location = LocationConfig(name="Primary", translation_lang="French")
    area = AreaConfig(name="Area", locations=["Primary"], translation_lang="Spanish")

    assert config.translation_language == "Italian"
    assert _location_translation_language(location, config) == "French"
    assert _area_translation_language(area, config) == "Spanish"
