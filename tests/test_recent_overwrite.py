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
