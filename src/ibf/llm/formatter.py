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
from ..util import convert_hour_to_ampm, round_windspeed  # will add helper there

PRECIP_HEAVY_THRESHOLD_MM = 10.0
PRECIP_HEAVY_THRESHOLD_IN = 0.5
_FAHRENHEIT_UNITS = {"fahrenheit", "f"}
_INCH_UNITS = {"inch", "in", "inches"}


def _snow_level_unit_label(temp_unit: str, precip_unit: str) -> str:
    if temp_unit.lower() in _FAHRENHEIT_UNITS or precip_unit.lower() in _INCH_UNITS:
        return "ft"
    return "m"


def _convert_temperature(value: Any, unit: str) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    unit = (unit or "").lower()
    if unit in _FAHRENHEIT_UNITS:
        return (float(value) * 9.0 / 5.0) + 32.0
    return float(value)


def _convert_precipitation(value: Any, unit: str) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    unit = (unit or "").lower()
    if unit in _INCH_UNITS:
        return float(value) / 25.4
    return float(value)


def _convert_snowfall(value: Any, unit: str) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    unit = (unit or "").lower()
    if unit in _INCH_UNITS:
        return float(value) / 2.54
    return float(value)


def _convert_wind(value: Any, unit: str) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    unit = (unit or "").lower()
    if unit == "mph":
        return float(value) / 1.609344
    if unit == "kt":
        return float(value) / 1.852
    if unit == "mps":
        return float(value) / 3.6
    return float(value)


def _convert_snow_level(value_m: Any, temperature_unit: str, precipitation_unit: str) -> int | None:
    if not isinstance(value_m, (int, float)) or value_m <= 0:
        return None
    temp_unit = (temperature_unit or "").lower()
    precip_unit = (precipitation_unit or "").lower()
    if temp_unit in _FAHRENHEIT_UNITS or precip_unit in _INCH_UNITS:
        value_ft = float(value_m) * 3.28084
        return int(round(value_ft / 500.0) * 500)
    return int(round(float(value_m) / 100.0) * 100)


def _format_unit_label(unit: str) -> str:
    normalized = (unit or "").strip().lower()
    if normalized in {"inch", "in"}:
        return "in"
    return normalized

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
    """
    Convert the structured dataset and alerts into a human-readable text block for the LLM.

    This function iterates through each day and hour, converting standard-unit data
    into the configured display units and formatting it for the LLM.

    Args:
        dataset: The processed forecast data.
        alerts: List of active alerts.
        tz_str: Timezone string for date formatting.
        temperature_unit: Unit for temperature display.
        precipitation_unit: Unit for precipitation display.
        snowfall_unit: Unit for snowfall display.
        windspeed_unit: Unit for wind speed display.

    Returns:
        A string containing the formatted dataset and alerts.
    """
    if not dataset:
        return "Error: No valid forecast data received for formatting."

    alert_text = _format_alerts(alerts, dataset, tz_str)
    output_parts: List[str] = []
    snow_level_unit = _snow_level_unit_label(temperature_unit, precipitation_unit)

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
        is_single_member = len(ensemble_keys) <= 1
        members_output: List[str] = []
        daily_lows: List[float] = []
        daily_highs: List[float] = []
        daily_precip: List[float] = []
        daily_snow: List[float] = []

        for member in ensemble_keys:
            # For deterministic (single-member) datasets, omit the "Scenario 00:" label.
            block_lines = [] if is_single_member else [f"Scenario {member.replace('member', '')}:"]
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
                temp = _convert_temperature(member_data.get("temperature"), temperature_unit)
                precip_val = _convert_precipitation(
                    member_data.get("precipitation", 0.0),
                    precipitation_unit,
                )
                snowfall_val = _convert_snowfall(
                    member_data.get("snowfall", 0.0),
                    snowfall_unit,
                )
                wind_speed = _convert_wind(member_data.get("wind_speed", 0.0), windspeed_unit)
                wind_gust = _convert_wind(member_data.get("wind_gust", 0.0), windspeed_unit)
                wind_direction = member_data.get("wind_direction", "variable")
                pop = member_data.get("pop")
                cloud_cover = member_data.get("cloud_cover")

                if isinstance(temp, (int, float)):
                    high_temp = max(high_temp, temp)
                    low_temp = min(low_temp, temp)

                if isinstance(precip_val, (int, float)):
                    total_precip += precip_val
                if isinstance(snowfall_val, (int, float)):
                    total_snow += snowfall_val

                hour_label = convert_hour_to_ampm(_hour_from_string(hour_entry.get("hour", "0:00")))
                weather_desc = str(member_data.get("weather", "Unknown")).capitalize()
                snow_level = _convert_snow_level(
                    member_data.get("snow_level"),
                    temperature_unit,
                    precipitation_unit,
                )

                precip_text = _format_hourly_precip_rate(
                    precipitation=precip_val,
                    snowfall=snowfall_val,
                    weather_desc=weather_desc,
                    unit=precipitation_unit,
                )

                snow_text = ""
                if isinstance(snow_level, int) and snow_level > 0:
                    snow_text = f"(snow down to about {snow_level} {snow_level_unit})"

                wind_speed_rounded = round_windspeed(wind_speed, windspeed_unit)
                wind_gust_rounded = round_windspeed(wind_gust, windspeed_unit) if wind_gust else 0
                wind_text = _format_wind(wind_direction, wind_speed_rounded, wind_gust_rounded)
                pop_text = f"pop{int(pop)}" if isinstance(pop, int) else ""
                cloud_text = ""
                if is_single_member and isinstance(cloud_cover, int) and 0 <= cloud_cover <= 100:
                    cloud_text = f"cc{cloud_cover}"

                temp_text = f"{_format_temp(temp)}" if isinstance(temp, (int, float)) else "N/A"
                details = [temp_text, weather_desc]
                if precip_text:
                    details.append(precip_text)
                if cloud_text:
                    details.append(cloud_text)
                if snow_text:
                    details.append(snow_text)
                if pop_text:
                    details.append(pop_text)
                details.append(wind_text)
                detail_str = " ".join(part.strip() for part in details if part)
                block_lines.append(f"{hour_label} {detail_str}")

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
                daily_precip.append(_normalize_daily_total(total_precip, precipitation_unit, kind="rainfall"))
                daily_snow.append(round(total_snow, 1))

        if members_output:
            scenarios_text = "\n\n".join(members_output)
            if is_single_member:
                # Deterministic-style output: the per-member summary already contains
                # the low/high and precip/snow totals. Avoid emitting probabilistic
                # range summaries which are meaningless with a single member.
                output_parts.append(f"{date_heading}\n{scenarios_text}\n")
            else:
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
                output_parts.append(f"{date_heading}\n{scenarios_text}\nRANGE SUMMARY:\n" + range_summary + "\n")

    final_text = "\n".join(part for part in output_parts if part.strip())
    return (alert_text + "\n" + final_text).strip() if alert_text else final_text.strip()


def format_area_dataset(area_name: str, locations: List[dict[str, Any]]) -> str:
    """
    Combine multiple location datasets into a single area-level text block.

    Each entry in `locations` must provide: name, latitude, longitude, timezone, text.
    This aggregates the individual location prompts into one large context for the area forecast.

    Args:
        area_name: Name of the area.
        locations: List of dictionaries representing representative locations.

    Returns:
        The combined text block.
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
    """Render alert metadata into human-readable text that precedes the dataset."""
    if not alerts:
        return ""
    first_date = dataset[0].get("date")
    try:
        earliest = datetime.strptime(first_date, "%Y-%m-%d").date() if first_date else None
    except (TypeError, ValueError):
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
    """Return the integer hour from strings like '06:00'."""
    try:
        return int(value.split(":")[0])
    except (IndexError, TypeError, ValueError):
        return 0


def _format_temp(value: float) -> str:
    """Format temperature for hourly lines without repeating units."""
    return f"{round(value)}°"


def _format_wind(direction: str, speed: float, gust: float) -> str:
    """Summarize wind direction and gusts without repeating units."""
    if not isinstance(speed, (int, float)) or speed <= 0:
        return "calm"
    base = f"{direction or 'VAR'} {int(speed)}"
    gust_part = ""
    if isinstance(gust, (int, float)) and gust - speed >= 5:
        gust_part = f" gust {int(gust)}"
    return f"{base}{gust_part}"


def _format_hourly_precip_rate(
    precipitation: Any,
    snowfall: Any,
    weather_desc: str,
    unit: str,
) -> str:
    """Return a formatted precipitation rate, labeling ambiguous phases."""
    if not isinstance(precipitation, (int, float)):
        return ""
    precision = 0 if unit == "mm" else 1
    value = round(float(precipitation), precision)
    if value == 0:
        return ""
    value_text = f"{value:.{precision}f}"
    phase = _precip_phase(snowfall, weather_desc)
    unit_label = _format_unit_label(unit)
    rate_text = f"{value_text} {unit_label}/h"
    if phase == "mixed":
        return f"(Precip {rate_text})"
    return rate_text


def _precip_phase(snowfall: Any, weather_desc: str) -> str:
    """Determine whether precip is rain, snow, or mixed for labeling."""
    snowfall_amt = float(snowfall) if isinstance(snowfall, (int, float)) else 0.0
    weather_lower = (weather_desc or "").lower()
    snow_keywords = ("snow", "sleet", "flurry", "wintry", "freezing", "ice pellet")
    rain_keywords = ("rain", "shower", "drizzle", "thunder", "storm")

    has_snow_signal = snowfall_amt > 0 or any(keyword in weather_lower for keyword in snow_keywords)
    has_rain_signal = any(keyword in weather_lower for keyword in rain_keywords)

    if has_snow_signal and has_rain_signal:
        return "mixed"
    if has_snow_signal:
        return "snow"
    if has_rain_signal:
        return "rain"
    return "rain" if snowfall_amt == 0 else "mixed"


def _member_summary(
    high_temp: float,
    low_temp: float,
    total_precip: float,
    total_snow: float,
    temperature_unit: str,
    precipitation_unit: str,
    snowfall_unit: str,
) -> str:
    """Produce a per-member summary of highs, lows, and precipitation totals."""
    if not (math.isfinite(high_temp) and math.isfinite(low_temp)):
        return " No valid temperature data found for summary.\n"
    lines = [
        f" Low {round(low_temp)}°{temperature_unit.capitalize()[0]}, High {round(high_temp)}°{temperature_unit.capitalize()[0]}",
    ]
    snow_line = _format_total_snowfall_line(total_snow, snowfall_unit)
    if snow_line:
        lines.append(snow_line)
    rainfall_line = _format_total_amount_line(total_precip, precipitation_unit, label="rainfall")
    if rainfall_line:
        lines.append(rainfall_line)
    return "\n".join(lines)


def _normalize_daily_total(value: float, unit: str, *, kind: str) -> float:
    """
    Normalize totals used for RANGE SUMMARY calculations.

    For mm rainfall, treat trace totals as 0 and round sub-1 mm totals to 0.5 mm
    to avoid misleading outputs like "0.0 mm" or "near 0 mm".
    """
    if not isinstance(value, (int, float)):
        return 0.0
    v = float(value)
    if v <= 0:
        return 0.0

    if kind == "rainfall" and unit == "mm":
        # Treat trace rainfall as 0.
        if v < 0.25:
            return 0.0
        if v < 1.0:
            return round(v * 2.0) / 2.0
        return float(int(round(v)))

    # Default: keep some precision for statistics.
    return round(v, 1)


def _format_total_amount_line(value: float, unit: str, *, label: str) -> str:
    """
    Format a "Total rainfall/snowfall" line, omitting meaningless near-zero totals.

    For mm rainfall:
    - < 0.25 mm: omit
    - 0.25 mm to < 1.0 mm: round to nearest 0.5 mm
    - >= 1.0 mm: round to nearest whole mm (and print as an integer, not 1.0)
    """
    if not isinstance(value, (int, float)):
        return ""
    v = float(value)
    if v <= 0:
        return ""

    unit_label = _format_unit_label(unit)
    if label == "rainfall" and unit == "mm":
        if v < 0.25:
            return ""
        if v < 1.0:
            rounded = round(v * 2.0) / 2.0
        else:
            rounded = float(int(round(v)))
        if rounded <= 0:
            return ""
        # Render 0.5 steps without trailing .0
        text = str(int(rounded)) if float(rounded).is_integer() else f"{rounded:.1f}".rstrip("0").rstrip(".")
        return f" Total rainfall: {text} {unit_label}."

    # Default formatting: keep existing behavior but avoid "0.0".
    precision = 0 if unit == "mm" else 1
    rounded = round(v, precision)
    if rounded == 0:
        return ""
    if precision == 0:
        return f" Total {label}: {int(rounded)} {unit_label}."
    return f" Total {label}: {rounded:.{precision}f} {unit_label}."


def _format_total_snowfall_line(value: float, unit: str) -> str:
    """Format a "Total snowfall" line with sensible rounding for cm."""
    if not isinstance(value, (int, float)):
        return ""
    v = float(value)
    if v <= 0:
        return ""

    unit_label = _format_unit_label(unit)
    if unit_label == "cm":
        if v < 1.0:
            return " Total snowfall: less than 1 cm."
        rounded = int(round(v))
        if rounded <= 0:
            return ""
        return f" Total snowfall: {rounded} cm."

    rounded = round(v, 1)
    if rounded == 0:
        return ""
    text = str(int(rounded)) if float(rounded).is_integer() else f"{rounded:.1f}".rstrip("0").rstrip(".")
    return f" Total snowfall: {text} {unit_label}."


def _should_use_only_low(hours: List[dict]) -> bool:
    """Determine if the range summary should report only low temperatures."""
    if not hours:
        return False
    hour_int = _hour_from_string(hours[0].get("hour", "0:00"))
    return hour_int > 15


def _should_reverse_high_low(hours: List[dict]) -> bool:
    """Return True if the schedule warrants reporting highs before lows."""
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
    """
    Generate a summary string describing the range of conditions for a day.

    Args:
        daily_lows: Collection of low temperatures from members.
        daily_highs: Collection of high temperatures from members.
        daily_precip: Collection of precipitation totals.
        daily_snow: Collection of snowfall totals.
        temp_unit_short: Short unit string for temp (e.g., "C").
        precip_unit: Unit string for precip.
        snow_unit: Unit string for snow.
        use_only_low: If true, only report low temperatures (e.g., for evening forecasts).
        reverse_high_and_low: If true, report high then low (e.g., for afternoon forecasts).

    Returns:
        A formatted summary string.
    """
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
    heavy_threshold_mm = (
        PRECIP_HEAVY_THRESHOLD_MM if precip_unit == "mm" else PRECIP_HEAVY_THRESHOLD_IN * 25.4
    )
    heavy_precip_line = precipitation_exceedance_probability(
        daily_precip,
        precip_unit,
        heavy_threshold_mm,
    )
    if heavy_precip_line:
        summary_lines.append(heavy_precip_line)
    return "\n".join(summary_lines)


def precipitation_or_snowfall_likely(label: str, values: List[float], unit: str) -> str:
    """Describe the probability and likely range for precipitation or snowfall."""
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
    unit_label = _format_unit_label(unit)

    if label == "snowfall" and unit_label == "cm":
        lower_raw, upper_raw = percentiles
        if upper_raw < 1.0:
            return (
                f"Estimated probability of {label}: {probability}%\n"
                f"Likely {label} less than 1 {unit_label}"
            )
        lower = int(round(lower_raw))
        upper = int(round(upper_raw))
        if lower <= 0:
            return (
                f"Estimated probability of {label}: {probability}%\n"
                f"Likely {label} up to {upper} {unit_label}"
            )
        if lower == upper:
            return (
                f"Estimated probability of {label}: {probability}%\n"
                f"Likely {label} around {lower} {unit_label}"
            )
        return (
            f"Estimated probability of {label}: {probability}%\n"
            f"Likely {label} {lower} {unit_label} to {upper} {unit_label}"
        )

    precision = 0 if unit == "mm" else 1
    lower = round(percentiles[0], precision)
    upper = round(percentiles[1], precision)

    def _fmt(value: float) -> str:
        if precision == 0:
            return f"{int(value)}"
        return f"{value:.1f}"

    lower_text = _fmt(lower)
    upper_text = _fmt(upper)
    if lower == upper:
        return f"Estimated probability of {label}: {probability}%\nLikely {label} around {lower_text} {unit_label}"
    return f"Estimated probability of {label}: {probability}%\nLikely {label} {lower_text} {unit_label} to {upper_text} {unit_label}"


def precipitation_exceedance_probability(
    values: List[float],
    unit: str,
    threshold_mm: float,
) -> str:
    """
    Provide the probability of exceeding a precipitation threshold.

    The threshold is defined in millimeters and converted into the working unit before
    counting how many ensemble members exceed it.
    """
    if threshold_mm <= 0:
        return ""

    numeric = [v for v in values if isinstance(v, (int, float))]
    if not numeric:
        return ""

    threshold_value = threshold_mm if unit == "mm" else threshold_mm / 25.4
    exceedances = [v for v in numeric if v >= threshold_value]
    if not exceedances:
        return ""

    probability = _jeffreys_probability(len(exceedances), len(numeric))

    threshold_label = _format_threshold_label(unit, threshold_mm, threshold_value)
    return f"Estimated probability of precipitation >= {threshold_label}: {probability}%"


def _format_threshold_label(unit: str, threshold_mm: float, threshold_value: float) -> str:
    """Return a display string for the heavy precipitation threshold."""
    if unit == "mm":
        rounded = int(threshold_mm) if float(threshold_mm).is_integer() else round(threshold_mm, 1)
        return f"{rounded} mm"

    converted = round(threshold_value, 1 if threshold_value < 10 else 0)
    rounded_mm = int(threshold_mm) if float(threshold_mm).is_integer() else round(threshold_mm, 1)
    unit_label = "in" if unit == "inch" else unit
    return f"{rounded_mm} mm ({converted} {unit_label})"


def estimate_percentiles(values: Iterable[float], lower_fraction: float) -> tuple[float, float]:
    """
    Estimate the lower and upper bounds of a distribution using percentiles.

    Args:
        values: A list of numerical values.
        lower_fraction: The percentile fraction for the lower bound (e.g., 0.20 for 20th percentile).
                        The upper bound will be 1 - lower_fraction (e.g., 80th percentile).

    Returns:
        A tuple containing (lower_bound, upper_bound).
    """
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
    """Return a rounded probability percentage using the Jeffreys prior."""
    if total <= 0:
        return 0
    prob = (occurrences + 0.5) / (total + 1)
    return max(0, min(100, int(round(prob * 20) * 5)))


def convert_date_string(date_str: str) -> str:
    """Convert YYYY-MM-DD plus descriptor into uppercase friendly text."""
    try:
        cleaned = date_str.strip()
        date_part, _, descriptor = cleaned.partition(" ")
        parsed = datetime.strptime(date_part, "%Y-%m-%d")
        day = parsed.strftime("%d").lstrip("0")
        month = parsed.strftime("%B").upper()
        descriptor = descriptor.strip()
        return f"{descriptor} {day} {month}" if descriptor else f"{parsed.strftime('%A').upper()} {day} {month}"
    except (AttributeError, TypeError, ValueError):
        return date_str


def determine_current_season(latitude: float) -> str:
    """Roughly infer the current season based on month and hemisphere."""
    month = datetime.now().month
    if month in (3, 4, 5):
        return "Spring" if latitude >= 0 else "Autumn"
    if month in (6, 7, 8):
        return "Summer" if latitude >= 0 else "Winter"
    if month in (9, 10, 11):
        return "Autumn" if latitude >= 0 else "Spring"
    return "Winter" if latitude >= 0 else "Summer"
