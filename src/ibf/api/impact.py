"""
Impact-based forecast context loader with filesystem caching.
"""

from __future__ import annotations

import hashlib
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
DEFAULT_CONTEXT_LLM = "gemini-3-flash-preview"
CONTEXT_SECTION_HEADINGS = [
    "Existing Vulnerabilities",
    "Weather Impact Thresholds",
    "Exposed Populations and Assets",
    "Upcoming Events",
]


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
    context_llm: str = DEFAULT_CONTEXT_LLM,
    extra_context: Optional[str] = None,
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
        context_llm: LLM identifier to use for impact context generation.
        extra_context: Optional user-supplied context to prioritize.

    Returns:
        An ImpactContext object containing the text.
    """
    secrets = secrets or get_secrets()
    cleanup_impact_cache()
    context_llm = (context_llm or DEFAULT_CONTEXT_LLM).strip()

    cached_context, cache_path = _load_recent_cache(
        context_type,
        name,
        forecast_days,
        timezone_name,
        context_llm=context_llm,
        extra_context=extra_context,
    )
    if cached_context:
        logger.info("Using cached impact context for %s (%s)", name, context_type)
        return ImpactContext(
            name=name,
            content=cached_context,
            from_cache=True,
            cache_path=cache_path,
            cost_cents=0.0,
        )

    context, cost_cents = _generate_context(
        context_type,
        name,
        forecast_days,
        timezone_name,
        secrets,
        context_llm=context_llm,
        extra_context=extra_context,
    )
    if context:
        store_impact_context(
            name,
            context,
            context_type=context_type,
            forecast_days=forecast_days,
            timezone_name=timezone_name,
            context_llm=context_llm,
            extra_context=extra_context,
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
    context_llm: str = DEFAULT_CONTEXT_LLM,
    extra_context: Optional[str] = None,
) -> None:
    """
    Save generated impact context to the filesystem cache.

    Args:
        name: Name of the location or area.
        content: The context text to save.
        context_type: "location", "area", or "regional".
        forecast_days: Number of days covered.
        timezone_name: Local timezone.
        context_llm: LLM identifier used to generate this context.
    """
    context_llm = (context_llm or DEFAULT_CONTEXT_LLM).strip()
    cache_path = _cache_path(
        context_type,
        name,
        forecast_days,
        timezone_name,
        context_llm=context_llm,
        extra_context=extra_context,
    )
    payload = {
        "context": content,
        "timestamp": get_local_now(timezone_name).isoformat(),
        "context_type": context_type,
        "name": name,
        "forecast_days": forecast_days,
        "context_llm": context_llm,
        "extra_context": extra_context,
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
    legacy_suffix: bool = False,
    context_llm: str = DEFAULT_CONTEXT_LLM,
    extra_context: Optional[str] = None,
) -> Path:
    """
    Return the cache file path for the given context parameters.

    legacy_suffix=True preserves the older filename scheme that included the forecast_days suffix.
    """
    safe_name = _slugify(name)
    context_llm = (context_llm or DEFAULT_CONTEXT_LLM).strip()
    local_now = date_override or get_local_now(timezone_name)
    date_str = local_now.strftime("%Y%m%d")
    filename = f"{date_str}_{context_type}_{safe_name}"
    if context_llm and context_llm.strip().lower() != DEFAULT_CONTEXT_LLM.lower():
        filename += f"__{_slugify(context_llm)}"
    extra_key = _extra_context_key(extra_context)
    if extra_key:
        filename += f"__ctx{extra_key}"
    if legacy_suffix:
        filename += f"_{forecast_days}"
    filename += ".json"
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
    *,
    context_llm: str = DEFAULT_CONTEXT_LLM,
    extra_context: Optional[str] = None,
) -> Tuple[Optional[str], Path]:
    """
    Attempt to load a cached context from the past MAX_CONTEXT_AGE_DAYS (inclusive).

    Returns:
        (context_text_or_None, cache_path_for_today)
    """
    context_llm = (context_llm or DEFAULT_CONTEXT_LLM).strip()
    local_now = get_local_now(timezone_name)
    has_extra = _extra_context_key(extra_context) is not None
    for offset in range(MAX_CONTEXT_AGE_DAYS):
        date_candidate = local_now - timedelta(days=offset)
        cache_path = _cache_path(
            context_type,
            name,
            forecast_days,
            timezone_name,
            date_override=date_candidate,
            context_llm=context_llm,
            extra_context=extra_context,
        )
        cached = _load_cache(cache_path)
        if cached:
            return cached, cache_path
        if not has_extra:
            legacy_path = _cache_path(
                context_type,
                name,
                forecast_days,
                timezone_name,
                date_override=date_candidate,
                legacy_suffix=True,
                context_llm=context_llm,
            )
            cached_legacy = _load_cache(legacy_path)
            if cached_legacy:
                return cached_legacy, legacy_path

            # Backwards-compat: the historical cache key didn't include any context_llm suffix.
            if context_llm.strip().lower() == DEFAULT_CONTEXT_LLM.lower():
                legacy_no_model = _cache_path(
                    context_type,
                    name,
                    forecast_days,
                    timezone_name,
                    date_override=date_candidate,
                    context_llm=DEFAULT_CONTEXT_LLM,
                )
                cached_no_model = _load_cache(legacy_no_model)
                if cached_no_model:
                    return cached_no_model, legacy_no_model
                legacy_no_model_suffix = _cache_path(
                    context_type,
                    name,
                    forecast_days,
                    timezone_name,
                    date_override=date_candidate,
                    legacy_suffix=True,
                    context_llm=DEFAULT_CONTEXT_LLM,
                )
                cached_no_model_suffix = _load_cache(legacy_no_model_suffix)
                if cached_no_model_suffix:
                    return cached_no_model_suffix, legacy_no_model_suffix

    today_path = _cache_path(
        context_type,
        name,
        forecast_days,
        timezone_name,
        date_override=local_now,
        context_llm=context_llm,
        extra_context=extra_context,
    )
    return None, today_path


def _slugify(value: str) -> str:
    """Normalize a name into a lowercase, filesystem-safe slug."""
    return re.sub(r"[-\s]+", "_", re.sub(r"[^\w\s-]", "", value.strip())).lower()


def _extra_context_key(extra_context: Optional[str]) -> Optional[str]:
    """Return a short hash for user-supplied context, or None when absent."""
    if not extra_context:
        return None
    normalized = re.sub(r"\s+", " ", extra_context.strip())
    if not normalized:
        return None
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]


def _generate_context(
    context_type: str,
    name: str,
    forecast_days: int,
    timezone_name: str,
    secrets: Secrets,
    *,
    context_llm: str,
    extra_context: Optional[str] = None,
) -> Tuple[str, float]:
    """Generate impact context using the requested LLM."""
    context_llm = (context_llm or DEFAULT_CONTEXT_LLM).strip()
    max_event_days = EVENT_LOOKAHEAD_DAYS
    local_now = get_local_now(timezone_name)
    start_iso = local_now.strftime("%Y-%m-%d")
    end_iso = (local_now + timedelta(days=forecast_days)).strftime("%Y-%m-%d")
    events_end_iso = (local_now + timedelta(days=max_event_days)).strftime("%Y-%m-%d")
    local_date_str = local_now.strftime("%A %d %B %Y")
    extra_context_block = ""
    if extra_context:
        extra_context_block = (
            "\nAdditional local context supplied by a knowledgeable source (treat as authoritative and emphasize it):\n"
            f"{extra_context.strip()}\n"
        )

    prompt = f"""Another assistant will soon prepare {forecast_days}-day impact-based weather forecast and associated warnings for {name} ({'an area' if context_type == 'area' else 'a location'}).

To provide context for that forecast, identify and list all relevant contextual information that could influence weather impacts, including:

    • Current national and local conditions and vulnerabilities (e.g., recent flooding or landslides, ongoing drought, damaged infrastructure, health outbreaks, power or water supply issues).

    • Weather impact thresholds specific to this location (IMPORTANT): Identify any known rainfall amounts (in mm), wind speeds (in km/h), or other weather thresholds that historically trigger impacts such as flooding, landslides, road closures, power outages, or structural damage in this specific area. For example: "Flash flooding typically occurs with rainfall exceeding 25mm in 24 hours" or "Landslides are a risk when rainfall exceeds 50mm over 2-3 days" or "Wind damage to informal structures begins around 60 km/h gusts". Include any location-specific vulnerability factors that affect these thresholds (e.g., poor drainage, deforested slopes, damaged infrastructure from recent events).

    • Upcoming events that may increase exposure or vulnerability (e.g. public holidays, major sports events, concerts, festivals, school terms or exams). These must be truly major events with large public attendance (citywide holidays, stadium events, large festivals), and they must occur at the location (or within 20 km of it). If an event is small, niche, or only a modest local gathering, omit it. If you are not confident it is major, omit it. Do NOT include minor or distant events or national events that are not tied to the location. CRITICAL: Only include events that occur TODAY or within the next {max_event_days} days (through {events_end_iso}). Do NOT include any events that have already occurred (events before today) or events more than {max_event_days} days in the future. For any events listed, you MUST provide the exact date (e.g., "15 November 2025" or "November 15, 2025"). Vague descriptions like "mid-November", "late November", "early November", or "around November 15" are NOT acceptable. If you cannot find the exact date, do not include that event.

    • Key vulnerable groups and assets (e.g. informal settlements, flood-prone neighbourhoods, critical infrastructure, tourism areas, coastal communities).

Use only recent, publicly available information covering the period from {start_iso} through {end_iso} for vulnerabilities/thresholds/exposures. Present your findings as a structured list, grouped under headings such as:

    • "Existing Vulnerabilities"

    • "Weather Impact Thresholds"

    • "Exposed Populations and Assets"

    • "Upcoming Events"

For each item, add 1–2 sentences explaining why it is relevant for an impact-based forecast and warning for the next {forecast_days} days. For events in the "Upcoming Events" section, you MUST include the exact date (day, month, and year) for each event, and only include events occurring today or within the next {max_event_days} days (up to {events_end_iso}). Do not use vague timeframes and do not include past events or events beyond that window. For the "Weather Impact Thresholds" section, provide specific quantitative thresholds when available (e.g., "X mm rainfall", "Y km/h winds"), as these will be used to determine when impacts should be mentioned in the forecast.
{extra_context_block}

Formatting requirements:

    • Begin immediately with the first heading. Do NOT include any introduction, preamble, concluding remarks, summaries, or sign-offs.
    • You MUST include all four headings below, even if you have no items for a section.
    • If a section has no relevant items, still include the heading and add a single bullet: "• No relevant items found."
    • Use Markdown level-3 headings in the exact form:
        ### Existing Vulnerabilities
        ### Weather Impact Thresholds
        ### Exposed Populations and Assets
        ### Upcoming Events
    • Under each heading, use bullet-style lines (you may use the "•" bullet character) that concisely state the information.

IMPORTANT: Provide only the structured context information as plain text. Do NOT include any URLs, web links, or citations. Do not offer to draft the forecast or ask if you should proceed. Just provide the requested contextual information as text only."""

    if _is_gemini_model(context_llm):
        context_text, cost_cents = _generate_context_gemini_search(
            prompt,
            model_name=_normalize_gemini_model_name(context_llm),
            api_key=secrets.gemini_api_key,
            name=name,
        )
    else:
        context_text, cost_cents = _generate_context_openai_web_search(
            prompt,
            model_name=context_llm,
            api_key=secrets.openai_api_key,
            name=name,
        )

    context_text = _clean_context_text(context_text)
    if context_text:
        logger.info("Generated impact context for %s (%s); %d characters", name, context_type, len(context_text))
    else:
        cost_cents = 0.0
    return context_text, cost_cents


def _is_gemini_model(model_name: str) -> bool:
    lowered = (model_name or "").strip().lower()
    return lowered.startswith("gemini-") or lowered.startswith("google/gemini-")


def _normalize_gemini_model_name(model_name: str) -> str:
    """
    Accept either:
    - "gemini-3-flash-preview"
    - "google/gemini-3-flash-preview"
    and normalize to the direct Gemini model name for the Google SDK.
    """
    raw = (model_name or "").strip()
    lowered = raw.lower()
    if lowered.startswith("google/gemini-"):
        return raw.split("/", 1)[1]
    return raw


def _generate_context_openai_web_search(
    prompt: str,
    *,
    model_name: str,
    api_key: Optional[str],
    name: str,
) -> tuple[str, float]:
    if not api_key:
        logger.warning("OPENAI_API_KEY is required to generate impact context.")
        return "", 0.0

    client = OpenAI(api_key=api_key)
    model_name = (model_name or DEFAULT_CONTEXT_LLM).strip()
    cost_cents = 0.0
    try:
        response = client.responses.create(
            model=model_name,
            input=prompt,
            tools=[{"type": "web_search"}],
            timeout=60.0,
        )
        cost_cents = _log_usage_and_cost(model_name, getattr(response, "usage", None))
        context_text = _extract_response_text(response)
        return context_text, cost_cents
    except Exception as exc:
        logger.warning(
            "Responses API with web search failed for impact context (%s): %s. Falling back to chat completions.",
            name,
            exc,
        )
        try:
            fallback = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "You supply concise contextual information for weather impact assessments."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=1800,
            )
            cost_cents = _log_usage_and_cost(model_name, getattr(fallback, "usage", None))
            if fallback.choices:
                return (fallback.choices[0].message.content or "").strip(), cost_cents
            return "", 0.0
        except Exception as chat_exc:
            logger.error("Chat completions fallback failed for impact context (%s): %s", name, chat_exc)
            return "", 0.0


def _generate_context_gemini_search(
    prompt: str,
    *,
    model_name: str,
    api_key: Optional[str],
    name: str,
) -> tuple[str, float]:
    if not api_key:
        logger.warning("GEMINI_API_KEY is required to generate impact context with %s.", model_name)
        return "", 0.0

    # Lazy import so the rest of the system can run without this optional dependency.
    import os
    from contextlib import contextmanager

    from google import genai  # type: ignore[import-not-found]
    from google.genai import types  # type: ignore[import-not-found]

    @contextmanager
    def _force_gemini_api_key(key: str):
        """
        google-genai will prefer GOOGLE_API_KEY over GEMINI_API_KEY if both are set.
        This repo uses GOOGLE_API_KEY for Maps/Geocoding, so for Gemini context calls we
        temporarily hide GOOGLE_API_KEY and force GEMINI_API_KEY.
        """
        old_google_api_key = os.environ.get("GOOGLE_API_KEY")
        old_gemini_api_key = os.environ.get("GEMINI_API_KEY")
        try:
            os.environ.pop("GOOGLE_API_KEY", None)
            os.environ["GEMINI_API_KEY"] = key
            yield
        finally:
            if old_google_api_key is not None:
                os.environ["GOOGLE_API_KEY"] = old_google_api_key
            else:
                os.environ.pop("GOOGLE_API_KEY", None)
            if old_gemini_api_key is not None:
                os.environ["GEMINI_API_KEY"] = old_gemini_api_key
            else:
                os.environ.pop("GEMINI_API_KEY", None)

    def _is_complete(text: str) -> bool:
        if not text:
            return False
        normalized = _standardize_context_headings(text)
        return all(f"### {heading}" in normalized for heading in CONTEXT_SECTION_HEADINGS)

    def _looks_truncated(text: str) -> bool:
        if not text:
            return False
        tail = text.strip()[-12:]
        # Heuristic: if we end on an alphanumeric with no terminal punctuation, assume truncation.
        if re.search(r"[A-Za-z0-9]$", tail) and not re.search(r"[.!?\)\]\}\"\']$", tail):
            return True
        return False

    def _first_missing_heading(text: str) -> Optional[str]:
        for heading in CONTEXT_SECTION_HEADINGS:
            marker = f"### {heading}"
            if marker not in text:
                return marker
        return None

    def _merge_context_chunks(existing: str, addition: str) -> str:
        """Combine continuation chunks without introducing word breaks."""
        if not existing:
            return addition.strip()
        if not addition:
            return existing
        existing = existing.rstrip()
        addition = addition.lstrip()

        def _should_join_words(left: str, right: str) -> bool:
            left_match = re.search(r"([A-Za-z]+)$", left)
            right_match = re.match(r"([A-Za-z]+)", right)
            if not left_match or not right_match:
                return False
            left_word = left_match.group(1)
            right_word = right_match.group(1).lower()
            if right_word in {
                "the",
                "and",
                "for",
                "to",
                "of",
                "in",
                "on",
                "at",
                "by",
                "or",
                "an",
                "a",
                "is",
                "are",
                "was",
                "were",
                "be",
                "as",
                "if",
                "it",
                "its",
                "from",
                "this",
                "that",
                "these",
                "those",
            }:
                return False
            if len(left_word) <= 2:
                return True
            if len(right_word) <= 3:
                return True
            return False

        if _should_join_words(existing, addition):
            return (existing + addition).strip()
        if re.search(r"[A-Za-z0-9]$", existing) and re.match(r"[A-Za-z0-9]", addition):
            return (existing + " " + addition).strip()
        return (existing + "\n\n" + addition).strip()

    with _force_gemini_api_key(api_key):
        client = genai.Client(api_key=api_key)
    tool = types.Tool(google_search=types.GoogleSearch())
    # Allow a longer response; we enforce structure via post-checks/continuations.
    config = types.GenerateContentConfig(
        tools=[tool],
        temperature=0.2,
        max_output_tokens=15000,
    )

    def _call(contents: str) -> tuple[str, float]:
        try:
            with _force_gemini_api_key(api_key):
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                )
        except Exception as exc:
            logger.error("Gemini Google Search grounding failed for impact context (%s): %s", name, exc)
            return "", 0.0
        text = (getattr(response, "text", None) or "").strip()
        usage = getattr(response, "usage_metadata", None)
        return text, _log_gemini_usage_and_cost(model_name, usage)

    # First pass.
    combined, cost_cents = _call(prompt)
    if not combined:
        return "", 0.0

    # If Gemini returns an incomplete or abruptly-truncated answer, ask it to continue.
    # This can happen for small locations (sparse results) or when the model hits an internal stop.
    for _ in range(2):
        if _is_complete(combined) and not _looks_truncated(combined):
            break
        missing = _first_missing_heading(combined)
        tail = combined[-400:] if len(combined) > 400 else combined
        continuation = (
            "You are continuing an incomplete impact-context answer.\n"
            "Do NOT repeat any headings or bullets already provided.\n"
            "First complete any unfinished sentence/bullet if the previous text ended abruptly.\n"
            "Then provide the remaining required sections using EXACT Markdown level-3 headings:\n"
            "### Existing Vulnerabilities\n"
            "### Weather Impact Thresholds\n"
            "### Exposed Populations and Assets\n"
            "### Upcoming Events\n"
            "If you cannot find any relevant items for a section, include the heading and write one bullet saying so.\n"
            "Do NOT include URLs or citations.\n\n"
            "Already provided (do not repeat):\n"
            f"{combined}\n\n"
        )
        if missing:
            continuation += f"Start with the next missing heading: {missing}\n"
        continuation += f"\nLast part of previous output (for continuity):\n{tail}\n"
        next_text, next_cost = _call(continuation)
        if not next_text:
            break
        cost_cents += next_cost
        combined = _merge_context_chunks(combined, next_text)

    return combined, cost_cents


def _log_gemini_usage_and_cost(model_name: str, usage_metadata: Any) -> float:
    """
    Log Gemini usage and estimated cost (in USD cents) for the impact context call.

    The google-genai SDK exposes usage as `usage_metadata` (prompt/candidates/total token counts).
    """
    if not usage_metadata:
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

    def _get(obj: Any, key: str) -> Any:
        if hasattr(obj, key):
            return getattr(obj, key)
        if isinstance(obj, dict):
            return obj.get(key)
        return None

    input_tokens = _get(usage_metadata, "prompt_token_count")
    output_tokens = _get(usage_metadata, "candidates_token_count")
    total_tokens = _get(usage_metadata, "total_token_count")
    try:
        input_tokens_i = int(input_tokens or 0)
        output_tokens_i = int(output_tokens or 0)
        total_tokens_i = int(total_tokens or (input_tokens_i + output_tokens_i))
    except Exception:
        input_tokens_i, output_tokens_i, total_tokens_i = 0, 0, 0

    cost_entry = get_model_cost(model_name)
    cost_display = "n/a"
    usd = 0.0
    if cost_entry:
        usd = cost_entry.cost_for_usage(
            input_tokens=input_tokens_i,
            output_tokens=output_tokens_i,
            cached_input_tokens=0,
        )
        cost_display = f"{usd * 100:.2f}"

    logger.info(
        "Impact context LLM usage – model=%s input_tokens=%s cached_input_tokens=%s output_tokens=%s total_tokens=%s cost_usd_cents=%s",
        model_name,
        input_tokens_i,
        0,
        output_tokens_i,
        total_tokens_i,
        cost_display,
    )
    return usd * 100 if cost_entry else 0.0


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
        r"^Here is the requested.*?\n\n",
        r"\n\nIf you'd like.*",
        r"\n\nWould you like.*",
        r"\n\nLet me know.*",
        r"\n\nEach of these items.*",
    ]
    for pattern in unwanted:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE | re.DOTALL | re.MULTILINE)

    cleaned = _standardize_context_headings(cleaned)
    cleaned = _trim_before_first_heading(cleaned)
    return cleaned.strip()


def _standardize_context_headings(text: str) -> str:
    """Force known section headings to Markdown h3 style."""
    if not text:
        return ""
    updated = text
    for heading in CONTEXT_SECTION_HEADINGS:
        pattern = rf"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?{re.escape(heading)}(?:\*\*|__)?\s*:?"
        updated = re.sub(pattern, f"### {heading}", updated, flags=re.IGNORECASE | re.MULTILINE)
    return updated


def _trim_before_first_heading(text: str) -> str:
    """Remove any intro content before the first heading."""
    if not text:
        return ""
    first_idx = text.find("### ")
    if first_idx > 0:
        return text[first_idx:]
    return text


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
