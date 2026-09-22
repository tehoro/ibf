"""Area prose cues from unrounded processed wind speeds (internally km/h).

Beaufort mean-speed lower boundaries in m/s from the NWS reference table:
https://www.weather.gov/media/marine/SeaState.pdf
Gust descriptions use the same speed bands but are explicitly labelled gusts.
"""

from bisect import bisect_right
import math


_BOUNDARIES_MPS = (0.3, 1.6, 3.4, 5.5, 8.0, 10.8, 13.9, 17.2, 20.8, 24.5, 28.5, 32.7)
_BOUNDARIES_KPH = tuple(boundary * 3.6 for boundary in _BOUNDARIES_MPS)
_DESCRIPTIONS = (
    "calm", "light air", "light breeze", "gentle breeze", "moderate breeze",
    "fresh breeze", "strong breeze", "near gale", "gale force", "strong gale",
    "storm force", "violent storm force", "hurricane force",
)


def beaufort_category(speed_kph: object) -> int | None:
    """Classify valid unrounded speeds, without treating absent data as calm."""
    if isinstance(speed_kph, bool) or not isinstance(speed_kph, (int, float)):
        return None
    if not math.isfinite(speed_kph) or speed_kph < 0:
        return None
    return bisect_right(_BOUNDARIES_KPH, speed_kph)


def format_wind_description_cues(dataset: list[dict]) -> str:
    """Preserve date/hour/member identity; never mix peak gusts with mean winds.

    A notable gust reaches near gale and is at least two categories above the
    concurrent mean. This is an IBF editorial rule, not an official warning rule.
    Adjacent hours with identical cues are compressed without averaging them.
    """
    lines = []
    for day in dataset:
        groups: list[tuple[list[str], str]] = []
        for hour in day.get("hours", []):
            by_description: dict[str, list[str]] = {}
            for member, values in hour.get("ensemble_members", {}).items():
                mean = beaufort_category(values.get("wind_speed"))
                gust = beaufort_category(values.get("wind_gust"))
                if mean is None:
                    continue
                description = f"mean: {_DESCRIPTIONS[mean]}"
                if gust is not None and gust >= 10:
                    description += f"; exceptional gust: {_DESCRIPTIONS[gust]}"
                elif gust is not None and gust >= 7 and gust - mean >= 2:
                    description += f"; notable gust: {_DESCRIPTIONS[gust]}"
                by_description.setdefault(description, []).append(member)
            cue = " | ".join(
                f"{','.join(members)}: {description}"
                for description, members in by_description.items()
            )
            if not cue:
                continue
            hour_label = str(hour.get("hour", "unknown"))
            if groups and groups[-1][1] == cue:
                groups[-1][0].append(hour_label)
            else:
                groups.append(([hour_label], cue))
        for hours, cue in groups:
            lines.append(f"Date {day.get('date', 'unknown')}; local hours {','.join(hours)}: {cue}")
    if not lines:
        return ""
    return (
        "BEAUFORT WORDING CUES (same hours and members as the numerical data; "
        "memberNN corresponds to Scenario NN, or the sole dataset when no Scenario label is shown):\n"
        + "\n".join(lines)
    )
