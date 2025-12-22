from ibf.pipeline.executor import _resolve_forecast_days


def test_forecast_days_zero_defaults_to_one() -> None:
    assert _resolve_forecast_days(0, 4) == 1
    assert _resolve_forecast_days("0", 4) == 1


def test_forecast_days_negative_defaults_to_one() -> None:
    assert _resolve_forecast_days(-2, 4) == 1


def test_forecast_days_invalid_uses_fallback() -> None:
    assert _resolve_forecast_days("not-a-number", 4) == 4
    assert _resolve_forecast_days(None, 4) == 4


def test_forecast_days_positive_pass_through() -> None:
    assert _resolve_forecast_days(3, 4) == 3
    assert _resolve_forecast_days("5", 4) == 5
