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
_ALERT_BLOCK_RE = re.compile(
    r"(?ms)^ALERT from (?P<source>[^\n]+?):\s*\n"
    r"Title:\s*(?P<title>[^\n]+?)\s*\n"
    r"Valid from:\s*(?P<onset>[^\n]+?)\s*\n"
    r"Expires:\s*(?P<expires>[^\n]+?)\s*\n"
    r"Description:\s*(?P<description>.*?)\s*\n<END ALERT>"
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
_COMPACT_CLEAR_AND_CLEAR_RE = re.compile(
    r"\bclear(?: skies)?\s+and\s+(?P<modifier>mainly|mostly)\s+clear(?: skies)?\b",
    re.IGNORECASE,
)
_COMPACT_STRENGTHENING_TO_DIRECTION_RE = re.compile(
    r"\bstrengthening\s+to\s+(?:an?\s+)?"
    r"(?P<direction>(?:north|south|east|west)(?:-?(?:east|west))?erly)\b",
    re.IGNORECASE,
)
_COMPACT_STANDALONE_TOTAL_RE = re.compile(
    r"\bGiving\s+(?P<amount>\d+(?:\.\d+)?\s*(?:mm|cm|inches?|in)\b)\s+in\s+total\.\s*",
    re.IGNORECASE,
)
_COMPACT_TOTAL_AFTER_CLEARING_RE = re.compile(
    r"(?P<precip>\b[^.!?]{0,160}\b(?:rain|showers?|snow)\b[^.!?]{0,100}?),\s+"
    r"(?P<transition>(?:clearing|easing|turning)[^.!?]{0,100}?),\s+"
    r"(?P<amount>with\s+\d+(?:\.\d+)?\s*(?:mm|cm|inches?|in)\s+"
    r"(?:of\s+(?:rainfall|snowfall)\s+)?expected(?:\s+in\s+total)?)"
    r"(?P<punct>[.!?])",
    re.IGNORECASE,
)
_COMPACT_WIND_CLEARING_RE = re.compile(
    r"(?P<wind>\b[^.!?]{0,140}\b(?:winds?|northerlies|southerlies|easterlies|westerlies)\b"
    r"[^.!?]{0,140}?),\s*before\s+clearing\s+(?P<timing>[^.!?]+)(?P<punct>[.!?])",
    re.IGNORECASE,
)
_COMPACT_INLINE_TEMPERATURE_NARRATIVE_RE = re.compile(
    rf",\s*(?:with\s+)?(?:temperatures?\b|(?:a|the)\s+(?:low|high)\b)"
    rf"[^.!?]*{_COMPACT_TEMPERATURE_VALUE}[^.!?]*[.!?]",
    re.IGNORECASE,
)
_COMPACT_TEMPERATURE_NARRATIVE_SENTENCE_RE = re.compile(
    r"(?P<lead>^|[.!?]\s+)(?:(?:temperatures?|the\s+temperature)\b|"
    rf"(?:a|the)\s+(?:low|high)\b)[^.!?]*{_COMPACT_TEMPERATURE_VALUE}[^.!?]*[.!?]\s*",
    re.IGNORECASE,
)
_COMPACT_PARTIAL_REST_OF_DAY_RE = re.compile(
    rf"\b(?P<state>{_COMPACT_SKY_STATE})\s+for\s+the\s+rest\s+of\s+the\s+day\b",
    re.IGNORECASE,
)
_COMPACT_PARTIAL_THIS_AFTERNOON_RE = re.compile(
    rf"\b(?P<state>{_COMPACT_SKY_STATE})\s+this\s+afternoon\b",
    re.IGNORECASE,
)
_COMPACT_PARTIAL_MOST_OF_EVENING_RE = re.compile(
    rf"\b(?P<state>{_COMPACT_SKY_STATE})\s+for\s+most\s+of\s+the\s+evening\b",
    re.IGNORECASE,
)
_COMPACT_PARTIAL_AFTER_MIDNIGHT_CLAUSE_RE = re.compile(
    r",\s*(?:turning|becoming|clearing|changing)[^.!?]{0,80}"
    r"\b(?:in|during)\s+the\s+early\s+morning\b",
    re.IGNORECASE,
)
_COMPACT_EVENING_TO_EARLY_MORNING_RE = re.compile(
    r"(?P<evening>\bevening)\s+before\s+(?P<change>clearing|easing|ending)\s+"
    r"in\s+the\s+early\s+morning\b",
    re.IGNORECASE,
)
_COMPACT_SUNNY_EVENING_RE = re.compile(
    r"\b(?:(?P<modifier>mostly|mainly)\s+)?sunny"
    r"(?P<timing>\s+(?:in\s+the\s+)?(?:(?:early|late)\s+)?(?:this\s+)?evening)\b",
    re.IGNORECASE,
)
_COMPACT_CHANGE_RE = re.compile(
    r"\b(?:becoming|turning|developing|starting|remaining|leading\s+to|followed\s+by)\b",
    re.IGNORECASE,
)
_COMPACT_LATER_CHANGE_RE = re.compile(
    r"\b(?:before|then|later)\b[^.!?]{0,80}"
    r"\b(?:easing|clearing|ending|stopping|turning|becoming)\b",
    re.IGNORECASE,
)
_COMPACT_PERSISTENCE_TAIL_RE = re.compile(
    r",?\s+(?:and\s+)?(?:"
    r"remaining(?:\s+so|\s+(?:mostly\s+|mainly\s+)?(?:clear|cloudy|overcast))?\s+"
    r"(?:for\s+)?(?:the\s+)?(?:rest\s+of\s+the\s+day|during\s+the\s+day|"
    r"(?:much|most)\s+of\s+(?:the\s+)?(?:day|morning|afternoon|evening)|"
    r"through(?:out)?\s+(?:much\s+of\s+)?(?:the\s+)?(?:day|afternoon|evening)"
    r"(?:\s+and\s+(?:the\s+)?evening)?)"
    r"|(?:continuing|lasting)\s+(?:through(?:out)?|into)\s+"
    r"(?:(?:much|most)\s+of\s+)?"
    r"(?:the\s+)?(?:day|morning|afternoon|evening)(?:\s+and\s+(?:the\s+)?evening)?"
    r"|through\s+to\s+the\s+rest\s+of\s+the\s+day"
    r"|throughout\s+(?:the\s+)?day\s+and\s+(?:the\s+)?evening"
    r")",
    re.IGNORECASE,
)
_COMPACT_LIGHT_DIRECTIONAL_WIND_RE = re.compile(
    r"\blight(?:\s+(?:north|south|east|west)(?:-?(?:east|west))?erly)?\s+winds\b",
    re.IGNORECASE,
)
_COMPACT_WINDS_ARE_LIGHT_RE = re.compile(
    r"\bwinds\s+(?:will\s+be|are|remain)\s+light\b",
    re.IGNORECASE,
)
_COMPACT_SIGNIFICANT_WIND_RE = re.compile(
    r"\b(?:gust|strong|fresh|gale|severe|storm-force|hurricane-force)\w*\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SpotAlertRequirement:
    """Authoritative source, identity, and validity for one alert."""

    source: str
    title: str
    onset: str
    expires: str


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
    alerts: tuple[SpotAlertRequirement, ...] = ()


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
            body = _normalise_compact_forecast_wording(body, partial=partial)
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
        processed = _COMPACT_PARTIAL_THIS_AFTERNOON_RE.sub(r"\g<state>", processed)
        processed = _COMPACT_PARTIAL_REST_OF_DAY_RE.sub(r"\g<state>", processed)
        processed = _COMPACT_PARTIAL_MOST_OF_EVENING_RE.sub(r"\g<state>", processed)
        processed = _COMPACT_PARTIAL_REMAINS_CLEAR_RE.sub(r"\g<state>", processed)
        processed = _COMPACT_PARTIAL_STEADY_RE.sub(r"\g<state>", processed)
    return re.sub(
        rf"\b(?P<state>{_COMPACT_SKY_STATE}),\s+with light winds\b",
        r"\g<state> with light winds",
        processed,
        flags=re.IGNORECASE,
    )


def _normalise_compact_forecast_wording(text: str, *, partial: bool) -> str:
    """Apply narrow natural-language fixes approved for the compact profile."""
    processed = _COMPACT_CLEAR_WITH_LIGHT_WINDS_RE.sub("Clear with light winds", text)
    processed = _COMPACT_INCLUDING_TOTAL_RE.sub(r"giving \g<amount> in total", processed)
    processed = _COMPACT_SNOW_REACH_AREA_RE.sub(_replace_compact_snow_above, processed)
    processed = _COMPACT_SNOW_MAINLY_SETTLING_RE.sub(_replace_compact_snow_above, processed)
    processed = _COMPACT_THOUGH_SNOW_DOWN_RE.sub(_replace_compact_snow_down, processed)
    processed = _COMPACT_CLEAR_AND_CLEAR_RE.sub(_replace_clear_and_clear, processed)
    processed = _COMPACT_STRENGTHENING_TO_DIRECTION_RE.sub(
        r"strengthening and turning \g<direction>",
        processed,
    )
    processed = _normalise_wind_clearing(processed)
    processed = _attach_standalone_precipitation_total(processed)
    processed = _COMPACT_TOTAL_AFTER_CLEARING_RE.sub(
        r"\g<precip>, \g<amount>, \g<transition>\g<punct>",
        processed,
    )
    processed = _normalise_compact_period_boundary(processed, partial=partial)
    processed = _COMPACT_SUNNY_EVENING_RE.sub(_replace_sunny_evening, processed)
    processed = re.sub(
        r"\bleading\s+to\s+a\s+period\s+of\b",
        "turning to",
        processed,
        flags=re.IGNORECASE,
    )
    processed = re.sub(
        r"\bduring/through\s+the\s+day\b",
        "during the day",
        processed,
        flags=re.IGNORECASE,
    )
    processed = _remove_implicit_persistence_tails(processed)
    processed = _simplify_compact_light_winds(processed)
    if not partial:
        processed = _remove_repeated_temperature_narrative(processed)
    return processed


def _replace_sunny_evening(match: re.Match[str]) -> str:
    """Use night-time sky terminology for compact evening forecasts."""
    modifier = match.group("modifier")
    replacement = f"{modifier + ' ' if modifier else ''}clear{match.group('timing')}"
    return replacement.capitalize() if match.group(0)[0].isupper() else replacement


def _normalise_compact_period_boundary(text: str, *, partial: bool) -> str:
    """Keep compact timing inside the paragraph's supplied calendar period."""
    if partial:
        processed = _COMPACT_PARTIAL_AFTER_MIDNIGHT_CLAUSE_RE.sub("", text)
        processed = re.sub(
            r"\bby\s+(?:the\s+)?(?:early\s+morning|late\s+(?:night|tonight))\b",
            "by midnight",
            processed,
            flags=re.IGNORECASE,
        )
        processed = re.sub(
            r"\bthrough\s+the\s+night\b",
            "through the evening",
            processed,
            flags=re.IGNORECASE,
        )
        processed = re.sub(
            r"\blate\s+tonight\b",
            "late this evening",
            processed,
            flags=re.IGNORECASE,
        )
        return re.sub(r"\btonight\b", "this evening", processed, flags=re.IGNORECASE)

    processed = _COMPACT_EVENING_TO_EARLY_MORNING_RE.sub(
        r"\g<evening>, then \g<change> late",
        text,
    )
    processed = re.sub(
        r"\blate\s+(?:in\s+the\s+night|at\s+night|night)\b",
        "late in the evening",
        processed,
        flags=re.IGNORECASE,
    )
    processed = re.sub(
        r"\bovernight\b",
        "late in the evening",
        processed,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\btonight\b", "in the evening", processed, flags=re.IGNORECASE)


def _remove_implicit_persistence_tails(text: str) -> str:
    """Remove end-of-period persistence after an onset or change is already timed."""

    def simplify_sentence(match: re.Match[str]) -> str:
        sentence = match.group(0)
        if not _COMPACT_CHANGE_RE.search(sentence):
            return sentence

        def remove_tail(tail: re.Match[str]) -> str:
            if _COMPACT_LATER_CHANGE_RE.search(sentence[tail.end() :]):
                return tail.group(0)
            return ""

        simplified = _COMPACT_PERSISTENCE_TAIL_RE.sub(remove_tail, sentence)
        simplified = re.sub(r"\s+,", ",", simplified)
        return re.sub(r",\s*,", ",", simplified)

    return re.sub(r"(?:\d\.\d|[^.!?])+[.!?]", simplify_sentence, text)


def _simplify_compact_light_winds(text: str) -> str:
    """Suppress minor directions and shifts when the prose itself calls winds light."""

    def simplify_sentence(match: re.Match[str]) -> str:
        sentence = match.group(0)
        if _COMPACT_SIGNIFICANT_WIND_RE.search(sentence):
            return sentence
        stripped = sentence.lstrip()
        leading = sentence[: len(sentence) - len(stripped)]
        if _COMPACT_LIGHT_DIRECTIONAL_WIND_RE.match(stripped) or _COMPACT_WINDS_ARE_LIGHT_RE.match(
            stripped
        ):
            return f"{leading}Light winds."

        inline = re.search(
            r",?\s+with\s+light(?:\s+(?:north|south|east|west)"
            r"(?:-?(?:east|west))?erly)?\s+winds\b[^.!?]*",
            sentence,
            re.IGNORECASE,
        )
        if inline:
            simplified = sentence[: inline.start()] + ", with light winds" + sentence[inline.end() :]
            return re.sub(
                r"\b(clear|mainly clear|mostly clear),\s+with light winds\b",
                r"\1 with light winds",
                simplified,
                flags=re.IGNORECASE,
            )
        return sentence

    return re.sub(r"(?:\d\.\d|[^.!?])+[.!?]", simplify_sentence, text)


def _replace_clear_and_clear(match: re.Match[str]) -> str:
    replacement = f"{match.group('modifier').lower()} clear"
    return replacement.capitalize() if match.group(0)[0].isupper() else replacement


def _normalise_wind_clearing(text: str) -> str:
    """Make a wind-subject clearing clause unambiguous."""
    rain_present = bool(_RAIN_WORD_RE.search(text))

    def replace(match: re.Match[str]) -> str:
        ending = f"easing {match.group('timing')}"
        if rain_present:
            ending += " as the rain clears"
        return f"{match.group('wind')}, {ending}{match.group('punct')}"

    return _COMPACT_WIND_CLEARING_RE.sub(replace, text)


def _attach_standalone_precipitation_total(text: str) -> str:
    """Move a detached total back to the nearest preceding precipitation sentence."""
    total_match = _COMPACT_STANDALONE_TOTAL_RE.search(text)
    if not total_match:
        return text

    preceding = text[: total_match.start()]
    sentences = list(re.finditer(r"(?:\d\.\d|[^.!?])+[.!?]", preceding))
    precipitation_sentence = next(
        (
            sentence
            for sentence in reversed(sentences)
            if _RAIN_WORD_RE.search(sentence.group(0)) or _SNOW_WORD_RE.search(sentence.group(0))
        ),
        None,
    )
    if precipitation_sentence is None:
        return text

    sentence = precipitation_sentence.group(0).rstrip()
    attached = f"{sentence[:-1].rstrip()}, giving {total_match.group('amount')} in total."
    processed = (
        preceding[: precipitation_sentence.start()]
        + attached
        + preceding[precipitation_sentence.end() :]
        + text[total_match.end() :]
    )
    return re.sub(r" {2,}", " ", processed)


def _remove_repeated_temperature_narrative(text: str) -> str:
    """Keep full-day extrema in their final low/high sentence only."""
    summaries = list(_COMPACT_TEMPERATURE_PAIR_RE.finditer(text))
    if not summaries:
        return text

    summary = summaries[-1]
    preceding = text[: summary.start()]
    preceding = _COMPACT_INLINE_TEMPERATURE_NARRATIVE_RE.sub(".", preceding)

    def remove_sentence(match: re.Match[str]) -> str:
        lead = match.group("lead")
        return "" if not lead else f"{lead.rstrip()} "

    preceding = _COMPACT_TEMPERATURE_NARRATIVE_SENTENCE_RE.sub(remove_sentence, preceding)
    return preceding + text[summary.start() :]


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
        alerts = _parse_alert_requirements(block)

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
                alerts=alerts,
            )
        )

    return requirements


def _parse_alert_requirements(text: str) -> tuple[SpotAlertRequirement, ...]:
    """Extract the exact alert facts embedded in one supplied forecast period."""
    alerts: list[SpotAlertRequirement] = []
    for match in _ALERT_BLOCK_RE.finditer(text or ""):
        alerts.append(
            SpotAlertRequirement(
                source=match.group("source").strip(),
                title=match.group("title").strip(),
                onset=match.group("onset").strip(),
                expires=match.group("expires").strip(),
            )
        )
    return tuple(alerts)


def format_spot_output_contract(requirements: Iterable[SpotPeriodRequirement]) -> str:
    """Render a compact, high-recency checklist for the end of the user prompt."""
    periods = list(requirements)
    lines = [
        "--- MANDATORY OUTPUT CONTRACT ---",
        "Use each supplied period once. Keep the wording concise, but include every fact listed below.",
        "Only the precipitation amounts listed below are approved for publication. When none is "
        "listed for a period, describe precipitation qualitatively. Never use an individual "
        "scenario total.",
    ]
    for period in periods:
        facts: list[str] = []
        if period.low and not period.partial:
            facts.append(f"low {period.low}")
        if period.high and not period.partial:
            facts.append(f"high {period.high}")
        if period.rainfall:
            facts.append(f"rainfall {period.rainfall} (must be stated)")
        if period.snowfall:
            facts.append(f"snowfall {period.snowfall} (must be stated)")
        if facts:
            lines.append(f"- {period.source_label}: " + "; ".join(facts) + ".")
        else:
            lines.append(f"- {period.source_label}.")
        for alert in period.alerts:
            lines.append(
                f"  ALERT: {alert.source} {alert.title}; valid from {alert.onset}; "
                f"expires {alert.expires}. State the source, exact title, and both exact "
                "times in this period; never place it in a non-overlapping period."
            )

    lines.append("Return only the forecast paragraphs.")
    return "\n".join(lines)


def validate_spot_forecast(
    forecast_text: str,
    requirements: Iterable[SpotPeriodRequirement],
    *,
    alerts_present: bool = False,
    check_wording: bool = True,
    allow_missing_daily_extremes: bool = False,
) -> list[str]:
    """Return factual violations and, optionally, stylistic wording violations.

    When ``allow_missing_daily_extremes`` is true, an absent full-day low/high is
    tolerated, but an explicitly reported wrong value remains a violation.
    """
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

        if period.weekday:
            allowed_weekdays = {period.weekday}
            for alert in period.alerts:
                allowed_weekdays.update(
                    match.group(1).lower()
                    for match in _WEEKDAY_RE.finditer(f"{alert.onset} {alert.expires}")
                )
            other_weekdays = {
                match.group(1).lower()
                for match in _WEEKDAY_RE.finditer(body)
                if match.group(1).lower() not in allowed_weekdays
            }
            for other_weekday in sorted(other_weekdays):
                violations.append(
                    f"{period.source_label} must not describe {other_weekday.title()} "
                    "inside this forecast period."
                )

        if not period.partial:
            violations.extend(
                _temperature_violations(
                    paragraph,
                    period,
                    allow_missing_daily_extremes=allow_missing_daily_extremes,
                )
            )
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

    violations.extend(_alert_violations(output_periods, periods))
    return _deduplicate(violations)


def repair_missing_spot_temperatures(
    forecast_text: str,
    requirements: Iterable[SpotPeriodRequirement],
) -> str:
    """Append authoritative daily extremes when the model omitted their labels.

    This deliberately repairs omissions only. If a paragraph explicitly states a
    different low or high, normal compliance validation still rejects it.
    """
    period_by_key = {period.date_key: period for period in requirements}
    repaired = forecast_text or ""
    for match in reversed(list(_OUTPUT_PERIOD_RE.finditer(repaired))):
        header = match.group("header").strip()
        period = period_by_key.get(_date_key(header))
        if period is None or period.partial:
            continue

        body = match.group("body")
        paragraph = f"{header}: {body}"
        missing: list[tuple[str, str]] = []
        for label, expected in (("low", period.low), ("high", period.high)):
            if expected and not _temperature_values(_temperature_clause(paragraph, label)):
                missing.append((label, expected))
        if not missing:
            continue

        if len(missing) == 2:
            sentence = (
                f"The {missing[0][0]} is expected near {missing[0][1]} and "
                f"the {missing[1][0]} near {missing[1][1]}."
            )
        else:
            label, expected = missing[0]
            sentence = f"The {label} is expected near {expected}."

        trailing = body[len(body.rstrip()) :]
        content = body.rstrip()
        if content:
            separator = " " if content.endswith((".", "!", "?")) else ". "
            content = f"{content}{separator}{sentence}"
        else:
            content = sentence
        start, end = match.span("body")
        repaired = repaired[:start] + content + trailing + repaired[end:]

    return repaired


def _alert_violations(
    output_periods: dict[str, tuple[str, str]],
    periods: list[SpotPeriodRequirement],
) -> list[str]:
    """Validate alert identity, validity times, and placement by supplied period."""
    alert_by_key: dict[tuple[str, str, str, str], SpotAlertRequirement] = {}
    affected_keys: dict[tuple[str, str, str, str], set[str]] = {}
    period_labels = {period.date_key: period.source_label for period in periods}

    for period in periods:
        for alert in period.alerts:
            key = _alert_key(alert)
            alert_by_key[key] = alert
            affected_keys.setdefault(key, set()).add(period.date_key)

    violations: list[str] = []
    for period in periods:
        output = output_periods.get(period.date_key)
        if output is None:
            continue
        header, body = output
        paragraph = f"{header}: {body}"
        for key, alert in alert_by_key.items():
            title_present = _contains_phrase(paragraph, alert.title)
            expected_here = period.date_key in affected_keys[key]
            if not expected_here:
                if title_present:
                    valid_labels = ", ".join(
                        period_labels[date_key]
                        for date_key in sorted(affected_keys[key])
                        if date_key in period_labels
                    )
                    violations.append(
                        f"{alert.title} must not appear in {period.source_label}; "
                        f"its supplied validity overlaps {valid_labels}."
                    )
                continue

            if not title_present:
                violations.append(
                    f"{period.source_label} must state the alert title {alert.title}."
                )
                continue
            if not _contains_phrase(paragraph, alert.source):
                violations.append(
                    f"{period.source_label} must attribute {alert.title} to {alert.source}."
                )
            for timing_label, value in (("start", alert.onset), ("end", alert.expires)):
                clock = _clock_time(value)
                if clock and not re.search(rf"(?<!\d){re.escape(clock)}(?!\d)", paragraph):
                    violations.append(
                        f"{period.source_label} must state the exact {timing_label} time "
                        f"{clock} for {alert.title}."
                    )

    return violations


def _alert_key(alert: SpotAlertRequirement) -> tuple[str, str, str, str]:
    return tuple(
        re.sub(r"\s+", " ", value.strip().lower())
        for value in (alert.source, alert.title, alert.onset, alert.expires)
    )


def _contains_phrase(text: str, phrase: str) -> bool:
    normalised_text = re.sub(r"\s+", " ", (text or "").strip().lower())
    normalised_phrase = re.sub(r"\s+", " ", (phrase or "").strip().lower())
    return bool(normalised_phrase) and normalised_phrase in normalised_text


def _clock_time(value: str) -> str:
    match = re.search(r"(?<!\d)(?:[01]\d|2[0-3]):[0-5]\d(?!\d)", value or "")
    return match.group(0) if match else ""


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


def correction_preserves_other_numeric_facts(
    original: str,
    corrected: str,
    *,
    governed_values: Iterable[str] = (),
) -> bool:
    """Return whether correction preserved numeric facts not governed by the contract."""
    original_signature = _non_precip_fact_signature(original)
    corrected_signature = _non_precip_fact_signature(corrected)
    governed_signature = _non_precip_fact_signature("\n".join(governed_values))
    for value in governed_signature:
        original_signature.pop(value, None)
        corrected_signature.pop(value, None)
    return original_signature == corrected_signature


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


def _temperature_violations(
    text: str,
    period: SpotPeriodRequirement,
    *,
    allow_missing_daily_extremes: bool = False,
) -> list[str]:
    violations: list[str] = []
    for label, expected in (("low", period.low), ("high", period.high)):
        if not expected:
            continue
        clause = _temperature_clause(text, label)
        expected_values = _temperature_values(expected)
        if not clause:
            if allow_missing_daily_extremes:
                continue
            violations.append(f"{period.source_label} must state the supplied {label} {expected}.")
            continue
        if any(value not in _temperature_values(clause) for value in expected_values):
            violations.append(f"{period.source_label} must state the supplied {label} {expected}.")
    return violations


def _temperature_clause(text: str, label: str) -> str:
    other = "high" if label == "low" else "low"
    for match in re.finditer(rf"\b{label}\b", text, re.IGNORECASE):
        remainder = text[match.end() :]
        stop = re.search(rf"\b{other}\b|[.;\n]", remainder, re.IGNORECASE)
        clause = remainder[: stop.start()] if stop else remainder
        if _temperature_values(clause):
            return clause
    return ""


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
