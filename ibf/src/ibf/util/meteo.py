"""
Meteorological helper functions (WMO codes, wet bulb calculations, etc.).
"""

from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)


_WMO_CODES = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light rain",
    53: "moderate rain",
    55: "moderate rain",
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "light rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "light snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light rain showers",
    81: "moderate rain showers",
    82: "heavy rain showers",
    85: "light snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}


def wmo_weather(code: int | float | None) -> str:
    if code is None:
        return "unknown"
    try:
        return _WMO_CODES.get(int(code), f"Invalid code: {code}")
    except (ValueError, TypeError):
        return "unknown"


_DIRECTIONS = [
    "northerly",
    "northeasterly",
    "easterly",
    "southeasterly",
    "southerly",
    "southwesterly",
    "westerly",
    "northwesterly",
]


def degrees_to_compass(value: float | int | None) -> str:
    try:
        degrees = float(value)
    except (ValueError, TypeError):
        logger.debug("Invalid wind direction %s; returning 'variable'", value)
        return "variable"
    index = int((degrees + 22.5) / 45) % 8
    return _DIRECTIONS[index]


def round_windspeed(speed: float | int | None, unit: str = "kph") -> int:
    try:
        speed_float = float(speed)
    except (ValueError, TypeError):
        return 0

    unit = (unit or "").lower()
    if unit in ("kph", "kmh"):
        nearest = 10
    elif unit in ("mph", "kt", "kts", "mps"):
        nearest = 5
    else:
        return round(speed_float)

    rounded_value = nearest * int(round(speed_float / nearest))
    if rounded_value == 0 and speed_float > 0:
        return max(1, round(speed_float))
    return rounded_value


def calculate_relative_humidity(temp_c: float, dewpoint_c: float) -> float:
    try:
        t = float(temp_c)
        td = float(dewpoint_c)
    except (ValueError, TypeError):
        logger.debug("Invalid inputs for relative humidity: %s, %s", temp_c, dewpoint_c)
        return math.nan

    A = 17.27
    B = 237.7

    es_t = 611.2 * math.exp((A * t) / (B + t))
    es_td = 611.2 * math.exp((A * td) / (B + td))
    if es_t == 0:
        return 0.0
    return (es_td / es_t) * 100.0


def calculate_wet_bulb(temp_c: float, dewpoint_c: float) -> float:
    try:
        t = float(temp_c)
        td = float(dewpoint_c)
    except (ValueError, TypeError):
        logger.debug("Invalid inputs for wet bulb: %s, %s", temp_c, dewpoint_c)
        return math.nan

    rh = calculate_relative_humidity(t, td)
    if math.isnan(rh):
        return math.nan

    # Convert to Fahrenheit for Stull's approximation.
    t_f = t * 1.8 + 32

    try:
        term1 = math.atan(0.151977 * math.sqrt(rh + 8.313659))
        term2 = math.atan(t_f + rh)
        term3 = math.atan(rh - 1.676331)
        term4 = 0.00391838 * math.pow(rh, 1.5) * math.atan(0.023101 * rh)
        tw_f = term1 + term2 - term3 + term4 - 4.686035
    except ValueError:
        return math.nan

    return (tw_f - 32) / 1.8

