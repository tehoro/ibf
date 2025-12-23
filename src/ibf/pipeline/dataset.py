"""
Transform Open-Meteo hourly data into the legacy day/hour structure.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List
import math

from ..util import (
    wmo_weather,
    degrees_to_compass,
    round_windspeed,
)
from ..util.snow import (
    compute_hourly_snow_level,
    extract_pressure_profile,
    rh_from_T_Td,
    estimate_snow_level_msl,
    should_check_snow_level,
    wet_bulb_dj,
)
from ..api.thin import select_members


def build_processed_days(
    forecast_raw: Dict[str, Any],
    *,
    timezone_name: str,
    temperature_unit: str = "celsius",
    precipitation_unit: str = "mm",
    windspeed_unit: str = "kph",
    thin_select: int = 16,
    location_altitude: float = 0.0,
    snow_levels_enabled: bool = False,
    highest_terrain_m: float | None = None,
    pressure_levels_hpa: list[float] | None = None,
) -> List[dict]:
    """
    Convert raw Open-Meteo data into day/hour structure and thin ensemble members.

    Organizes the flat hourly arrays into a nested structure of Days -> Hours -> Members.
    Also calculates derived fields like snow level and wind direction.

    Args:
        forecast_raw: The JSON response from Open-Meteo.
        timezone_name: Target timezone for day segmentation.
        precipitation_unit: Unit for precipitation ("mm" or "inch").
        windspeed_unit: Unit for wind speed ("kph", "mph", etc.).
        thin_select: Number of ensemble members to retain.
        location_altitude: Altitude of the location in meters (for snow level calc).

    Returns:
        A list of day dictionaries containing hourly forecasts.
    """
    hourly_units = forecast_raw.get("hourly_units", {})
    members = _detect_members(hourly_units)
    hourly = forecast_raw.get("hourly", {})
    timestamps = hourly.get("time", [])
    freezing_level_unit = _resolve_freezing_level_unit(hourly_units)

    tz = _resolve_timezone(timezone_name)
    now = datetime.now(tz)

    processed: Dict[str, Dict[str, Dict[str, dict]]] = {}

    # Pre-fetch keyed data for quick lookup
    indexed = _build_indexed_hourly(hourly, len(timestamps))

    for idx, ts in enumerate(timestamps):
        dt = _parse_timestamp(ts, tz)
        if dt is None:
            continue

        hours_old = (now - dt).total_seconds() / 3600
        if hours_old > 24:
            continue
        if dt < now:
            continue

        date_key = dt.strftime("%Y-%m-%d")
        hour_key = dt.strftime("%H:00")
        processed.setdefault(date_key, {}).setdefault(hour_key, {})

        for member in members:
            record = _build_member_record(
                member,
                idx,
                indexed,
                temperature_unit=temperature_unit,
                precipitation_unit=precipitation_unit,
                windspeed_unit=windspeed_unit,
                freezing_level_unit=freezing_level_unit,
                location_altitude=location_altitude,
                snow_levels_enabled=snow_levels_enabled,
                highest_terrain_m=highest_terrain_m,
                pressure_levels_hpa=pressure_levels_hpa,
            )
            if record:
                processed[date_key][hour_key][member] = record

    final_days: List[dict] = []
    for date_key in sorted(processed.keys()):
        hours = processed[date_key]
        if not hours:
            continue

        try:
            year, month, day = map(int, date_key.split("-"))
        except ValueError:
            continue

        day_label = _classify_day(datetime(year, month, day, tzinfo=tz), now)
        hour_blocks = [
            {"hour": hour_key, "ensemble_members": hours[hour_key]}
            for hour_key in sorted(hours.keys())
            if hours[hour_key]
        ]
        if not hour_blocks:
            continue

        final_days.append(
            {
                "date": date_key,
                "year": year,
                "month": month,
                "day": day,
                "dayofweek": day_label,
                "hours": hour_blocks,
            }
        )

    if not final_days:
        return []

    return select_members(final_days, thin_select=thin_select)


def _detect_members(hourly_units: Dict[str, Any]) -> List[str]:
    """Inspect the hourly_units payload to find available ensemble member suffixes."""
    members = ["member00"]
    for key in hourly_units.keys():
        if key.startswith("temperature_2m_member"):
            suffix = key.replace("temperature_2m_member", "")
            members.append(f"member{suffix.zfill(2)}")
    return sorted(set(members))


def _build_indexed_hourly(hourly: Dict[str, Any], count: int) -> Dict[str, List[Any]]:
    """Create aligned lists for all hourly arrays to support indexed lookups."""
    return {key: hourly.get(key, [None] * count) for key in hourly}


_FAHRENHEIT_UNITS = {"fahrenheit", "f"}
_INCH_UNITS = {"inch", "in", "inches"}
_FEET_UNITS = {"ft", "feet", "foot"}
_METER_UNITS = {"m", "meter", "meters", "metre", "metres"}


def _to_celsius(value: Any, unit: str) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if unit.lower() in _FAHRENHEIT_UNITS:
        return (numeric - 32.0) * (5.0 / 9.0)
    return numeric


def _to_mm(value: Any, unit: str) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if unit.lower() in _INCH_UNITS:
        return numeric * 25.4
    return numeric


def _to_meters(value: Any, unit: str) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if unit.lower() in _FEET_UNITS:
        return numeric * 0.3048
    return numeric


def _snow_level_units(temp_unit: str, precip_unit: str) -> str:
    if temp_unit.lower() in _FAHRENHEIT_UNITS or precip_unit.lower() in _INCH_UNITS:
        return "us"
    return "metric"


def _resolve_freezing_level_unit(hourly_units: Dict[str, Any]) -> str:
    if not isinstance(hourly_units, dict):
        return "m"
    unit_value = hourly_units.get("freezing_level_height")
    if not unit_value:
        for key, value in hourly_units.items():
            if key.startswith("freezing_level_height") and value:
                unit_value = value
                break
    if not isinstance(unit_value, str):
        return "m"
    token = unit_value.strip().lower()
    if token in _FEET_UNITS:
        return "ft"
    if token in _METER_UNITS:
        return "m"
    return "m"


def _snow_level_output(value_m: float | int) -> int:
    return int(round((float(value_m) * 3.28084) / 500.0) * 500)


def _parse_timestamp(value: str, tz: ZoneInfo) -> datetime | None:
    """Parse ISO timestamps (with optional Z suffix) and convert to the target TZ."""
    try:
        if value.endswith("Z"):
            base = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            base = datetime.fromisoformat(value)
        return base.astimezone(tz)
    except ValueError:
        return None


def _build_member_record(
    member: str,
    index: int,
    indexed: Dict[str, List[Any]],
    *,
    temperature_unit: str,
    precipitation_unit: str,
    windspeed_unit: str,
    freezing_level_unit: str,
    location_altitude: float,
    snow_levels_enabled: bool,
    highest_terrain_m: float | None,
    pressure_levels_hpa: list[float] | None,
) -> Dict[str, Any] | None:
    """Assemble the dictionary of derived values for a single member/hour."""
    base = "" if member == "member00" else f"_{member}"
    temperature = _safe_get(indexed, f"temperature_2m{base}", index)
    dewpoint = _safe_get(indexed, f"dewpoint_2m{base}", index)
    precipitation = _safe_get(indexed, f"precipitation{base}", index)
    precip_probability = _safe_get(indexed, f"precipitation_probability{base}", index)
    snowfall = _safe_get(indexed, f"snowfall{base}", index)
    weather_code = _safe_get(indexed, f"weather_code{base}", index)
    cloud_cover = _safe_get(indexed, f"cloud_cover{base}", index)
    wind_speed = _safe_get(indexed, f"wind_speed_10m{base}", index)
    wind_direction = _safe_get(indexed, f"wind_direction_10m{base}", index)
    wind_gusts = _safe_get(indexed, f"wind_gusts_10m{base}", index)

    required = [
        temperature,
        precipitation,
        snowfall,
        weather_code,
        cloud_cover,
        wind_speed,
        wind_direction,
    ]
    if any(v is None for v in required):
        return None

    snow_level: int | None = None
    snow_level_debug: dict | None = None
    if snow_levels_enabled:
        try:
            wx_code = int(weather_code) if weather_code is not None else 0
        except Exception:
            wx_code = 0
        temp_c = _to_celsius(temperature, temperature_unit)
        dewpoint_c = _to_celsius(dewpoint, temperature_unit)
        precip_mm = _to_mm(precipitation, precipitation_unit)
        snow_units = _snow_level_units(temperature_unit, precipitation_unit)

        # Avoid expensive or noisy calculations when snow is implausible.
        if (
            dewpoint_c is not None
            and temp_c is not None
            and precip_mm is not None
            and should_check_snow_level(precip_mm, wx_code, temp_c)
        ):
            freezing_level = _safe_get(indexed, f"freezing_level_height{base}", index)
            if freezing_level is not None:
                freezing_level_m = _to_meters(freezing_level, freezing_level_unit)
                if freezing_level_m is None:
                    freezing_level = None
                else:
                    freezing_level = freezing_level_m
                snow_level_m = _estimate_snow_level(
                    temp_c,
                    dewpoint_c,
                    snowfall,
                    precip_mm,
                    freezing_level,
                    location_altitude,
                    weather_code=wx_code,
                    max_terrain_m=highest_terrain_m,
                )
                if snow_level_m is not None:
                    snow_level = (
                        _snow_level_output(snow_level_m)
                        if snow_units == "us"
                        else int(snow_level_m)
                    )
            elif pressure_levels_hpa:
                surface_pressure = _safe_get(indexed, f"surface_pressure{base}", index)
                try:
                    surface_pressure_hpa = float(surface_pressure) if surface_pressure is not None else None
                except Exception:
                    surface_pressure_hpa = None

                if surface_pressure_hpa is not None:
                    profile = extract_pressure_profile(
                        indexed,
                        index,
                        pressure_levels_hpa=pressure_levels_hpa,
                        surface_pressure_hpa=surface_pressure_hpa,
                    )
                    if profile is not None:
                        try:
                            profile_snow = compute_hourly_snow_level(
                                precipitation_mm=float(precip_mm),
                                weather_code=wx_code,
                                temperature_c=float(temp_c),
                                dewpoint_c=float(dewpoint_c),
                                location_elevation_m=float(location_altitude),
                                surface_pressure_hpa=surface_pressure_hpa,
                                pressure_profile=profile,
                                units=snow_units,
                                max_terrain_m=highest_terrain_m,
                                precip_adjust=True,
                            )
                            snow_level = None if profile_snow < 0 else int(profile_snow)
                            if snow_level is None:
                                # Capture a small amount of debug data for downstream logging
                                # (executor emits a few sample hours when computed_hours=0).
                                raw_est = estimate_snow_level_msl(
                                    z_station_m=float(location_altitude),
                                    p_station_pa=surface_pressure_hpa * 100.0,
                                    t2m_c=float(temp_c),
                                    td2m_c=float(dewpoint_c),
                                    pressures_hpa=profile["pressures_hpa"],
                                    temps_c=profile["temps_c"],
                                    rhs_pct=profile["rhs_pct"],
                                    geop_heights_m=profile["geop_heights_m"],
                                    precip_rate_mm_per_hr=float(precip_mm),
                                    apply_precip_adjustment=True,
                                )
                                snow_level_debug = {
                                    "method": "profile",
                                    "raw_estimate_m": float(raw_est) if math.isfinite(raw_est) else None,
                                }
                        except Exception:
                            snow_level = None

    record: Dict[str, Any] = {
        "temperature": _round_value(temperature, 1),
        "precipitation": _round_value(
            precipitation, 1 if precipitation_unit == "mm" else 2
        ),
        "snowfall": _round_value(snowfall, 1),
        "weather": wmo_weather(weather_code),
        "cloud_cover": int(cloud_cover) if cloud_cover is not None else None,
        "wind_direction": degrees_to_compass(wind_direction),
        "wind_speed": round_windspeed(wind_speed, windspeed_unit),
        "wind_gust": round_windspeed(wind_gusts, windspeed_unit) if wind_gusts else 0,
        "snow_level": int(snow_level) if snow_level is not None else None,
    }
    if snow_level_debug is not None:
        record["_snow_level_debug"] = snow_level_debug

    # Probability of precipitation (POP) is typically available only for deterministic models.
    # If absent or invalid, omit it entirely.
    if isinstance(precip_probability, (int, float)):
        try:
            pop_int = int(round(float(precip_probability)))
            if 0 <= pop_int <= 100:
                record["pop"] = pop_int
        except Exception:
            pass

    return record


def _safe_get(indexed: Dict[str, List[Any]], key: str, idx: int) -> Any:
    """Return indexed hourly data, guarding against missing arrays or bounds."""
    values = indexed.get(key)
    if values is None or idx >= len(values):
        return None
    return values[idx]


def _round_value(value: Any, digits: int) -> float:
    """Round numeric values safely, defaulting to 0.0 if conversion fails."""
    try:
        return round(float(value), digits)
    except (ValueError, TypeError):
        return 0.0


def _estimate_snow_level(
    temperature: Any,
    dewpoint: Any,
    snowfall: Any,
    precipitation: Any,
    freezing_level: Any,
    location_altitude: float,
    *,
    weather_code: int,
    max_terrain_m: float | None = None,
) -> int | None:
    """Estimate snow level altitude based on freezing level and wet-bulb temperature."""
    try:
        fzl = float(freezing_level)
    except (TypeError, ValueError):
        fzl = None

    try:
        precip = float(precipitation)
    except (TypeError, ValueError):
        precip = 0.0
    try:
        temp_c = float(temperature)
    except (TypeError, ValueError):
        temp_c = float("nan")
    try:
        dewpoint_c = float(dewpoint)
    except (TypeError, ValueError):
        dewpoint_c = float("nan")

    if not should_check_snow_level(precip, int(weather_code or 0), temp_c):
        return None

    if math.isnan(dewpoint_c):
        return None

    # Estimate station pressure from altitude using a standard atmosphere approximation.
    # This is good enough for snow-level diagnostics and avoids needing another field.
    try:
        z = float(location_altitude)
    except (TypeError, ValueError):
        z = 0.0
    p_pa = 101325.0 * math.pow(max(0.0, 1.0 - 2.25577e-5 * z), 5.25588)
    rh_pct = rh_from_T_Td(temp_c, dewpoint_c)
    wet_bulb = wet_bulb_dj(temp_c, rh_pct, p_pa)
    if math.isnan(wet_bulb):
        return None

    alt_diff = (fzl - location_altitude) if fzl is not None else None
    if alt_diff is None or abs(alt_diff) < 10:
        lapse_rate = 0.0065
    else:
        lapse_rate = max(0.001, min(0.015, (temp_c - wet_bulb) / alt_diff))
    first_guess = (wet_bulb - 1.0) / lapse_rate + location_altitude if lapse_rate > 0 else fzl

    if precip == 0 or (fzl is not None and fzl <= location_altitude):
        return None

    snow_level = min(first_guess, fzl - 100) if fzl is not None else first_guess
    if snow_level < location_altitude or snow_level > (location_altitude + 3000):
        return None

    if max_terrain_m is not None and math.isfinite(float(max_terrain_m)):
        terrain_threshold = float(max_terrain_m) - 300.0
        if snow_level > terrain_threshold:
            return None

    return round(snow_level, -2)


def _classify_day(forecast_dt: datetime, current_dt: datetime) -> str:
    """Return human-friendly labels (e.g., 'Tomorrow, Monday') for each forecast day."""
    forecast_date = forecast_dt.date()
    current_date = current_dt.date()
    day_name = forecast_dt.strftime("%A")

    if forecast_date == current_date:
        hour = current_dt.hour
        if hour >= 22:
            return f"Rest of the evening, {day_name}"
        if hour > 15:
            return f"This evening, {day_name}"
        if hour > 10:
            return f"This afternoon and evening, {day_name}"
        if hour >= 6:
            return f"Rest of today, {day_name}"
        return f"Today, {day_name}"
    if forecast_date == current_date + timedelta(days=1):
        return f"Tomorrow, {day_name}"
    if forecast_date < current_date:
        return "Past"
    return day_name


def _resolve_timezone(name: str) -> ZoneInfo:
    """Return a ZoneInfo instance, falling back to UTC if the name is invalid."""
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")
