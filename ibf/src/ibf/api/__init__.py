"""
External API clients used by the IBF pipeline.
"""

from .open_meteo import ForecastRequest, ForecastResponse, fetch_forecast
from .alerts import AlertSummary, fetch_alerts
from .impact import ImpactContext, fetch_impact_context
from .geocode import GeocodeResult, geocode_name

__all__ = [
    "ForecastRequest",
    "ForecastResponse",
    "fetch_forecast",
    "AlertSummary",
    "fetch_alerts",
    "ImpactContext",
    "fetch_impact_context",
    "GeocodeResult",
    "geocode_name",
]

