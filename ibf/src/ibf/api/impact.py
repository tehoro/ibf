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
from typing import Optional

from openai import OpenAI

from ..config.settings import Secrets, get_secrets
from ..util import ensure_directory, get_local_now

logger = logging.getLogger(__name__)

CACHE_DIR = ensure_directory("ibf_cache/impact")


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

    cache_path = _cache_path(context_type, name, forecast_days, timezone_name)
    cached = _load_cache(cache_path, timezone_name)
    if cached:
        logger.info("Using cached impact context for %s (%s)", name, context_type)
        return ImpactContext(name=name, content=cached, from_cache=True, cache_path=cache_path)

    context = _generate_context(context_type, name, forecast_days, timezone_name, secrets)
    if context:
        store_impact_context(
            name,
            context,
            context_type=context_type,
            forecast_days=forecast_days,
            timezone_name=timezone_name,
        )
        return ImpactContext(name=name, content=context, from_cache=False, cache_path=cache_path)

    logger.info("Impact context unavailable for %s (%s); continuing without it.", name, context_type)
    return ImpactContext(name=name, content="", from_cache=False, cache_path=cache_path)


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


def cleanup_impact_cache(max_age_days: int = 3) -> None:
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


def _cache_path(context_type: str, name: str, forecast_days: int, timezone_name: str) -> Path:
    """Return the cache file path for the given context parameters."""
    safe_name = _slugify(name)
    local_now = get_local_now(timezone_name)
    date_str = local_now.strftime("%Y%m%d")
    filename = f"{date_str}_{context_type}_{safe_name}_{forecast_days}.json"
    return CACHE_DIR / filename


def _load_cache(path: Path, timezone_name: str) -> Optional[str]:
    """Read cached context text if it exists and matches today's date."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None
    today_str = get_local_now(timezone_name).strftime("%Y%m%d")
    if path.name.split("_", 1)[0] != today_str:
        return None
    return data.get("context", "")


def _slugify(value: str) -> str:
    """Normalize a name into a lowercase, filesystem-safe slug."""
    return re.sub(r"[-\s]+", "_", re.sub(r"[^\w\s-]", "", value.strip())).lower()


def _generate_context(
    context_type: str,
    name: str,
    forecast_days: int,
    timezone_name: str,
    secrets: Secrets,
) -> str:
    """Call the configured LLM to generate fresh impact context text."""
    api_key = secrets.openai_api_key
    if not api_key:
        logger.warning("OPENAI_API_KEY is required to generate impact context.")
        return ""

    client = OpenAI(api_key=api_key)
    max_event_days = min(forecast_days, 10)
    local_now = get_local_now(timezone_name)
    local_date_str = local_now.strftime("%A %d %B %Y")
    start_iso = local_now.strftime("%Y-%m-%d")
    end_iso = (local_now + timedelta(days=max_event_days)).strftime("%Y-%m-%d")
    prompt = f"""Another assistant will soon prepare a {forecast_days}-day impact-based weather forecast and warning plan for {name} ({'an area' if context_type == 'area' else 'a location'}) in the local timezone ({timezone_name}). The local date at the time of writing is {local_date_str}.

Provide structured context covering ONLY the upcoming {forecast_days} days (from {start_iso} through {end_iso} inclusive). Identify and list information that could influence weather impacts, including:
• Existing vulnerabilities (recent floods, landslides, drought, damaged infrastructure, health concerns, etc.).
• Quantitative weather impact thresholds specific to this place (rainfall totals in mm, wind speeds in km/h, etc.) that historically trigger impacts such as flooding, landslides, transport disruption, or structural damage.
• Exposed populations and critical assets (informal settlements, flood-prone neighbourhoods, schools, hospitals, tourism areas, ports, etc.).
• Major upcoming public events occurring today or within the next {max_event_days} days (sporting events, national holidays, concerts, festivals). For every event listed, provide the exact calendar date in ISO form `YYYY-MM-DD – description`. Do NOT include events before {start_iso} or after {end_iso}. If no such events exist, explicitly state “No significant public events identified during this period.”

Use only recent, publicly available information. Present the findings as plain text grouped under the headings:
Existing Vulnerabilities
Weather Impact Thresholds
Exposed Populations and Assets
Upcoming Events

For each bullet, write one to two sentences explaining why the item matters for impact-based forecasting over the next {forecast_days} days. Do not include URLs, citations, or conversational conclusions—only the requested structured context."""

    try:
        response = client.responses.create(
            model="gpt-4o-mini",
            input=prompt,
            timeout=60.0,
        )
        context_text = _extract_response_text(response)
    except Exception as exc:
        logger.warning("Responses API failed for impact context (%s): %s. Falling back to chat completions.", name, exc)
        try:
            fallback = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You supply concise contextual information for weather impact assessments."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=1800,
            )
            if fallback.choices:
                context_text = fallback.choices[0].message.content.strip()
            else:
                context_text = ""
        except Exception as chat_exc:
            logger.error("Chat completions fallback failed for impact context (%s): %s", name, chat_exc)
            return ""

    context_text = _clean_context_text(context_text)
    if context_text:
        logger.info("Generated impact context for %s (%s); %d characters", name, context_type, len(context_text))
    return context_text


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

