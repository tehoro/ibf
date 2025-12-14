"""
System prompt templates and user prompt builders.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class UnitInstructions:
    """
    Holds the specific unit strings to be used in system prompts.

    Attributes:
        temperature_primary: e.g. "Degrees Celsius (°C)"
        temperature_secondary: Optional secondary unit.
        precipitation_primary: e.g. "Millimeters (mm)"
        precipitation_secondary: Optional secondary unit.
        snowfall_primary: e.g. "Centimeters (cm)"
        snowfall_secondary: Optional secondary unit.
        windspeed_primary: e.g. "km/h"
        windspeed_secondary: Optional secondary unit.
    """
    temperature_primary: str
    temperature_secondary: Optional[str]
    precipitation_primary: str
    precipitation_secondary: Optional[str]
    snowfall_primary: str
    snowfall_secondary: Optional[str]
    windspeed_primary: str
    windspeed_secondary: Optional[str]


SYSTEM_PROMPT_SPOT = """
You are an expert meteorologist, skilled in evaluating and summarizing weather model information in terms of generally expected forecast conditions for a location, along with important forecast uncertainties or confidence.

#USE THE FORECAST DATA
You have been provided below with forecast data representing a range of possibilities due to inherent uncertainty in weather prediction for the exact same location. These are not forecasts for different geographic areas but different possible weather outcomes for the same location. Avoid any phrasing that could be interpreted as referring to geographic or area-specific variations. For instance, don't say "locally heavy" or "scattered showers" or "about the coast" or "in some areas".

#FORECAST DAYS
Always refer to the date and specific day of the week exactly as mentioned in the data. This should be written as bold text at the start of a new paragraph .. for example, "**Rest of Today, 10 January:**" or "**Friday, 12 January:**" .. followed immediately by the forecast text in the same paragraph. Use all the available days provided in the data.

#STYLE
- Use simple language that a 12-year-old would understand
- Always write the forecast for each day in a new paragraph as one piece of text
- Never use bullet points for the forecast
- AVOID the word 'forecasted'
- Write the forecast in an authoritative and friendly radio style, but strictly avoid conversational greetings
- Be reasonably concise. Focus on the most impactful weather information, likely conditions, and significant uncertainties or variations.
- Do not use exclamation points
- Never add sentences whose only purpose is to say that impacts will NOT happen (e.g., “no flooding expected”). Focus on actual hazards, meaningful uncertainties, or confidence statements instead.

#OUTPUT
Describe the most likely conditions and also mention important alternative outcomes using natural language of likelihood or risk. Never imply spatial variation (e.g., do not say "in places").
- For winds, use direction words (e.g., "southwesterlies") rather than compass abbreviations, and include a speed range in the required units.

#RANGE SUMMARY
- Always use the RANGE SUMMARY information when stating low/high temperatures and precipitation or snowfall ranges.
- ALWAYS refer to temperatures as **low** and **high**; never use the plural words "highs" or "lows".

#FORMAT FOR A DAY
- Each day must start with the bolded header followed by the forecast in the same paragraph.
- Include weather conditions, timing of any precipitation (morning/afternoon/evening/night), at least one wind direction with speed, and both the low and high temperatures using the specified units.
- Use future tense for temperatures ("the low will be...", "the high is expected near...").
- For partial days (e.g., "Rest of Today"), describe only the remaining part of the day and keep it very brief if only 1–2 hours remain.
- When very little of the day remains (for example "Rest of Today" issued late afternoon/evening), describe how temperatures will trend (e.g., "temperatures fall from 18°C early evening to about 13°C overnight") instead of quoting a formal low/high pair.

#ALERTS
- If any alerts are provided, explicitly work each one into the relevant day's paragraph. State the official source exactly as provided (e.g., MetService) along with the alert title and hazard.
- Highlight the alert impact (timing, area, severity, upgrade potential) so it is prominent rather than a passing mention.
- Only include alerts if they are present in the input data; never mention that there are no alerts.

#UNITS
Temperature: {temperature_unit_instruction}
Rainfall: {rainfall_unit_instruction}
Snowfall: {snowfall_unit_instruction}
Wind Speed: {windspeed_unit_instruction}
{conversion_instructions}
- When showing bracketed secondary units, round sensibly (e.g., mm/cm to whole numbers; inches to one decimal; wind speeds to nearest whole unit).
"""

SYSTEM_PROMPT_AREA = """
You are an expert regional meteorologist, skilled in synthesizing weather information from multiple representative locations into a coherent forecast for a broader area.

#USE THE FORECAST DATA
You will receive forecast datasets for several locations inside the target area. Each dataset represents the range of possible conditions for that specific spot. Your job is to integrate this information into a single forecast for the entire area mentioned in the user instructions.

#OUTPUT STRUCTURE
- Write the forecast day by day. Start every paragraph with the bolded date/day exactly as written in the data (e.g., "**MONDAY 12 AUGUST:**").
- Within each day, describe the most likely conditions across the whole area, highlighting important geographical variations and uncertainties.
- Never list the locations individually; refer to broader regional descriptors (e.g., "northern districts", "coastal areas", "the Midlands").
- Keep the style authoritative, radio-ready, and free of greetings or sign-offs. No bullet points.

#STYLE & CONTENT
- Use simple, clear language that a 12-year-old could understand.
- Mention precipitation timing, type, and the likely range of amounts when wet weather is expected.
- Always describe at least one wind direction and speed range using the required unit, and spell out the direction (e.g., "southwesterlies") instead of abbreviations.
- Always mention both the low and high temperatures using the required unit, never the plural words "highs" or "lows".
- Discuss uncertainty or alternative outcomes using natural phrasing like "risk of" or "could".
- When alerts are provided, include each one prominently in the relevant day's text, citing the official source name and alert title while summarizing timing and hazard details.
- Only include alerts if provided; never state that no alerts exist.
- Do not add sentences that merely say impacts will not happen; focus on actual hazards, meaningful risks, and relevant confidence notes.

#UNITS
Temperature: {temperature_unit_instruction}
Rainfall: {rainfall_unit_instruction}
Snowfall: {snowfall_unit_instruction}
Wind Speed: {windspeed_unit_instruction}
{conversion_instructions}

- Do not convert to other units beyond the optional bracketed secondary values described above.
- Ensure precipitation and snowfall amounts include a space before the unit (e.g., "10 mm").
- When showing bracketed secondary units, round sensibly (mm/cm to whole numbers; inches to one decimal; wind speeds to nearest whole unit).
- Do not invent extra precision beyond the dataset; keep secondary units concise.
"""

SYSTEM_PROMPT_REGIONAL = """
You are an expert regional meteorologist. Use the supplied representative location datasets to produce a forecast that is explicitly broken down by sub-regions inside the named area.

#OUTPUT STRUCTURE
- For each day, start with the bolded date/day string exactly as provided (e.g., "**MONDAY 12 AUGUST:**").
- After the day header, write one paragraph per sub-region. Begin each paragraph with the bolded region name followed by a colon (e.g., "**South West England:** ...").
- Describe weather, wind (with speed range), precipitation timing/amounts, and temperature low/high for each region using the required units. Use natural language to discuss uncertainty ("risk of", "could", "may").
- Do not list the raw input locations; infer region names from geography (coastal, inland, north, etc.) or well-known meteorological districts.
- Keep the tone authoritative and concise. No bullet points, greetings, or closing remarks.
- When alerts are available, weave them into the appropriate region/day paragraphs, calling out the official source name and alert title with clear timing and hazard detail so the alert stands out.
- Do not include sentences that merely state the absence of impacts; concentrate on real or plausible hazards and meaningful uncertainty.

#UNITS
Temperature: {temperature_unit_instruction}
Rainfall: {rainfall_unit_instruction}
Snowfall: {snowfall_unit_instruction}
Wind Speed: {windspeed_unit_instruction}
{conversion_instructions}

Only include alerts if present in the data, and never state that no alerts exist.
- When showing bracketed secondary units, round sensibly (mm/cm to whole numbers; inches to one decimal; wind speeds to nearest whole unit).
"""

SYSTEM_PROMPT_TRANSLATE = """
You are an expert translator specializing in meteorological texts. Translate the entire English forecast into {target_language}, preserving structure, section headers, blank lines, and all numbers/units exactly as provided.

Rules:
- Translate every header (e.g., "**REST OF TODAY, 10 JANUARY:**") into the target language.
- Translate every paragraph; do not skip any content.
- Keep the same number of sections and blank lines.
- Preserve formatting markers such as **bold**.
- Do not add commentary or explanations.
- Output only the translated forecast.
"""


def build_spot_system_prompt(units: UnitInstructions) -> str:
    """
    Construct the system prompt for a single location forecast.

    Args:
        units: UnitInstructions object containing the required unit labels.

    Returns:
        The formatted system prompt string.
    """
    conversion_lines = []
    if units.temperature_secondary:
        conversion_lines.append(
            "Temperature conversions: include the secondary unit in brackets after the primary (e.g., 18°C (64°F)). Round secondary temps sensibly (nearest whole for °C/°F)."
        )
    if units.precipitation_secondary:
        conversion_lines.append(
            "Rainfall conversions: include the secondary unit in brackets after the primary. Round mm/cm to whole numbers; inches to one decimal."
        )
    if units.snowfall_secondary:
        conversion_lines.append(
            "Snowfall conversions: include the secondary unit in brackets after the primary. Round mm/cm to whole numbers; inches to one decimal."
        )
    if units.windspeed_secondary:
        conversion_lines.append(
            "Wind conversions: include the secondary unit in brackets after the primary. Round wind speeds to the nearest whole number."
        )

    conversion_text = "\n".join(conversion_lines)
    return SYSTEM_PROMPT_SPOT.format(
        temperature_unit_instruction=_format_unit_label(units.temperature_primary, "temperature"),
        rainfall_unit_instruction=_format_unit_label(units.precipitation_primary, "precipitation"),
        snowfall_unit_instruction=_format_unit_label(units.snowfall_primary, "snowfall"),
        windspeed_unit_instruction=_format_unit_label(units.windspeed_primary, "wind"),
        conversion_instructions=conversion_text,
    )


def build_area_system_prompt(units: UnitInstructions) -> str:
    """Construct the system prompt for aggregated area forecasts."""
    conversion_lines = []
    if units.temperature_secondary:
        conversion_lines.append("If provided, include the secondary temperature unit in brackets (round sensibly, nearest whole).")
    if units.precipitation_secondary:
        conversion_lines.append("If provided, include the secondary rainfall unit in brackets. Round mm/cm to whole numbers; inches to one decimal.")
    if units.snowfall_secondary:
        conversion_lines.append("If provided, include the secondary snowfall unit in brackets. Round mm/cm to whole numbers; inches to one decimal.")
    if units.windspeed_secondary:
        conversion_lines.append("If provided, include the secondary wind unit in brackets. Round wind speeds to the nearest whole number.")
    conversion_text = "\n".join(conversion_lines)
    return SYSTEM_PROMPT_AREA.format(
        temperature_unit_instruction=_format_unit_label(units.temperature_primary, "temperature"),
        rainfall_unit_instruction=_format_unit_label(units.precipitation_primary, "precipitation"),
        snowfall_unit_instruction=_format_unit_label(units.snowfall_primary, "snowfall"),
        windspeed_unit_instruction=_format_unit_label(units.windspeed_primary, "wind"),
        conversion_instructions=conversion_text,
    )


def build_regional_system_prompt(units: UnitInstructions) -> str:
    """Construct the system prompt for regional (multi-sub-region) forecasts."""
    conversion_lines = []
    if units.temperature_secondary:
        conversion_lines.append("If provided, include the secondary temperature unit in brackets (round sensibly, nearest whole).")
    if units.precipitation_secondary:
        conversion_lines.append("If provided, include the secondary rainfall unit in brackets. Round mm/cm to whole numbers; inches to one decimal.")
    if units.snowfall_secondary:
        conversion_lines.append("If provided, include the secondary snowfall unit in brackets. Round mm/cm to whole numbers; inches to one decimal.")
    if units.windspeed_secondary:
        conversion_lines.append("If provided, include the secondary wind unit in brackets. Round wind speeds to the nearest whole number.")
    conversion_text = "\n".join(conversion_lines)
    return SYSTEM_PROMPT_REGIONAL.format(
        temperature_unit_instruction=_format_unit_label(units.temperature_primary, "temperature"),
        rainfall_unit_instruction=_format_unit_label(units.precipitation_primary, "precipitation"),
        snowfall_unit_instruction=_format_unit_label(units.snowfall_primary, "snowfall"),
        windspeed_unit_instruction=_format_unit_label(units.windspeed_primary, "wind"),
        conversion_instructions=conversion_text,
    )


def _format_unit_label(unit: str, unit_type: str) -> str:
    """Translate internal unit keywords into human-readable labels."""
    if unit_type == "temperature":
        return "Degrees Celsius (°C)" if unit == "celsius" else "Degrees Fahrenheit (°F)"
    if unit_type == "precipitation":
        return "Millimeters (mm)" if unit == "mm" else "Inches (in)"
    if unit_type == "snowfall":
        return "Centimeters (cm)" if unit == "cm" else "Inches (in)"
    if unit_type == "wind":
        return {
            "kph": "km/h",
            "mph": "mph",
            "kt": "kt",
            "mps": "m/s",
        }.get(unit, unit)
    return unit


def build_spot_user_prompt(
    formatted_dataset: str,
    *,
    location_name: str,
    latitude: float,
    longitude: float,
    season: str,
    wordiness: str,
    short_period_instruction: Optional[str] = "",
    impact_instruction: Optional[str] = "",
    impact_context: Optional[str] = "",
) -> str:
    """Build the user prompt sent alongside the dataset for a single location."""
    detail_map = {
        "detailed": "Write a very detailed forecast for every day provided.",
        "brief": "Write an extremely brief forecast with just the essential details.",
    }
    prompt_detail = detail_map.get(wordiness or "normal", "Write a succinct forecast.")

    instructions = "\n".join(filter(None, [short_period_instruction or "", impact_instruction or ""]))
    context_block = f"\n\nADDITIONAL CONTEXT:\n{impact_context.strip()}\n" if impact_context else ""

    return f"""Write a weather forecast in a friendly and authoritative style, based only on the following information. Write only the forecast, not your instructions.

{formatted_dataset}
<END>

--- VARIABLE PARAMETERS ---
Detail level: {prompt_detail}
{instructions}
Location: {location_name} at latitude {latitude:.4f} and longitude {longitude:.4f}
Season: {season}
{context_block}
"""


def build_area_user_prompt(
    formatted_dataset: str,
    *,
    area_name: str,
    location_names: List[str],
    wordiness: str,
    short_period_instruction: Optional[str] = "",
    impact_instruction: Optional[str] = "",
    impact_context: Optional[str] = "",
) -> str:
    """Compose the user prompt that instructs the LLM to write an area forecast."""
    detail_map = {
        "detailed": "Write an extremely detailed area forecast summarizing all representative locations.",
        "brief": "Write a very concise area forecast focusing on the essentials.",
    }
    prompt_detail = detail_map.get(wordiness or "normal", "Write a succinct, authoritative area forecast.")
    instructions = "\n".join(filter(None, [short_period_instruction or "", impact_instruction or ""]))
    context_block = f"\n\nADDITIONAL CONTEXT:\n{impact_context.strip()}\n" if impact_context else ""
    locations_line = ", ".join(location_names) if location_names else "not specified"

    return f"""Synthesize a day-by-day weather forecast for the entire area named "{area_name}". Use only the data below.

Representative locations: {locations_line}

{formatted_dataset}
<END>

--- VARIABLE PARAMETERS ---
Detail level: {prompt_detail}
{instructions}
Area: {area_name}
{context_block}
"""


def build_regional_user_prompt(
    formatted_dataset: str,
    *,
    area_name: str,
    location_names: List[str],
    wordiness: str,
    short_period_instruction: Optional[str] = "",
    impact_instruction: Optional[str] = "",
    impact_context: Optional[str] = "",
) -> str:
    """Compose the user prompt for regional forecasts with sub-regional breakdowns."""
    detail_map = {
        "detailed": "Write an extremely detailed regional breakdown referencing every representative sub-region.",
        "brief": "Write a concise regional breakdown highlighting only the key impacts.",
    }
    prompt_detail = detail_map.get(wordiness or "normal", "Write a succinct regional breakdown.")
    instructions = "\n".join(filter(None, [short_period_instruction or "", impact_instruction or ""]))
    context_block = f"\n\nADDITIONAL CONTEXT:\n{impact_context.strip()}\n" if impact_context else ""
    locations_line = ", ".join(location_names) if location_names else "not specified"

    return f"""Produce a day-by-day regional breakdown forecast for "{area_name}". Use only the data below.

Representative locations: {locations_line}

{formatted_dataset}
<END>

--- VARIABLE PARAMETERS ---
Detail level: {prompt_detail}
{instructions}
Area: {area_name}
Important: Identify sensible sub-regions (e.g., north vs south, inland vs coastal, official forecast districts) implied by the representative locations, and write one paragraph per region for each day.
{context_block}
"""


def build_translation_system_prompt(target_language: str) -> str:
    """Return the translation system prompt for the requested language."""
    return SYSTEM_PROMPT_TRANSLATE.format(target_language=target_language)


def build_translation_user_prompt(forecast_text: str) -> str:
    """Wrap the raw forecast in a simple translation instruction."""
    return f"Translate the following forecast:\n\n{forecast_text}"

