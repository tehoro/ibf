from __future__ import annotations

from ibf.config.models import ForecastConfig
from ibf.pipeline import executor
from ibf.web.scaffold import PLACEHOLDER_TEMPLATE


def _write_output(config: ForecastConfig, name: str, content: str) -> None:
    path = executor._build_destination_path(config, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_minimum_refresh_ignores_placeholder(tmp_path) -> None:
    config = ForecastConfig(web_root=tmp_path, minimum_refresh_minutes=60)
    placeholder = PLACEHOLDER_TEMPLATE.format(title="Test Location")
    _write_output(config, "Test Location", placeholder)

    assert executor._should_skip_recent_output(config, "Test Location", context="location") is False


def test_minimum_refresh_skips_fresh_real_output(tmp_path) -> None:
    config = ForecastConfig(web_root=tmp_path, minimum_refresh_minutes=60)
    _write_output(config, "Test Location", "<html><body>Real forecast content</body></html>")

    assert executor._should_skip_recent_output(config, "Test Location", context="location") is True


def test_minimum_refresh_override_can_disable(tmp_path) -> None:
    config = ForecastConfig(web_root=tmp_path, minimum_refresh_minutes=60)
    _write_output(config, "Test Location", "<html><body>Real forecast content</body></html>")

    assert (
        executor._should_skip_recent_output(
            config,
            "Test Location",
            context="location",
            minimum_refresh_minutes=0,
        )
        is False
    )


def test_forecast_failure_preserves_previous_valid_page(tmp_path) -> None:
    destination = tmp_path / "place" / "index.html"
    destination.parent.mkdir(parents=True)
    previous = "<html><div id='forecast-content'>A real earlier forecast</div></html>"
    destination.write_text(previous, encoding="utf-8")

    executor._preserve_or_render_unavailable_forecast(
        destination=destination,
        display_name="Test Place",
        issue_time="2026-07-27 12:00 UTC",
        model_label="Test model",
        model_ack_url=None,
    )

    assert destination.read_text(encoding="utf-8") == previous


def test_forecast_failure_replaces_internal_dataset_preview(tmp_path) -> None:
    destination = tmp_path / "place" / "index.html"
    destination.parent.mkdir(parents=True)
    destination.write_text(
        "<html>Area dataset preview for Test Place: /Users/example/cache.json</html>",
        encoding="utf-8",
    )

    executor._preserve_or_render_unavailable_forecast(
        destination=destination,
        display_name="Test Place",
        issue_time="2026-07-27 12:00 UTC",
        model_label="Test model",
        model_ack_url=None,
    )

    html = destination.read_text(encoding="utf-8")
    assert "Forecast temporarily unavailable" in html
    assert "Area dataset preview" not in html
    assert "/Users/example" not in html
    assert '<div id="translated-forecast-content">' not in html
