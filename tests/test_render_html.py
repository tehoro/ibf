from pathlib import Path

from ibf import __version__
from ibf.render import ForecastPage, render_forecast_page


def test_forecast_page_renders_llm_and_context_provenance(tmp_path: Path) -> None:
    destination = tmp_path / "forecast" / "index.html"
    render_forecast_page(
        ForecastPage(
            destination=destination,
            display_name="Otaki, New Zealand",
            issue_time="2026-07-28 09:00 NZST",
            forecast_text="**Tuesday, 28 July:** Fine.",
            translated_text="**Tūrei, 28 Hūrae:** Ka pai.",
            translation_language="Māori",
            ibf_context="### Existing Vulnerabilities\n• Flood-prone road.",
            context_provenance=(
                "Gemini Google Search (gemini-3.5-flash); "
                "generated 28 July 2026 at 08:30 NZST"
            ),
            forecast_llm="Gemini (gemini-3.5-flash-lite)",
            translation_llm="LM Studio (gemma-4-12b-it)",
        )
    )

    rendered = destination.read_text(encoding="utf-8")
    assert f">IBF</a> {__version__}, developed by" in rendered
    assert "Forecast language model: Gemini (gemini-3.5-flash-lite)." in rendered
    assert "Translation language model: LM Studio (gemma-4-12b-it)." in rendered
    assert "Context source: Gemini Google Search (gemini-3.5-flash)" in rendered
    assert "generated 28 July 2026 at 08:30 NZST" in rendered
    assert rendered.index("Data courtesy") < rendered.index("Forecast language model")
    assert rendered.index("Forecast language model") < rendered.index(
        "If you want to interactively request"
    )


def test_forecast_page_escapes_provenance_labels(tmp_path: Path) -> None:
    destination = tmp_path / "forecast" / "index.html"
    render_forecast_page(
        ForecastPage(
            destination=destination,
            display_name="Test",
            issue_time="now",
            forecast_text="Fine.",
            ibf_context="Context.",
            context_provenance="provider <unsafe>",
            forecast_llm="model <unsafe>",
        )
    )

    rendered = destination.read_text(encoding="utf-8")
    assert "provider &lt;unsafe&gt;" in rendered
    assert "model &lt;unsafe&gt;" in rendered
    assert "provider <unsafe>" not in rendered
    assert "model <unsafe>" not in rendered
