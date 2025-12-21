"""
Wrappers around OpenAI-compatible APIs and Google Gemini.
"""

from __future__ import annotations

import logging
import re
import json
from typing import Any, Optional, Tuple

from openai import OpenAI

from .settings import LLMSettings
from .costs import get_model_cost

logger = logging.getLogger(__name__)
_LAST_COST_CENTS: float = 0.0


def generate_forecast_text(
    prompt: str,
    system_prompt: str,
    settings: LLMSettings,
    *,
    reasoning: Optional[dict] = None,
) -> str:
    """
    Execute the LLM request and return the cleaned forecast text.

    Dispatches to either the Google Gemini client or the generic OpenAI-compatible
    client based on the settings.

    Args:
        prompt: The user prompt containing the data.
        system_prompt: The system prompt defining the persona and rules.
        settings: Configuration for the LLM provider.

    Returns:
        The generated forecast text, cleaned of any "thinking" artifacts.
    """
    if settings.is_google:
        return _call_gemini(prompt, system_prompt, settings)
    return _call_openai_compatible(prompt, system_prompt, settings, reasoning=reasoning)


def _call_openai_compatible(
    prompt: str,
    system_prompt: str,
    settings: LLMSettings,
    *,
    reasoning: Optional[dict],
) -> str:
    """Call an OpenAI-compatible Chat Completions endpoint and clean the result."""
    client = OpenAI(api_key=settings.api_key, base_url=settings.base_url)
    request_kwargs = {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
        "stream": False,
    }
    if reasoning:
        request_kwargs["extra_body"] = reasoning
    response = client.chat.completions.create(**request_kwargs)
    _log_usage_and_cost(settings.model, getattr(response, "usage", None))
    message = response.choices[0].message if response.choices else None
    raw_text = _coerce_message_content(getattr(message, "content", None))
    if not raw_text and message is not None:
        try:
            payload = message.model_dump()
        except Exception:
            payload = repr(message)
        snippet = json.dumps(payload, default=str) if isinstance(payload, dict) else str(payload)
        logger.warning(
            "LLM empty content payload for model %s (truncated): %s",
            settings.model,
            snippet[:2000],
        )
    cleaned = _clean_llm_output(raw_text)
    if not cleaned and raw_text:
        logger.warning(
            "Cleaned LLM output was empty for model %s; returning raw text.",
            settings.model,
        )
        cleaned = raw_text.strip()
    if not cleaned and message is not None:
        reasoning = getattr(message, "reasoning", None)
        reasoning_text = _coerce_message_content(getattr(reasoning, "content", None))
        if reasoning_text:
            logger.warning(
                "Using reasoning content as fallback output for model %s.", settings.model
            )
            cleaned = reasoning_text.strip()
    if not cleaned:
        choice = response.choices[0] if response.choices else None
        logger.warning(
            "LLM response for model %s contained no usable text (finish_reason=%s).",
            settings.model,
            getattr(choice, "finish_reason", None),
        )
    return cleaned


def _call_gemini(prompt: str, system_prompt: str, settings: LLMSettings) -> str:
    """Invoke the Google Gemini SDK and return cleaned text."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.api_key)
    config = types.GenerateContentConfig(
        temperature=settings.temperature,
        max_output_tokens=settings.max_tokens,
        system_instruction=system_prompt,
    )
    try:
        response = client.models.generate_content(
            model=settings.model,
            contents=prompt,
            config=config,
        )
    except Exception as exc:
        logger.warning("Gemini request failed with system instruction; retrying (%s)", exc)
        fallback_config = types.GenerateContentConfig(
            temperature=settings.temperature,
            max_output_tokens=settings.max_tokens,
        )
        response = client.models.generate_content(
            model=settings.model,
            contents=f"{system_prompt}\n\n{prompt}",
            config=fallback_config,
        )

    text = (getattr(response, "text", None) or "").strip()
    if text:
        return _clean_llm_output(text)

    candidates = getattr(response, "candidates", None)
    if candidates:
        candidate = candidates[0]
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) if content is not None else None
        if parts:
            text_value = getattr(parts[0], "text", None)
            if text_value:
                return _clean_llm_output(text_value)

    raise RuntimeError(
        f"Gemini response was empty or blocked: {getattr(response, 'prompt_feedback', None)}"
    )


def _clean_llm_output(text: str) -> str:
    """
    Strip common "thinking" wrappers (<think> blocks or explicit reasoning lists).

    Some models (like DeepSeek R1) output their chain-of-thought before the final answer.
    This function removes that content to leave only the forecast.

    Args:
        text: Raw output from the LLM.

    Returns:
        Cleaned text ready for publishing.
    """
    if not text:
        return ""

    # Remove <think>...</think> sections if present.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    # Remove obvious reasoning bullet lists before the first "**" header.
    first_header = re.search(r"\*\*.+?\*\*", text)
    if first_header:
        text = text[first_header.start():]

    # Remove leftover "Let's..." analytical paragraphs.
    text = re.sub(r"Let'?s [^\n]+\n", "", text)
    text = re.sub(r"The instruction says[^\n]+\n", "", text)

    return text.strip()


def _coerce_message_content(content: Any) -> str:
    """
    Normalize the various content payloads returned by OpenAI-compatible endpoints.

    Handles plain strings, structured content-part lists, and objects that expose a
    `.text` attribute (as seen in recent OpenAI/OpenRouter SDKs).
    """
    if not content:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    if isinstance(content, (list, tuple)):
        for item in content:
            if not item:
                continue
            if isinstance(item, str):
                parts.append(item)
                continue
            text_value = getattr(item, "text", None)
            if not text_value and isinstance(item, dict):
                text_value = item.get("text")
            if text_value:
                parts.append(str(text_value))
        return "\n".join(parts).strip()
    text_attr = getattr(content, "text", None)
    if text_attr:
        return str(text_attr)
    return str(content)


def _log_usage_and_cost(model_name: str, usage: Any) -> None:
    """Log prompt/completion/cached tokens and estimated USD cents for a chat call."""
    if not usage:
        logger.info(
            "LLM usage – model=%s prompt_tokens=%s cached_prompt_tokens=%s completion_tokens=%s total_tokens=%s cost_usd_cents=%s",
            model_name,
            "n/a",
            "n/a",
            "n/a",
            "n/a",
            "n/a",
        )
        return

    try:
        prompt_tokens, cached_prompt_tokens, completion_tokens, total_tokens = _normalize_chat_usage(usage)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Unable to normalize LLM usage data (%s): %s", type(usage), exc)
        logger.info(
            "LLM usage – model=%s prompt_tokens=%s cached_prompt_tokens=%s completion_tokens=%s total_tokens=%s cost_usd_cents=%s",
            model_name,
            getattr(usage, "prompt_tokens", getattr(usage, "input_tokens", "n/a")),
            "n/a",
            getattr(usage, "completion_tokens", getattr(usage, "output_tokens", "n/a")),
            getattr(usage, "total_tokens", "n/a"),
            "n/a",
        )
        return

    cost_entry = get_model_cost(model_name)
    cost_display = "n/a"
    cost_cents = 0.0
    if cost_entry:
        usd = cost_entry.cost_for_usage(
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            cached_input_tokens=cached_prompt_tokens,
        )
        cost_cents = usd * 100
        cost_display = f"{cost_cents:.2f}"
    else:
        cost_cents = 0.0

    logger.info(
        "LLM usage – model=%s prompt_tokens=%s cached_prompt_tokens=%s completion_tokens=%s total_tokens=%s cost_usd_cents=%s",
        model_name,
        prompt_tokens,
        cached_prompt_tokens,
        completion_tokens,
        total_tokens,
        cost_display,
    )
    global _LAST_COST_CENTS
    _LAST_COST_CENTS = cost_cents


def _normalize_chat_usage(usage: Any) -> Tuple[int, int, int, int]:
    """Return (prompt_tokens, cached_prompt_tokens, completion_tokens, total_tokens)."""

    def _get_attr(obj: Any, attr: str) -> Any:
        if hasattr(obj, attr):
            return getattr(obj, attr)
        if isinstance(obj, dict):
            return obj.get(attr)
        return None

    input_tokens = _get_attr(usage, "input_tokens")
    if input_tokens is not None:
        cached = _get_attr(_get_attr(usage, "input_tokens_details") or {}, "cached_tokens") or 0
        output_tokens = _get_attr(usage, "output_tokens") or 0
        total_tokens = _get_attr(usage, "total_tokens") or (int(input_tokens) + int(output_tokens))
        return int(input_tokens), int(cached), int(output_tokens), int(total_tokens)

    prompt_tokens = _get_attr(usage, "prompt_tokens")
    if prompt_tokens is not None:
        cached = _get_attr(_get_attr(usage, "prompt_tokens_details") or {}, "cached_tokens") or 0
        completion_tokens = _get_attr(usage, "completion_tokens") or 0
        total_tokens = _get_attr(usage, "total_tokens") or (int(prompt_tokens) + int(completion_tokens))
        return int(prompt_tokens), int(cached), int(completion_tokens), int(total_tokens)

    raise ValueError("Unsupported usage payload structure")


def consume_last_cost_cents() -> float:
    """Return and reset the most recent LLM cost (in USD cents)."""
    global _LAST_COST_CENTS
    value = _LAST_COST_CENTS
    _LAST_COST_CENTS = 0.0
    return value
