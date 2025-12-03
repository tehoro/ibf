"""
Impact-based forecast context loader with filesystem caching.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

from openai import OpenAI

from ..config.settings import Secrets, get_secrets
from ..llm.costs import get_model_cost
from ..util import ensure_directory, get_local_now

logger = logging.getLogger(__name__)

CACHE_DIR = ensure_directory("ibf_cache/impact")
MAX_CONTEXT_AGE_DAYS = 3
EVENT_LOOKAHEAD_DAYS = 10


@dataclass
class ImpactContext:
    """
    Container for impact-based context data.

    Attributes:
        name: Name of the location or area.
        content: The generated context text.
        from_cache: True if the content was loaded from disk cache.
        cache_path: Path to the cache file (if applicable).
    """
    name: str
    content: str
    from_cache: bool
    cache_path: Optional[Path] = None
    cost_cents: float = 0.0


def fetch_impact_context(
    name: str,
    *,
    context_type: str = "location",
    forecast_days: int = 4,
    secrets: Optional[Secrets] = None,
    timezone_name: str = "UTC",
) -> ImpactContext:
    """
    Retrieve or generate impact context for a location or area.

    Checks the filesystem cache first. If missing or stale, queries an LLM to generate
    context based on vulnerabilities, events, and thresholds.

    Args:
        name: Name of the location or area.
        context_type: "location", "area", or "regional".
        forecast_days: Number of days to cover in the context.
        secrets: Optional Secrets instance.
        timezone_name: Local timezone for date calculations.

    Returns:
        An ImpactContext object containing the text.
    """
    secrets = secrets or get_secrets()
    cleanup_impact_cache()

    cached_context, cache_path = _load_recent_cache(context_type, name, forecast_days, timezone_name)
    if cached_context:
        logger.info("Using cached impact context for %s (%s)", name, context_type)
        return ImpactContext(
            name=name,
            content=cached_context,
            from_cache=True,
            cache_path=cache_path,
            cost_cents=0.0,
        )

    context, cost_cents = _generate_context(context_type, name, forecast_days, timezone_name, secrets)
    if context:
        store_impact_context(
            name,
            context,
            context_type=context_type,
            forecast_days=forecast_days,
            timezone_name=timezone_name,
        )
        return ImpactContext(
            name=name,
            content=context,
            from_cache=False,
            cache_path=cache_path,
            cost_cents=cost_cents,
        )

    logger.info("Impact context unavailable for %s (%s); continuing without it.", name, context_type)
    return ImpactContext(name=name, content="", from_cache=False, cache_path=cache_path, cost_cents=0.0)


def store_impact_context(
    name: str,
    content: str,
    *,
    context_type: str = "location",
    forecast_days: int = 4,
    timezone_name: str = "UTC",
) -> None:
    """
    Save generated impact context to the filesystem cache.

    Args:
        name: Name of the location or area.
        content: The context text to save.
        context_type: "location", "area", or "regional".
        forecast_days: Number of days covered.
        timezone_name: Local timezone.
    """
    cache_path = _cache_path(context_type, name, forecast_days, timezone_name)
    payload = {
        "context": content,
        "timestamp": get_local_now(timezone_name).isoformat(),
        "context_type": context_type,
        "name": name,
        "forecast_days": forecast_days,
    }
    try:
        cache_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to write impact cache %s (%s)", cache_path, exc)


def cleanup_impact_cache(max_age_days: int = MAX_CONTEXT_AGE_DAYS) -> None:
    """
    Remove old impact context files from the cache.

    Args:
        max_age_days: Files older than this will be deleted.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    for path in CACHE_DIR.glob("*.json"):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) < cutoff:
                path.unlink()
        except OSError:
            continue


def _cache_path(
    context_type: str,
    name: str,
    forecast_days: int,
    timezone_name: str,
    *,
    date_override: Optional[datetime] = None,
) -> Path:
    """Return the cache file path for the given context parameters."""
    safe_name = _slugify(name)
    local_now = date_override or get_local_now(timezone_name)
    date_str = local_now.strftime("%Y%m%d")
    filename = f"{date_str}_{context_type}_{safe_name}_{forecast_days}.json"
    return CACHE_DIR / filename


def _load_cache(path: Path) -> Optional[str]:
    """Read cached context text if it exists and is within the allowed age."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    timestamp_raw = data.get("timestamp")
    cached_ts = None
    if timestamp_raw:
        try:
            cached_ts = datetime.fromisoformat(timestamp_raw)
            if cached_ts.tzinfo is None:
                cached_ts = cached_ts.replace(tzinfo=timezone.utc)
        except ValueError:
            cached_ts = None
    if cached_ts is None:
        cached_ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

    now_utc = datetime.now(timezone.utc)
    if now_utc - cached_ts.astimezone(timezone.utc) > timedelta(days=MAX_CONTEXT_AGE_DAYS):
        return None
    return data.get("context", "")


def _load_recent_cache(
    context_type: str,
    name: str,
    forecast_days: int,
    timezone_name: str,
) -> Tuple[Optional[str], Path]:
    """
    Attempt to load a cached context from the past MAX_CONTEXT_AGE_DAYS (inclusive).

    Returns:
        (context_text_or_None, cache_path_for_today)
    """
    local_now = get_local_now(timezone_name)
    for offset in range(MAX_CONTEXT_AGE_DAYS):
        date_candidate = local_now - timedelta(days=offset)
        cache_path = _cache_path(
            context_type,
            name,
            forecast_days,
            timezone_name,
            date_override=date_candidate,
        )
        cached = _load_cache(cache_path)
        if cached:
            return cached, cache_path

    today_path = _cache_path(context_type, name, forecast_days, timezone_name, date_override=local_now)
    return None, today_path


def _slugify(value: str) -> str:
    """Normalize a name into a lowercase, filesystem-safe slug."""
    return re.sub(r"[-\s]+", "_", re.sub(r"[^\w\s-]", "", value.strip())).lower()


def _generate_context(
    context_type: str,
    name: str,
    forecast_days: int,
    timezone_name: str,
    secrets: Secrets,
) -> Tuple[str, float]:
    """Call GPT-4o with web search enabled (fallback to chat completions) for context."""
    api_key = secrets.openai_api_key
    if not api_key:
        logger.warning("OPENAI_API_KEY is required to generate impact context.")
        return "", 0.0

    client = OpenAI(api_key=api_key)
    max_event_days = EVENT_LOOKAHEAD_DAYS
    local_now = get_local_now(timezone_name)
    start_iso = local_now.strftime("%Y-%m-%d")
    end_iso = (local_now + timedelta(days=forecast_days)).strftime("%Y-%m-%d")
    events_end_iso = (local_now + timedelta(days=max_event_days)).strftime("%Y-%m-%d")
    local_date_str = local_now.strftime("%A %d %B %Y")
    prompt = f"""Another assistant will soon prepare {forecast_days}-day impact-based weather forecast and associated warnings for {name} ({'an area' if context_type == 'area' else 'a location'}).

To provide context for that forecast, identify and list all relevant contextual information that could influence weather impacts, including:

    • Current national and local conditions and vulnerabilities (e.g., recent flooding or landslides, ongoing drought, damaged infrastructure, health outbreaks, power or water supply issues).

    • Weather impact thresholds specific to this location (IMPORTANT): Identify any known rainfall amounts (in mm), wind speeds (in km/h), or other weather thresholds that historically trigger impacts such as flooding, landslides, road closures, power outages, or structural damage in this specific area. For example: "Flash flooding typically occurs with rainfall exceeding 25mm in 24 hours" or "Landslides are a risk when rainfall exceeds 50mm over 2-3 days" or "Wind damage to informal structures begins around 60 km/h gusts". Include any location-specific vulnerability factors that affect these thresholds (e.g., poor drainage, deforested slopes, damaged infrastructure from recent events).

    • Upcoming events that may increase exposure or vulnerability (e.g. public holidays, major sports events, concerts, festivals, school terms or exams). These should be major events that have quite large public attendance, not small minor ones. CRITICAL: Only include events that occur TODAY or within the next {max_event_days} days (through {events_end_iso}). Do NOT include any events that have already occurred (events before today) or events more than {max_event_days} days in the future. For any events listed, you MUST provide the exact date (e.g., "15 November 2025" or "November 15, 2025"). Vague descriptions like "mid-November", "late November", "early November", or "around November 15" are NOT acceptable. If you cannot find the exact date, do not include that event.

    • Key vulnerable groups and assets (e.g. informal settlements, flood-prone neighbourhoods, critical infrastructure, tourism areas, coastal communities).

Use only recent, publicly available information covering the period from {start_iso} through {end_iso} for vulnerabilities/thresholds/exposures. Present your findings as a structured list, grouped under headings such as:

    • "Existing Vulnerabilities"

    • "Weather Impact Thresholds"

    • "Exposed Populations and Assets"

    • "Upcoming Events"

For each item, add 1–2 sentences explaining why it is relevant for an impact-based forecast and warning for the next {forecast_days} days. For events in the "Upcoming Events" section, you MUST include the exact date (day, month, and year) for each event, and only include events occurring today or within the next {max_event_days} days (up to {events_end_iso}). Do not use vague timeframes and do not include past events or events beyond that window. For the "Weather Impact Thresholds" section, provide specific quantitative thresholds when available (e.g., "X mm rainfall", "Y km/h winds"), as these will be used to determine when impacts should be mentioned in the forecast.

IMPORTANT: Provide only the structured context information as plain text. Do NOT include any URLs, web links, or citations. Do not offer to draft the forecast or ask if you should proceed. Just provide the requested contextual information as text only."""

    cost_cents = 0.0
    try:
        response = client.responses.create(
            model="gpt-4o",
            input=prompt,
            tools=[{"type": "web_search"}],
            timeout=60.0,
        )
        cost_cents = _log_usage_and_cost("gpt-4o", getattr(response, "usage", None))
        context_text = _extract_response_text(response)
    except Exception as exc:
        logger.warning(
            "Responses API with web search failed for impact context (%s): %s. Falling back to chat completions.",
            name,
            exc,
        )
        try:
            fallback = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You supply concise contextual information for weather impact assessments."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=1800,
            )
            cost_cents = _log_usage_and_cost("gpt-4o", getattr(fallback, "usage", None))
            if fallback.choices:
                context_text = fallback.choices[0].message.content.strip()
            else:
                context_text = ""
        except Exception as chat_exc:
            logger.error("Chat completions fallback failed for impact context (%s): %s", name, chat_exc)
            return "", 0.0

    context_text = _clean_context_text(context_text)
    if context_text:
        logger.info("Generated impact context for %s (%s); %d characters", name, context_type, len(context_text))
    else:
        cost_cents = 0.0
    return context_text, cost_cents


def _extract_response_text(response) -> str:
    """Coerce OpenAI Responses API output into a simple text string."""
    text = getattr(response, "output_text", "") or ""
    if text:
        return text.strip()
    output = getattr(response, "output", None)
    if isinstance(output, list):
        for item in output:
            candidate = getattr(item, "text", None)
            if candidate:
                return candidate.strip()
            contents = getattr(item, "content", None)
            if isinstance(contents, list):
                for content_item in contents:
                    candidate = getattr(content_item, "text", None)
                    if candidate:
                        return candidate.strip()
    return ""


def _clean_context_text(text: str) -> str:
    """Strip links, chatter, and formatting glitches from LLM output."""
    if not text:
        return ""
    cleaned = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"www\.\S+", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)  # collapse spaces/tabs but preserve newlines
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"\n[ \t]+\n", "\n\n", cleaned)
    cleaned = re.sub(r"\s*(###\s)", r"\n\n\1", cleaned)  # ensure headings start on their own line
    cleaned = cleaned.strip()
    unwanted = [
        r"\n\nIf you'd like.*",
        r"\n\nWould you like.*",
        r"\n\nLet me know.*",
    ]
    for pattern in unwanted:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    return cleaned.strip()


def _log_usage_and_cost(model_name: str, usage: Any) -> float:
    """Log token usage and estimated cost (in USD cents) for the impact context call."""
    if not usage:
        logger.info(
            "Impact context LLM usage – model=%s input_tokens=%s cached_input_tokens=%s output_tokens=%s total_tokens=%s cost_usd_cents=%s",
            model_name,
            "n/a",
            "n/a",
            "n/a",
            "n/a",
            "n/a",
        )
        return 0.0

    try:
        input_tokens, cached_input_tokens, output_tokens, total_tokens = _normalize_usage(usage)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Unable to normalize LLM usage data (%s): %s", type(usage), exc)
        logger.info(
            "Impact context LLM usage – model=%s input_tokens=%s cached_input_tokens=%s output_tokens=%s total_tokens=%s cost_usd_cents=%s",
            model_name,
            getattr(usage, "input_tokens", "n/a"),
            "n/a",
            getattr(usage, "output_tokens", "n/a"),
            getattr(usage, "total_tokens", "n/a"),
            "n/a",
        )
        return 0.0

    cost_entry = get_model_cost(model_name)
    cost_display = "n/a"
    usd = 0.0
    if cost_entry:
        usd = cost_entry.cost_for_usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
        )
        cost_display = f"{usd * 100:.2f}"

    logger.info(
        "Impact context LLM usage – model=%s input_tokens=%s cached_input_tokens=%s output_tokens=%s total_tokens=%s cost_usd_cents=%s",
        model_name,
        input_tokens,
        cached_input_tokens,
        output_tokens,
        total_tokens,
        cost_display,
    )
    return usd * 100 if cost_entry else 0.0


def _normalize_usage(usage: Any) -> tuple[int, int, int, int]:
    """Return (input, cached_input, output, total) tokens from Responses or Chat usage payloads."""
    def _get_attr(obj: Any, attr: str) -> Any:
        if hasattr(obj, attr):
            return getattr(obj, attr)
        if isinstance(obj, dict):
            return obj.get(attr)
        return None

    input_tokens = _get_attr(usage, "input_tokens")
    if input_tokens is not None:
        cached = _get_attr(_get_attr(usage, "input_tokens_details"), "cached_tokens") or 0
        output = _get_attr(usage, "output_tokens") or 0
        total = _get_attr(usage, "total_tokens") or (input_tokens + output)
        return int(input_tokens), int(cached), int(output), int(total)

    prompt_tokens = _get_attr(usage, "prompt_tokens")
    completion_tokens = _get_attr(usage, "completion_tokens")
    if prompt_tokens is not None or completion_tokens is not None:
        input_tokens = int(prompt_tokens or 0)
        output_tokens = int(completion_tokens or 0)
        total_tokens = int(_get_attr(usage, "total_tokens") or (input_tokens + output_tokens))
        return input_tokens, 0, output_tokens, total_tokens

    raise ValueError("Unsupported usage payload structure")

