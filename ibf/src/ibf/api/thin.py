"""
Utilities to select representative ensemble members (ported from thinEnsembles.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List
import math


@dataclass
class MemberSeries:
    """
    Time series data for a single ensemble member.

    Attributes:
        temperature: List of temperature values.
        precipitation: List of precipitation values.
    """
    temperature: List[float]
    precipitation: List[float]


def select_members(ensemble_days: List[dict], *, thin_select: int = 16, weight_temp: float = 1.0, weight_precip: float = 1.0) -> List[dict]:
    """
    Reduce the ensemble set by picking members with the largest average distance.

    This algorithm greedily selects members that are most different from the already
    selected set, ensuring a diverse representation of possible outcomes.

    Args:
        ensemble_days: List of day dictionaries (as produced by legacy code).
        thin_select: Target number of members to retain (including control).
        weight_temp: Weight applied to temperature differences (default 1.0).
        weight_precip: Weight applied to precipitation differences (default 1.0).

    Returns:
        A new list of day dictionaries with only the selected members retained.
    """
    member_series = _flatten_members(ensemble_days)
    if not member_series:
        return ensemble_days

    selected = _run_selection(member_series, thin_select, weight_temp, weight_precip)

    pruned_days: List[dict] = []
    for day in ensemble_days:
        filtered_hours = []
        for hour in day.get("hours", []):
            members = hour.get("ensemble_members", {})
            filtered_members = {key: members[key] for key in selected if key in members}
            filtered_hours.append({"hour": hour.get("hour"), "ensemble_members": filtered_members})
        pruned_day = dict(day)
        pruned_day["hours"] = filtered_hours
        pruned_days.append(pruned_day)
    return pruned_days


def _flatten_members(ensemble_days: List[dict]) -> Dict[str, MemberSeries]:
    """Collapse the day/hour structure into per-member series for comparison."""
    members: Dict[str, MemberSeries] = {}
    for day in ensemble_days:
        for hour in day.get("hours", []):
            for member, payload in hour.get("ensemble_members", {}).items():
                series = members.setdefault(member, MemberSeries(temperature=[], precipitation=[]))
                series.temperature.append(float(payload.get("temperature", 0.0)))
                series.precipitation.append(float(payload.get("precipitation", 0.0)))
    return members


def _run_selection(members: Dict[str, MemberSeries], thin_select: int, weight_temp: float, weight_precip: float) -> List[str]:
    """Pick the most diverse ensemble members using RMS distance heuristics."""
    if len(members) <= thin_select:
        return list(members.keys())

    all_temps = [value for series in members.values() for value in series.temperature]
    all_precip = [value for series in members.values() for value in series.precipitation]
    min_temp, max_temp = min(all_temps), max(all_temps)
    min_precip, max_precip = min(all_precip), max(all_precip)

    def normalize(series: List[float], min_value: float, max_value: float) -> List[float]:
        if max_value == min_value:
            return [0.0] * len(series)
        return [(value - min_value) / (max_value - min_value) for value in series]

    normalized = {
        member: MemberSeries(
            temperature=normalize(series.temperature, min_temp, max_temp),
            precipitation=normalize(series.precipitation, min_precip, max_precip),
        )
        for member, series in members.items()
    }

    def rms(a: List[float], b: List[float]) -> float:
        if not a or not b:
            return 0.0
        diffs = [(x - y) ** 2 for x, y in zip(a, b)]
        if not diffs:
            return 0.0
        return math.sqrt(sum(diffs) / len(diffs))

    selected = ["member00"] if "member00" in members else [sorted(members.keys())[0]]
    remaining = set(members.keys()) - set(selected)

    while len(selected) < thin_select and remaining:
        best_member = None
        best_distance = float("-inf")

        for candidate in remaining:
            distances = []
            for existing in selected:
                temp_dist = rms(normalized[candidate].temperature, normalized[existing].temperature)
                precip_dist = rms(normalized[candidate].precipitation, normalized[existing].precipitation)
                distances.append(weight_temp * temp_dist + weight_precip * precip_dist)
            avg_distance = sum(distances) / len(distances) if distances else 0.0
            if avg_distance > best_distance:
                best_distance = avg_distance
                best_member = candidate

        if best_member is None:
            break
        selected.append(best_member)
        remaining.remove(best_member)

    return selected

