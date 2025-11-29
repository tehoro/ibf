"""
Formatting helpers that build the textual input for the LLM.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Iterable, List

import arrow
import numpy as np
import pytz

from ..api.alerts import AlertSummary
from ..util import convert_hour_to_ampm  # will add helper there


def format_location_dataset(
    dataset: List[dict],
    alerts: List[AlertSummary],
    tz_str: str,
    *,
    temperature_unit: str,
    precipitation_unit: str,
    snowfall_unit: str,
    windspeed_unit: str,
) -> str:
    if not dataset:
        return "Error: No valid forecast data received for formatting."

    alert_text = _format_alerts(alerts, dataset, tz_str)
    output_parts: List[str] = []

    for day in dataset:
        if not all(key in day for key in ("year", "month", "day", "dayofweek", "hours")):
            continue

        date_key = f" {day['year']}-{day['month']:02d}-{day['day']:02d} {day['dayofweek'].upper()}"
        date_heading = f"Date: {convert_date_string(date_key)}\n"

        hours = day.get("hours", [])
        if not hours:
            output_parts.append(f"{date_heading} No hourly data available.\n")
            continue

        ensemble_keys = list(hours[0].get("ensemble_members", {}).keys())
        members_output: List[str] = []
        daily_lows: List[float] = []
        daily_highs: List[float] = []
        daily_precip: List[float] = []
        daily_snow: List[float] = []

        for member in ensemble_keys:
            block_lines = [f"Scenario {member.replace('member', '')}:"]
            high_temp = -math.inf
            low_temp = math.inf
            total_precip = 0.0
            total_snow = 0.0
            has_data = False

            for hour_entry in hours:
                member_data = hour_entry.get("ensemble_members", {}).get(member)
                if not isinstance(member_data, dict):
                    continue

                has_data = True
                temp = member_data.get("temperature")
                precip_val = member_data.get("precipitation", 0.0)
                snowfall_val = member_data.get("snowfall", 0.0)
                wind_speed = member_data.get("wind_speed", 0.0)
                wind_gust = member_data.get("wind_gust", 0.0)
                wind_direction = member_data.get("wind_direction", "variable")

                if isinstance(temp, (int, float)):
                    high_temp = max(high_temp, temp)
                    low_temp = min(low_temp, temp)

                if isinstance(precip_val, (int, float)):
                    total_precip += precip_val
                if isinstance(snowfall_val, (int, float)):
                    total_snow += snowfall_val

                hour_label = convert_hour_to_ampm(_hour_from_string(hour_entry.get("hour", "0:00")))
                weather_desc = str(member_data.get("weather", "Unknown")).capitalize()
                snow_level = member_data.get("snow_level")

                precip_text = ""
                if isinstance(precip_val, (int, float)) and precip_val > 0:
                    precip_text = f", {precip_val:.1f} {precipitation_unit}/h"

                snow_text = ""
                if isinstance(snow_level, int) and snow_level > 0:
                    snow_text = f", snow down to about {snow_level} m"

                wind_text = _format_wind(wind_direction, wind_speed, wind_gust, windspeed_unit)

                temp_text = f"{_format_temp(temp, temperature_unit)}" if isinstance(temp, (int, float)) else "N/A"
                block_lines.append(f" {hour_label}: {temp_text}, {weather_desc}{precip_text}{snow_text}, {wind_text}")

            if has_data:
                summary = _member_summary(
                    high_temp,
                    low_temp,
                    total_precip,
                    total_snow,
                    temperature_unit,
                    precipitation_unit,
                    snowfall_unit,
                )
                block_lines.append(summary)
                members_output.append("\n".join(block_lines))

                if math.isfinite(high_temp) and math.isfinite(low_temp):
                    daily_highs.append(round(high_temp))
                    daily_lows.append(round(low_temp))
                daily_precip.append(round(total_precip, 1))
                daily_snow.append(round(total_snow, 1))

        if members_output:
            range_summary = calculate_range_summary(
                daily_lows,
                daily_highs,
                daily_precip,
                daily_snow,
                temperature_unit.capitalize()[0],
                precipitation_unit,
                snowfall_unit,
                _should_use_only_low(hours),
                _should_reverse_high_low(hours),
            )
            output_parts.append(f"{date_heading}" + "\n".join(members_output) + "\nRANGE SUMMARY:\n" + range_summary + "\n")

    final_text = "\n".join(part for part in output_parts if part.strip())
    return (alert_text + "\n" + final_text).strip() if alert_text else final_text.strip()


def format_area_dataset(area_name: str, locations: List[dict[str, Any]]) -> str:
    """
    Combine multiple location datasets into a single area-level text block.
    Each entry in `locations` must provide: name, latitude, longitude, timezone, text.
    """
    if not locations:
        return ""

    parts = [
        f"AREA CONTEXT: {area_name}",
        "Each block below is the processed dataset for a representative location.",
    ]

    for entry in locations:
        name = entry.get("name", "Unknown Location")
        lat = entry.get("latitude")
        lon = entry.get("longitude")
        tz = entry.get("timezone", "UTC")
        text = entry.get("text", "").strip()
        header = f"### LOCATION: {name}"
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            header += f" ({lat:.4f}, {lon:.4f})"
        header += f" — Timezone: {tz}"
        parts.append(header)
        if text:
            parts.append(text)
        parts.append("<END LOCATION>")

    return "\n\n".join(parts).strip()


def _format_alerts(alerts: List[AlertSummary], dataset: List[dict], tz_str: str) -> str:
    if not alerts:
        return ""
    first_date = dataset[0].get("date")
    try:
        earliest = datetime.strptime(first_date, "%Y-%m-%d").date() if first_date else None
    except Exception:
        earliest = None

    lines = []
    for alert in alerts:
        if not alert.onset or not alert.expires:
            continue
        onset = arrow.get(alert.onset).to(tz_str)
        expires = arrow.get(alert.expires).to(tz_str)
        if earliest and expires.date() < earliest:
            continue
        lines.append(
            "\n".join(
                [
                    f"ALERT from {alert.source or 'N/A'}:",
                    f"Title: {alert.title or 'N/A'}",
                    f"Valid from: {onset.format('YYYY-MM-DD HH:mm ZZZ')}",
                    f"Expires: {expires.format('YYYY-MM-DD HH:mm ZZZ')}",
                    f"Description: {alert.description or 'N/A'}",
                ]
            )
        )
    return "ACTIVE ALERTS:\n" + "\n".join(lines) if lines else ""


def _hour_from_string(value: str) -> int:
    try:
        return int(value.split(":")[0])
    except Exception:
        return 0


def _format_temp(value: float, unit: str) -> str:
    symbol = "°C" if unit == "celsius" else "°F"
    return f"{round(value)}{symbol}"


def _format_wind(direction: str, speed: float, gust: float, unit: str) -> str:
    unit_display = "km/h" if unit == "kph" else unit
    if not isinstance(speed, (int, float)) or speed <= 0:
        return "wind calm"
    gust_part = ""
    if isinstance(gust, (int, float)) and gust - speed >= 10:
        gust_part = f" gust {int(gust)} {unit_display}"
    return f"{direction or 'variable'} {int(speed)} {unit_display}{gust_part}"


def _member_summary(
    high_temp: float,
    low_temp: float,
    total_precip: float,
    total_snow: float,
    temperature_unit: str,
    precipitation_unit: str,
    snowfall_unit: str,
) -> str:
    if not (math.isfinite(high_temp) and math.isfinite(low_temp)):
        return " No valid temperature data found for summary.\n"
    lines = [
        f" Low {round(low_temp)}°{temperature_unit.capitalize()[0]}, High {round(high_temp)}°{temperature_unit.capitalize()[0]}",
    ]
    if total_snow > 0:
        lines.append(f" Total snowfall: {round(total_snow)} {snowfall_unit}.")
    if total_precip > 0:
        precision = 0 if precipitation_unit == "mm" else 1
        lines.append(f" Total rainfall: {round(total_precip, precision)} {precipitation_unit}.")
    return "\n".join(lines)


def _should_use_only_low(hours: List[dict]) -> bool:
    if not hours:
        return False
    hour_int = _hour_from_string(hours[0].get("hour", "0:00"))
    return hour_int > 15


def _should_reverse_high_low(hours: List[dict]) -> bool:
    if not hours:
        return False
    hour_int = _hour_from_string(hours[0].get("hour", "0:00"))
    return 10 < hour_int <= 15


def calculate_range_summary(
    daily_lows: Iterable[float],
    daily_highs: Iterable[float],
    daily_precip: Iterable[float],
    daily_snow: Iterable[float],
    temp_unit_short: str,
    precip_unit: str,
    snow_unit: str,
    use_only_low: bool,
    reverse_high_and_low: bool,
) -> str:
    daily_lows = list(daily_lows)
    daily_highs = list(daily_highs)
    daily_precip = list(daily_precip)
    daily_snow = list(daily_snow)
    if not daily_lows or not daily_highs:
        return "N/A"

    summary_lines = []
    if use_only_low:
        summary_lines.append(f"Likely low {min(daily_lows)}°{temp_unit_short} to {max(daily_lows)}°{temp_unit_short}")
    elif reverse_high_and_low:
        summary_lines.append(f"Likely high {min(daily_highs)}°{temp_unit_short} to {max(daily_highs)}°{temp_unit_short}")
        summary_lines.append(f"Likely low {min(daily_lows)}°{temp_unit_short} to {max(daily_lows)}°{temp_unit_short}")
    else:
        summary_lines.append(f"Likely low {min(daily_lows)}°{temp_unit_short} to {max(daily_lows)}°{temp_unit_short}")
        summary_lines.append(f"Likely high {min(daily_highs)}°{temp_unit_short} to {max(daily_highs)}°{temp_unit_short}")

    precip_line = precipitation_or_snowfall_likely("precipitation", daily_precip, precip_unit)
    snow_line = precipitation_or_snowfall_likely("snowfall", daily_snow, snow_unit)
    if precip_line:
        summary_lines.append(precip_line)
    if snow_line:
        summary_lines.append(snow_line)
    return "\n".join(summary_lines)


def precipitation_or_snowfall_likely(label: str, values: List[float], unit: str) -> str:
    if not values:
        return ""
    positive = [v for v in values if v > 0]
    total_members = len(values)
    if not positive:
        return ""
    probability = _jeffreys_probability(len(positive), total_members)
    percentiles = estimate_percentiles(positive, 0.20)
    if any(math.isnan(x) for x in percentiles):
        return f"Estimated probability of {label}: {probability}%"
    lower = round(percentiles[0], 1 if unit != "mm" else 0)
    upper = round(percentiles[1], 1 if unit != "mm" else 0)
    return f"Estimated probability of {label}: {probability}%\nLikely {label} {lower} {unit} to {upper} {unit}"


def estimate_percentiles(values: Iterable[float], lower_fraction: float) -> tuple[float, float]:
    numeric = [x for x in values if isinstance(x, (int, float))]
    if len(numeric) < 2:
        return (math.nan, math.nan)
    numeric.sort()
    n = len(numeric)
    lower_position = lower_fraction * (n - 1)
    upper_position = (1 - lower_fraction) * (n - 1)
    lower_est = float(np.interp(lower_position, np.arange(n), numeric))
    upper_est = float(np.interp(upper_position, np.arange(n), numeric))
    return lower_est, upper_est


def _jeffreys_probability(occurrences: int, total: int) -> int:
    if total <= 0:
        return 0
    prob = (occurrences + 0.5) / (total + 1)
    return max(0, min(100, int(round(prob * 20) * 5)))


def convert_date_string(date_str: str) -> str:
    try:
        cleaned = date_str.strip()
        date_part, _, descriptor = cleaned.partition(" ")
        parsed = datetime.strptime(date_part, "%Y-%m-%d")
        day = parsed.strftime("%d").lstrip("0")
        month = parsed.strftime("%B").upper()
        descriptor = descriptor.strip()
        return f"{descriptor} {day} {month}" if descriptor else f"{parsed.strftime('%A').upper()} {day} {month}"
    except Exception:
        return date_str


def determine_current_season(latitude: float) -> str:
    month = datetime.now().month
    if month in (3, 4, 5):
        return "Spring" if latitude >= 0 else "Autumn"
    if month in (6, 7, 8):
        return "Summer" if latitude >= 0 else "Winter"
    if month in (9, 10, 11):
        return "Autumn" if latitude >= 0 else "Spring"
    return "Winter" if latitude >= 0 else "Summer"

