"""
External API clients used by the IBF pipeline.
"""

from .open_meteo import (
    ForecastRequest,
    ForecastResponse,
    fetch_forecast,
    ENSEMBLE_MODELS,
    DEFAULT_ENSEMBLE_MODEL,
)
from .alerts import AlertSummary, fetch_alerts
from .impact import ImpactContext, fetch_impact_context
from .geocode import GeocodeResult, geocode_name

__all__ = [
    "ForecastRequest",
    "ForecastResponse",
    "fetch_forecast",
    "ENSEMBLE_MODELS",
    "DEFAULT_ENSEMBLE_MODEL",
    "AlertSummary",
    "fetch_alerts",
    "ImpactContext",
    "fetch_impact_context",
    "GeocodeResult",
    "geocode_name",
]

