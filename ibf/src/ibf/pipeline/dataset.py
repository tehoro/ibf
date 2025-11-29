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
    calculate_wet_bulb,
)
from ..api.thin import select_members


def build_processed_days(
    forecast_raw: Dict[str, Any],
    *,
    timezone_name: str,
    precipitation_unit: str = "mm",
    windspeed_unit: str = "kph",
    thin_select: int = 16,
    location_altitude: float = 0.0,
) -> List[dict]:
    """
    Convert raw Open-Meteo data into day/hour structure and thin ensemble members.
    """
    members = _detect_members(forecast_raw.get("hourly_units", {}))
    hourly = forecast_raw.get("hourly", {})
    timestamps = hourly.get("time", [])

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
                precipitation_unit=precipitation_unit,
                windspeed_unit=windspeed_unit,
                location_altitude=location_altitude,
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
    members = ["member00"]
    for key in hourly_units.keys():
        if key.startswith("temperature_2m_member"):
            suffix = key.replace("temperature_2m_member", "")
            members.append(f"member{suffix.zfill(2)}")
    return sorted(set(members))


def _build_indexed_hourly(hourly: Dict[str, Any], count: int) -> Dict[str, List[Any]]:
    return {key: hourly.get(key, [None] * count) for key in hourly}


def _parse_timestamp(value: str, tz: ZoneInfo) -> datetime | None:
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
    precipitation_unit: str,
    windspeed_unit: str,
    location_altitude: float,
) -> Dict[str, Any] | None:
    base = "" if member == "member00" else f"_{member}"
    temperature = _safe_get(indexed, f"temperature_2m{base}", index)
    dewpoint = _safe_get(indexed, f"dewpoint_2m{base}", index)
    precipitation = _safe_get(indexed, f"precipitation{base}", index)
    snowfall = _safe_get(indexed, f"snowfall{base}", index)
    weather_code = _safe_get(indexed, f"weather_code{base}", index)
    cloud_cover = _safe_get(indexed, f"cloud_cover{base}", index)
    wind_speed = _safe_get(indexed, f"wind_speed_10m{base}", index)
    wind_direction = _safe_get(indexed, f"wind_direction_10m{base}", index)
    wind_gusts = _safe_get(indexed, f"wind_gusts_10m{base}", index)
    fzl = _safe_get(indexed, f"freezing_level_height{base}", index)

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

    snow_level = _estimate_snow_level(
        temperature,
        dewpoint,
        snowfall,
        precipitation,
        fzl,
        location_altitude,
    )

    return {
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


def _safe_get(indexed: Dict[str, List[Any]], key: str, idx: int) -> Any:
    values = indexed.get(key)
    if values is None or idx >= len(values):
        return None
    return values[idx]


def _round_value(value: Any, digits: int) -> float:
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
) -> int | None:
    try:
        fzl = float(freezing_level)
    except (TypeError, ValueError):
        return None

    wet_bulb = calculate_wet_bulb(temperature, dewpoint)
    if math.isnan(wet_bulb):
        return None

    alt_diff = fzl - location_altitude
    lapse_rate = 0.0065 if abs(alt_diff) < 10 else max(0.001, min(0.015, (temperature - wet_bulb) / alt_diff))
    first_guess = (wet_bulb - 1.0) / lapse_rate + location_altitude if lapse_rate > 0 else fzl

    if precipitation == 0 or fzl <= location_altitude:
        return None

    snow_level = min(first_guess, fzl - 100)
    if snow_level < location_altitude or snow_level > (location_altitude + 3000):
        return None
    return round(snow_level, -2)


def _classify_day(forecast_dt: datetime, current_dt: datetime) -> str:
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
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")

