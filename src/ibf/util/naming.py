"""
Helpers for producing stable, human-friendly display names.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Sequence, List


def generate_unique_location_names(names: Sequence[str], kinds: Sequence[str]) -> List[str]:
    """
    Generate unique display names for locations to avoid conflicts when multiple
    forecasts exist for the same location name (e.g., deterministic vs ensemble).

    Returns a list of unique display names in the same order as the inputs.
    For duplicates, appends suffixes like " (Deterministic)", " (Ensemble)", or " 1", " 2".
    """
    if len(names) != len(kinds):
        raise ValueError("names and kinds must have the same length.")

    name_counts: dict[str, int] = {}
    name_kinds_all: dict[str, set[str]] = defaultdict(set)

    for name, kind in zip(names, kinds):
        name_counts[name] = name_counts.get(name, 0) + 1
        name_kinds_all[name].add(kind)

    name_should_use_kinds = {
        name: (count == 2 and len(name_kinds_all[name]) == 2)
        for name, count in name_counts.items()
    }

    result: List[str] = []
    name_occurrences: dict[str, int] = {}

    for name, kind in zip(names, kinds):
        if name_counts[name] == 1:
            result.append(name)
            continue

        occurrence = name_occurrences.get(name, 0) + 1
        name_occurrences[name] = occurrence

        if name_should_use_kinds[name]:
            if kind == "deterministic":
                result.append(f"{name} (Deterministic)")
            else:
                result.append(f"{name} (Ensemble)")
        else:
            result.append(f"{name} {occurrence}")

    return result
