"""Build and enforce concise factual contracts for spot forecast text."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Iterable, Optional


_DATE_LINE_RE = re.compile(r"(?m)^Date:\s*(?P<label>[^\n]+?)\s*$")
_OUTPUT_PERIOD_RE = re.compile(
    r"(?ms)^\s*\*\*(?P<header>[^*\n]+?):\*\*\s*(?P<body>.*?)(?=^\s*\*\*[^*\n]+?:\*\*|\Z)"
)
_DATE_KEY_RE = re.compile(
    r"\b(?P<day>\d{1,2})\s+"
    r"(?P<month>january|february|march|april|may|june|july|august|september|october|november|december)\b",
    re.IGNORECASE,
)
_WEEKDAY_RE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
_TEMPERATURE_RE = re.compile(r"-?\d+(?:\.\d+)?\s*°[CF]\b", re.IGNORECASE)
_PRECIP_MEASUREMENT_RE = re.compile(
    r"(?<![\w.])-?\d+(?:\.\d+)?\s*(?:mm|cm|inches?|in)\b",
    re.IGNORECASE,
)
_NON_PRECIP_FACT_RE = re.compile(
    r"(?<![\w.])-?\d+(?:\.\d+)?\s*(?:km/h|mph|kt|m/s|metres?|meters?|feet|ft|%)\b"
    r"|(?<![\w.])-?\d+(?:\.\d+)?(?=\s+(?:to|and|[-–])\s+-?\d+(?:\.\d+)?\s*"
    r"(?:km/h|mph|kt|m/s|metres?|meters?|feet|ft)\b)"
    r"|\b\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?\b",
    re.IGNORECASE,
)
_RAIN_WORD_RE = re.compile(r"\b(?:rain|showers?|drizzle|precipitation)\b", re.IGNORECASE)
_SNOW_WORD_RE = re.compile(
    r"\b(?:snow|snowfall|sleet|wintry|flurr(?:y|ies))\b",
    re.IGNORECASE,
)
_COMPACT_DEICTIC_RE = re.compile(r"\bthis\s+(morning|afternoon|evening)\b", re.IGNORECASE)
_COMPACT_WILL_BE_PRESENT_RE = re.compile(r"\s+will\s+be\s+present\b", re.IGNORECASE)
_COMPACT_TEMPERATURE_VALUE = (
    r"-?\d+(?:\.\d+)?\s*°[CF](?:\s*\(-?\d+(?:\.\d+)?\s*°[CF]\))?"
)
_COMPACT_TEMPERATURE_PAIR_RE = re.compile(
    rf"\bA\s+(?P<first_kind>low|high)\s+of\s+"
    rf"(?P<first_value>{_COMPACT_TEMPERATURE_VALUE})\s*"
    rf"(?:,\s*(?:and\s+)?|\s+and\s+)a\s+"
    rf"(?P<second_kind>low|high)\s+of\s+"
    rf"(?P<second_value>{_COMPACT_TEMPERATURE_VALUE})",
    re.IGNORECASE,
)
_COMPACT_GUST_CLAUSE_RE = re.compile(
    r"(?:,\s*(?:(?:but|though)\s+)?(?:with\s+)?|\s+(?:and|with)\s+)"
    r"(?:[a-z-]+\s+){0,2}"
    r"(?:a\s+maximum\s+)?gust(?:s|ing)?\s+"
    r"(?:reaching(?:\s+up\s+to)?|up\s+to|to|of)\s+"
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?:km/h|mph|kt|m/s)\b(?:\s*\([^)]*\))?[^,.;!?]*",
    re.IGNORECASE,
)
_COMPACT_SKY_STATE = (
    r"(?:clear skies|(?:mainly|mostly) clear(?: skies)?"
    r"(?:\s+to\s+(?:clear skies|partly cloudy))?|partly cloudy|cloudy|overcast)"
)
_COMPACT_WIND_STATE = (
    r"(?:light winds|"
    r"(?:north|south|east|west|northeast|northwest|southeast|southwest)"
    r"(?:erly winds|erlies))"
)
_COMPACT_STEADY_DAY_RE = re.compile(
    rf"\b(?P<state>{_COMPACT_SKY_STATE}|{_COMPACT_WIND_STATE})\s+"
    r"(?:all day|throughout the day)\b",
    re.IGNORECASE,
)
_COMPACT_PARTIAL_STEADY_RE = re.compile(
    rf"\b(?P<state>{_COMPACT_SKY_STATE})\s+"
    r"(?:(?:this|during the)\s+afternoon\s+and\s+)?"
    r"throughout\s+(?:the\s+)?(?:afternoon\s+and\s+evening|evening)\b",
    re.IGNORECASE,
)
_COMPACT_PARTIAL_REMAINS_CLEAR_RE = re.compile(
    rf"\b(?P<state>{_COMPACT_SKY_STATE})\s+this afternoon,\s*"
    r"(?:remaining|staying)\s+(?:mainly\s+|mostly\s+)?clear\s+"
    r"through(?:out)?\s+(?:the\s+)?evening\b",
    re.IGNORECASE,
)
_COMPACT_STEADY_BEFORE_CHANGE_RE = re.compile(
    rf"\b(?P<state>{_COMPACT_SKY_STATE})\s+during the day"
    r"(?=,\s*(?:turning|becoming)\b)",
    re.IGNORECASE,
)
_COMPACT_INITIAL_MORNING_RE = re.compile(
    rf"\b(?P<state>{_COMPACT_SKY_STATE})\s+(?:in|during) the morning"
    r"(?=,\s*(?:turning|becoming)\b[^.]*\bfrom late morning\b)",
    re.IGNORECASE,
)
_COMPACT_PERSISTENT_SKY_TAIL_RE = re.compile(
    r"(?P<change>\b(?:becoming|turning)\s+(?:overcast|cloudy)\s+from\s+"
    r"(?:early\s+|mid-?|late\s+)?(?:morning|afternoon|evening))"
    r"(?:\s+and\s+(?:remaining\s+so|continuing))?\s+through(?:out)?\s+"
    r"(?:much\s+of\s+)?(?:the\s+)?(?:afternoon\s+and\s+evening|rest\s+of\s+the\s+day)\b",
    re.IGNORECASE,
)
_COMPACT_LIGHT_WINDS_THROUGHOUT_RE = re.compile(
    r"\b(?P<state>light winds)\s+throughout\b",
    re.IGNORECASE,
)
_COMPACT_CLEAR_WITH_LIGHT_WINDS_RE = re.compile(
    r"\bA clear(?: and sunny)? day(?: from the start)?,?\s+with light winds\b",
    re.IGNORECASE,
)
_COMPACT_INCLUDING_TOTAL_RE = re.compile(
    r"\bincluding\s+a\s+total\s+of\s+"
    r"(?P<amount>\d+(?:\.\d+)?\s*(?:mm|inches?|in)\b)(?:\s+of\s+rainfall)?",
    re.IGNORECASE,
)
_COMPACT_SNOW_REACH_AREA_RE = re.compile(
    r"\b(?:snow|light rain)\s+may\s+reach\s+the\s+area,\s*"
    r"(?:though\s+it\s+is|with\s+snow)\s+mainly\s+settling\s+above(?:\s+about)?\s+"
    r"(?P<level>\d+(?:\.\d+)?)\s*(?P<unit>metres?|meters?|feet|ft|m)\b"
    r"(?:\s+to\s+\d+(?:\.\d+)?\s*(?:metres?|meters?|feet|ft|m)\b)?",
    re.IGNORECASE,
)
_COMPACT_SNOW_MAINLY_SETTLING_RE = re.compile(
    r"\bsnow\s+mainly\s+settling\s+above(?:\s+about)?\s+"
    r"(?P<level>\d+(?:\.\d+)?)\s*(?P<unit>metres?|meters?|feet|ft|m)\b"
    r"(?:\s+to\s+\d+(?:\.\d+)?\s*(?:metres?|meters?|feet|ft|m)\b)?",
    re.IGNORECASE,
)
_COMPACT_THOUGH_SNOW_DOWN_RE = re.compile(
    r"\bthough\s+snow\s+may\s+fall\s+down\s+to\s+about\s+"
    r"(?P<level>\d+(?:\.\d+)?)\s*(?P<unit>metres?|meters?|feet|ft|m)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SpotPeriodRequirement:
    """Authoritative output facts for one spot-forecast period."""

    source_label: str
    date_key: str
    partial: bool
    weekday: Optional[str] = None
    low: Optional[str] = None
    high: Optional[str] = None
    rainfall: Optional[str] = None
    snowfall: Optional[str] = None
    forbidden_rainfall: tuple[str, ...] = ()
    forbidden_snowfall: tuple[str, ...] = ()


def postprocess_compact_spot_output(
    forecast_text: str,
    *,
    gust_reporting_floor: int,
    alerts_present: bool = False,
) -> str:
    """Apply objective compact-profile wording rules without another LLM call."""
    processed = forecast_text or ""
    matches = list(_OUTPUT_PERIOD_RE.finditer(processed))
    for match in reversed(matches):
        header = match.group("header").strip()
        body = match.group("body")
        partial = _is_partial_label(header)

        if not partial:
            body = _COMPACT_DEICTIC_RE.sub(_replace_future_deictic, body)
        body = _COMPACT_WILL_BE_PRESENT_RE.sub("", body)
        body = _normalise_compact_temperature_order(body, partial=partial)
        if not alerts_present:
            body = _normalise_compact_forecast_wording(body)
            body = _remove_redundant_compact_timing(body, partial=partial)
            body = _remove_unreportable_compact_gusts(
                body,
                gust_reporting_floor=gust_reporting_floor,
            )

        start, end = match.span("body")
        processed = processed[:start] + body + processed[end:]
    return processed


def _replace_future_deictic(match: re.Match[str]) -> str:
    replacement = f"in the {match.group(1).lower()}"
    return replacement.capitalize() if match.group(0)[0].isupper() else replacement


def _normalise_compact_temperature_order(text: str, *, partial: bool) -> str:
    desired_first = "high" if partial else "low"

    def replace(match: re.Match[str]) -> str:
        values = {
            match.group("first_kind").lower(): match.group("first_value"),
            match.group("second_kind").lower(): match.group("second_value"),
        }
        if set(values) != {"low", "high"}:
            return match.group(0)
        desired_second = "low" if desired_first == "high" else "high"
        return (
            f"A {desired_first} of {values[desired_first]} and "
            f"a {desired_second} of {values[desired_second]}"
        )

    return _COMPACT_TEMPERATURE_PAIR_RE.sub(replace, text)


def _remove_unreportable_compact_gusts(
    text: str,
    *,
    gust_reporting_floor: int,
) -> str:
    def replace(match: re.Match[str]) -> str:
        return "" if float(match.group("value")) <= gust_reporting_floor else match.group(0)

    return _COMPACT_GUST_CLAUSE_RE.sub(replace, text)


def _remove_redundant_compact_timing(text: str, *, partial: bool) -> str:
    """Remove narrow steady-state spans already supplied by the period heading."""
    processed = _COMPACT_PERSISTENT_SKY_TAIL_RE.sub(r"\g<change>", text)
    processed = _COMPACT_INITIAL_MORNING_RE.sub(r"\g<state> at first", processed)
    processed = _COMPACT_STEADY_BEFORE_CHANGE_RE.sub(r"\g<state>", processed)
    processed = _COMPACT_STEADY_DAY_RE.sub(r"\g<state>", processed)
    processed = _COMPACT_LIGHT_WINDS_THROUGHOUT_RE.sub(r"\g<state>", processed)
    if partial:
        processed = _COMPACT_PARTIAL_REMAINS_CLEAR_RE.sub(r"\g<state>", processed)
        processed = _COMPACT_PARTIAL_STEADY_RE.sub(r"\g<state>", processed)
    return processed


def _normalise_compact_forecast_wording(text: str) -> str:
    """Apply narrow natural-language fixes approved for the compact profile."""
    processed = _COMPACT_CLEAR_WITH_LIGHT_WINDS_RE.sub("Clear with light winds", text)
    processed = _COMPACT_INCLUDING_TOTAL_RE.sub(r"giving \g<amount> in total", processed)
    processed = _COMPACT_SNOW_REACH_AREA_RE.sub(_replace_compact_snow_above, processed)
    processed = _COMPACT_SNOW_MAINLY_SETTLING_RE.sub(_replace_compact_snow_above, processed)
    return _COMPACT_THOUGH_SNOW_DOWN_RE.sub(_replace_compact_snow_down, processed)


def _replace_compact_snow_above(match: re.Match[str]) -> str:
    unit = _compact_height_unit(match.group("unit"))
    replacement = f"snow above about {match.group('level')} {unit}"
    return replacement.capitalize() if match.group(0)[0].isupper() else replacement


def _replace_compact_snow_down(match: re.Match[str]) -> str:
    unit = _compact_height_unit(match.group("unit"))
    return f"with snow down to about {match.group('level')} {unit}"


def _compact_height_unit(unit: str) -> str:
    return "ft" if unit.lower() in {"ft", "foot", "feet"} else "m"


def parse_spot_output_requirements(
    formatted_dataset: str,
    *,
    model_kind: str,
    external_context: str = "",
) -> list[SpotPeriodRequirement]:
    """Extract per-period output requirements from the exact dataset shown to the LLM."""
    if not formatted_dataset:
        return []

    matches = list(_DATE_LINE_RE.finditer(formatted_dataset))
    if not matches:
        return []

    prefix = formatted_dataset[: matches[0].start()]
    external_measurements = _measurement_keys(f"{prefix}\n{external_context}")
    requirements: list[SpotPeriodRequirement] = []
    is_ensemble = (model_kind or "ensemble").strip().lower() == "ensemble"

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(formatted_dataset)
        label = match.group("label").strip()
        block = formatted_dataset[match.end() : end]
        date_key = _date_key(label)
        if not date_key:
            continue

        low: Optional[str] = None
        high: Optional[str] = None
        rainfall: Optional[str] = None
        snowfall: Optional[str] = None
        forbidden_rainfall: tuple[str, ...] = ()
        forbidden_snowfall: tuple[str, ...] = ()

        if is_ensemble:
            summary = block.rsplit("RANGE SUMMARY:", 1)[-1] if "RANGE SUMMARY:" in block else ""
            low = _line_value(summary, "Likely low")
            high = _line_value(summary, "Likely high")
            rainfall = _reportable_rainfall(_line_value(summary, "Likely precipitation"))
            snowfall = _line_value(summary, "Likely snowfall")
            scenario_section = block.split("RANGE SUMMARY:", 1)[0]
            if rainfall is None:
                forbidden_rainfall = _scenario_totals(
                    scenario_section,
                    label="rainfall",
                    external_measurements=external_measurements,
                )
            if snowfall is None:
                forbidden_snowfall = _scenario_totals(
                    scenario_section,
                    label="snowfall",
                    external_measurements=external_measurements,
                )
        else:
            summary_matches = list(
                re.finditer(
                    r"(?mi)^\s*Low\s+(?P<low>[^,\n]+),\s*High\s+(?P<high>[^\n]+?)\s*$",
                    block,
                )
            )
            if summary_matches:
                low = summary_matches[-1].group("low").strip()
                high = summary_matches[-1].group("high").strip()
            rainfall = _reportable_rainfall(_total_line(block, "rainfall"))
            snowfall = _total_line(block, "snowfall")

        requirements.append(
            SpotPeriodRequirement(
                source_label=label,
                date_key=date_key,
                partial=_is_partial_label(label),
                weekday=_weekday(label),
                low=low,
                high=high,
                rainfall=rainfall,
                snowfall=snowfall,
                forbidden_rainfall=forbidden_rainfall,
                forbidden_snowfall=forbidden_snowfall,
            )
        )

    return requirements


def format_spot_output_contract(requirements: Iterable[SpotPeriodRequirement]) -> str:
    """Render a compact, high-recency checklist for the end of the user prompt."""
    periods = list(requirements)
    lines = [
        "--- MANDATORY OUTPUT CONTRACT ---",
        "Use each supplied period once. Keep the wording concise, but include every fact listed below.",
    ]
    for period in periods:
        facts: list[str] = []
        if period.low and not period.partial:
            facts.append(f"low {period.low}")
        if period.high and not period.partial:
            facts.append(f"high {period.high}")
        if period.rainfall:
            facts.append(f"rainfall {period.rainfall} (must be stated)")
        elif period.forbidden_rainfall:
            facts.append("no approved rainfall amount; do not use an individual scenario total")
        else:
            facts.append("no reportable rainfall amount supplied; do not invent one")
        if period.snowfall:
            facts.append(f"snowfall {period.snowfall} (must be stated)")
        elif period.forbidden_snowfall:
            facts.append("no approved snowfall amount; do not use an individual scenario total")
        lines.append(f"- {period.source_label}: " + "; ".join(facts) + ".")

    lines.append("Return only the forecast paragraphs.")
    return "\n".join(lines)


def validate_spot_forecast(
    forecast_text: str,
    requirements: Iterable[SpotPeriodRequirement],
    *,
    alerts_present: bool = False,
    check_wording: bool = True,
) -> list[str]:
    """Return factual violations and, optionally, stylistic wording violations."""
    violations = (
        _wording_violations(forecast_text, alerts_present=alerts_present)
        if check_wording
        else []
    )
    periods = list(requirements)
    if not periods:
        return violations

    output_periods: dict[str, tuple[str, str]] = {}
    duplicate_keys: set[str] = set()
    for match in _OUTPUT_PERIOD_RE.finditer(forecast_text or ""):
        header = match.group("header").strip()
        key = _date_key(header)
        if key:
            if key in output_periods:
                duplicate_keys.add(key)
            output_periods[key] = (header, match.group("body").strip())

    for duplicate_key in sorted(duplicate_keys):
        violations.append(f"Duplicate forecast period for {duplicate_key}.")

    expected_keys = {period.date_key for period in periods}
    for extra_key in sorted(set(output_periods) - expected_keys):
        violations.append(f"Unexpected forecast period for {extra_key}.")

    for period in periods:
        output = output_periods.get(period.date_key)
        if output is None:
            violations.append(f"Missing forecast paragraph for {period.source_label}.")
            continue
        header, body = output
        paragraph = f"{header}: {body}"

        if period.weekday and not period.partial and not re.search(
            rf"\b{re.escape(period.weekday)}\b",
            header,
            re.IGNORECASE,
        ):
            violations.append(
                f"{period.source_label} heading must use the supplied weekday {period.weekday.title()}."
            )

        if period.weekday and not alerts_present:
            other_weekdays = {
                match.group(1).lower()
                for match in _WEEKDAY_RE.finditer(body)
                if match.group(1).lower() != period.weekday
            }
            for other_weekday in sorted(other_weekdays):
                violations.append(
                    f"{period.source_label} must not describe {other_weekday.title()} "
                    "inside this forecast period."
                )

        if not period.partial:
            violations.extend(_temperature_violations(paragraph, period))
        if period.rainfall and not _amount_is_reported(paragraph, period.rainfall):
            violations.append(
                f"{period.source_label} must state the supplied rainfall amount {period.rainfall}."
            )
        if period.snowfall and not _amount_is_reported(paragraph, period.snowfall):
            violations.append(
                f"{period.source_label} must state the supplied snowfall amount {period.snowfall}."
            )
        if _RAIN_WORD_RE.search(paragraph):
            for amount in period.forbidden_rainfall:
                if _amount_is_reported(paragraph, amount):
                    violations.append(
                        f"{period.source_label} uses unapproved Scenario rainfall amount {amount}."
                    )
        if _SNOW_WORD_RE.search(paragraph):
            for amount in period.forbidden_snowfall:
                if _amount_is_reported(paragraph, amount):
                    violations.append(
                        f"{period.source_label} uses unapproved Scenario snowfall amount {amount}."
                    )

    return _deduplicate(violations)


def build_spot_correction_prompts(
    forecast_text: str,
    contract: str,
    violations: Iterable[str],
) -> tuple[str, str]:
    """Build a short copy-edit request that deliberately does not require reasoning."""
    system_prompt = """You are a precise weather-forecast copy editor.
Return the complete corrected forecast and nothing else.
Preserve every forecast period, weather fact, direction, timing, number, unit, alert, and uncertainty statement unless a listed violation explicitly requires changing it.
Do not explain your edits and do not perform extended reasoning."""
    violation_lines = "\n".join(f"- {item}" for item in violations)
    user_prompt = f"""Correct only the listed violations in the forecast.

VIOLATIONS:
{violation_lines}

{contract}

<FORECAST TO CORRECT>
{forecast_text.strip()}
<END FORECAST>
"""
    return system_prompt, user_prompt


def correction_preserves_other_numeric_facts(original: str, corrected: str) -> bool:
    """Return whether correction preserved numeric facts not governed by the contract."""
    return _non_precip_fact_signature(original) == _non_precip_fact_signature(corrected)


def _wording_violations(text: str, *, alerts_present: bool) -> list[str]:
    checks = [
        (r"\bwill be present\b", 'Use direct wording instead of "will be present".'),
        (
            r"\b(?:[a-z-]+\s+){0,2}winds?\b[^.\n]{0,100}\bwill\s+persist\b|"
            r"\b(?:north|south|east|west|northeast|northwest|southeast|southwest)erl(?:y|ies)\b"
            r"[^.\n]{0,100}\bwill\s+persist\b",
            'Use direct wind wording instead of saying winds "will persist".',
        ),
        (
            r"\bgusts?\s+(?:reaching|hitting)\s+up to\b",
            'Replace "gusts reaching/hitting up to" with "gusts up to" or "gusting".',
        ),
        (
            r"\b(?:heavy|powerful|significant)\s+(?:wind\s+)?gusts?\b|"
            r"\bquite\s+(?:strong|gusty)\b|\ba major factor\b",
            "Let wind values convey strength; remove vague or inflated wind wording.",
        ),
        (
            r"\b(?:[a-z-]+\s+){0,2}winds?\b[^.\n]{0,100}\bwill\s+be\s+common\b|"
            r"\b(?:north|south|east|west|northeast|northwest|southeast|southwest)erl(?:y|ies)\b"
            r"[^.\n]{0,100}\bwill\s+be\s+common\b",
            'Use direct wind wording instead of saying winds "will be common".',
        ),
        (
            r"\b(?:the\s+)?morning and (?:the\s+)?afternoon\b",
            'Compress an unchanged "morning and afternoon" period to "during/through the day".',
        ),
        (
            r"\b(?:the\s+)?evening and (?:the\s+)?late evening\b",
            'Do not pair the nested timing labels "evening and late evening".',
        ),
        (r"\bmostly clear or mainly clear\b", "Use one clear sky description, not two synonyms."),
        (r"(?im)^\s*\*\*Tomorrow\b", 'Remove "Tomorrow" from a full-day heading.'),
    ]
    if not alerts_present:
        checks.extend(
            [
                (r"\bovernight\b", 'Use the named day and early/late timing instead of "overnight".'),
                (
                    r"\btonight\b|\blate\s+(?:in|at)\s+(?:the\s+)?night\b",
                    'Use "late evening" before midnight or "early morning" after midnight, not "tonight/late in the night".',
                ),
            ]
        )

    violations: list[str] = []
    for pattern, message in checks:
        if re.search(pattern, text or "", re.IGNORECASE | re.MULTILINE):
            violations.append(message)
    return violations


def _temperature_violations(text: str, period: SpotPeriodRequirement) -> list[str]:
    violations: list[str] = []
    for label, expected in (("low", period.low), ("high", period.high)):
        if not expected:
            continue
        clause = _temperature_clause(text, label)
        expected_values = _temperature_values(expected)
        if not clause or any(value not in _temperature_values(clause) for value in expected_values):
            violations.append(f"{period.source_label} must state the supplied {label} {expected}.")
    return violations


def _temperature_clause(text: str, label: str) -> str:
    match = re.search(rf"\b{label}\b", text, re.IGNORECASE)
    if not match:
        return ""
    remainder = text[match.end() :]
    other = "high" if label == "low" else "low"
    stop = re.search(rf"\b{other}\b|[.;\n]", remainder, re.IGNORECASE)
    return remainder[: stop.start()] if stop else remainder


def _temperature_values(text: str) -> tuple[str, ...]:
    return tuple(_normalise_measurement(value) for value in _TEMPERATURE_RE.findall(text or ""))


def _amount_is_reported(text: str, amount: str) -> bool:
    normalised_text = re.sub(r"\s+", " ", (text or "").strip().lower())
    normalised_amount = re.sub(r"\s+", " ", (amount or "").strip().lower())
    if normalised_amount.startswith("less than "):
        target = normalised_amount.removeprefix("less than ")
        return bool(re.search(rf"\b(?:less than|under)\s+{re.escape(target)}\b", normalised_text))
    if normalised_amount.startswith("up to "):
        target = normalised_amount.removeprefix("up to ")
        return bool(re.search(rf"\bup to\s+{re.escape(target)}\b", normalised_text))
    if normalised_amount.startswith("around "):
        target = normalised_amount.removeprefix("around ")
        return bool(
            re.search(rf"\b(?:around|about|approximately)\s+{re.escape(target)}\b", normalised_text)
        )
    required = _measurement_keys(normalised_amount)
    present = _measurement_keys(normalised_text)
    return bool(required) and required.issubset(present)


def _line_value(text: str, label: str) -> Optional[str]:
    match = re.search(rf"(?mi)^\s*{re.escape(label)}\s+(.+?)\s*$", text or "")
    return match.group(1).strip() if match else None


def _total_line(text: str, label: str) -> Optional[str]:
    match = re.search(rf"(?mi)^\s*Total\s+{re.escape(label)}:\s*(.+?)\.\s*$", text or "")
    return match.group(1).strip() if match else None


def _reportable_rainfall(value: Optional[str]) -> Optional[str]:
    """Suppress rain totals that round below a useful spoken amount."""
    if not value:
        return None
    lowered = value.strip().lower()
    measurements = re.findall(r"(-?\d+(?:\.\d+)?)\s*(mm|inches?|in)\b", lowered)
    if not measurements:
        return value

    if lowered.startswith(("less than ", "under ")):
        reportable = [
            float(number) > (0.05 if unit.startswith("in") else 1.0)
            for number, unit in measurements
        ]
        return value if any(reportable) else None
    reportable = [
        float(number) >= (0.05 if unit.startswith("in") else 1.0)
        for number, unit in measurements
    ]
    return value if any(reportable) else None


def _scenario_totals(
    text: str,
    *,
    label: str,
    external_measurements: set[str],
) -> tuple[str, ...]:
    values = re.findall(rf"(?mi)^\s*Total\s+{re.escape(label)}:\s*(.+?)\.\s*$", text or "")
    unique: list[str] = []
    for value in values:
        stripped = value.strip()
        keys = _measurement_keys(stripped)
        if keys and keys.issubset(external_measurements):
            continue
        if stripped not in unique:
            unique.append(stripped)
    return tuple(unique)


def _measurement_keys(text: str) -> set[str]:
    return {_normalise_measurement(value) for value in _PRECIP_MEASUREMENT_RE.findall(text or "")}


def _normalise_measurement(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().lower())


def _date_key(label: str) -> str:
    matches = list(_DATE_KEY_RE.finditer(label or ""))
    if not matches:
        return ""
    match = matches[-1]
    return f"{int(match.group('day'))} {match.group('month').lower()}"


def _is_partial_label(label: str) -> bool:
    lowered = (label or "").strip().lower()
    return lowered.startswith(("this ", "rest of ", "today"))


def _weekday(label: str) -> Optional[str]:
    matches = re.findall(
        r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        label or "",
        re.IGNORECASE,
    )
    return matches[-1].lower() if matches else None


def _non_precip_fact_signature(text: str) -> Counter[str]:
    return Counter(_normalise_measurement(value) for value in _NON_PRECIP_FACT_RE.findall(text or ""))


def _deduplicate(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
