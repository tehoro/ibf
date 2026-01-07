"""
Wrappers around OpenAI-compatible APIs and Google Gemini.
"""

from __future__ import annotations

import logging
import re
import json
from contextvars import ContextVar
from typing import Any, Optional

from openai import OpenAI

from .settings import LLMSettings
from .usage import log_gemini_usage_and_cost, log_openai_usage_and_cost
from ..util.env import force_gemini_api_key

logger = logging.getLogger(__name__)
_LAST_COST_CENTS: ContextVar[float] = ContextVar("ibf_last_cost_cents", default=0.0)


def _reset_last_cost() -> None:
    """Reset the per-call cost tracker for the current context."""
    _LAST_COST_CENTS.set(0.0)


def _add_last_cost(amount: float) -> None:
    """Accumulate cost (USD cents) into the current context tracker."""
    current = _LAST_COST_CENTS.get()
    _LAST_COST_CENTS.set(current + amount)


def generate_forecast_text(
    prompt: str,
    system_prompt: str,
    settings: LLMSettings,
    *,
    reasoning: Optional[dict] = None,
    thinking_level: Optional[str] = None,
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
        return _call_gemini(prompt, system_prompt, settings, thinking_level=thinking_level)
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
    cost_cents = log_openai_usage_and_cost(settings.model, getattr(response, "usage", None))
    _LAST_COST_CENTS.set(cost_cents)
    message = response.choices[0].message if response.choices else None
    raw_text = _coerce_message_content(getattr(message, "content", None))
    if not raw_text and message is not None:
        try:
            payload = message.model_dump()
        except (AttributeError, TypeError, ValueError):
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


def _call_gemini(
    prompt: str,
    system_prompt: str,
    settings: LLMSettings,
    *,
    thinking_level: Optional[str] = None,
) -> str:
    """Invoke the Google Gemini SDK and return cleaned text."""
    from google import genai
    from google.genai import types

    logging.getLogger("google_genai.models").setLevel(logging.WARNING)

    _reset_last_cost()

    try:
        with force_gemini_api_key(settings.api_key):
            client = genai.Client(api_key=settings.api_key)
            config = _build_gemini_config(types, system_prompt, settings, thinking_level=thinking_level)
            response = _call_gemini_once(client, settings.model, prompt, config, system_prompt, settings)
            _add_last_cost(log_gemini_usage_and_cost(settings.model, getattr(response, "usage_metadata", None)))
    except Exception as exc:
        logger.error("Gemini request failed: %s", exc, exc_info=True)
        raise RuntimeError("Gemini request failed") from exc

    text = (getattr(response, "text", None) or "").strip()
    if text:
        cleaned = _clean_llm_output(text)
        final_text = _maybe_continue_gemini(
            client,
            types,
            settings,
            system_prompt,
            cleaned,
            response,
            thinking_level=thinking_level,
        )
        return final_text

    candidates = getattr(response, "candidates", None)
    if candidates:
        candidate = candidates[0]
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) if content is not None else None
        if parts:
            text_value = getattr(parts[0], "text", None)
            if text_value:
                cleaned = _clean_llm_output(text_value)
                final_text = _maybe_continue_gemini(
                    client,
                    types,
                    settings,
                    system_prompt,
                    cleaned,
                    response,
                    thinking_level=thinking_level,
                )
                return final_text

    raise RuntimeError(
        f"Gemini response was empty or blocked: {getattr(response, 'prompt_feedback', None)}"
    )


def _build_gemini_config(
    types_module,
    system_prompt: str,
    settings: LLMSettings,
    *,
    thinking_level: Optional[str] = None,
):
    """Build a Gemini GenerateContentConfig, disabling AFC when supported."""
    config_kwargs = {
        "temperature": settings.temperature,
        "max_output_tokens": settings.max_tokens,
        "system_instruction": system_prompt,
    }
    if thinking_level:
        thinking_cls = getattr(types_module, "ThinkingConfig", None)
        if thinking_cls:
            try:
                thinking_config = thinking_cls(thinking_level=thinking_level)
                for key in ("thinking_config", "thinkingConfig"):
                    try:
                        types_module.GenerateContentConfig(**{**config_kwargs, key: thinking_config})
                    except TypeError:
                        continue
                    else:
                        config_kwargs[key] = thinking_config
                        break
            except TypeError:
                pass
    afc_cls = getattr(types_module, "AutomaticFunctionCallingConfig", None)
    if afc_cls:
        afc_value = None
        try:
            afc_value = afc_cls(disable=True)
        except TypeError:
            try:
                afc_value = afc_cls(enabled=False)
            except TypeError:
                afc_value = None
        if afc_value is not None:
            for key in ("automatic_function_calling", "automatic_function_calling_config"):
                try:
                    types_module.GenerateContentConfig(**{**config_kwargs, key: afc_value})
                except TypeError:
                    continue
                else:
                    config_kwargs[key] = afc_value
                    break
    return types_module.GenerateContentConfig(**config_kwargs)


def _call_gemini_once(client, model_name: str, prompt: str, config, system_prompt: str, settings: LLMSettings):
    """Single Gemini call with a fallback that inlines the system prompt."""
    try:
        return client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=config,
        )
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning("Gemini request failed with system instruction; retrying (%s)", exc)
        fallback_config = type(config)(
            temperature=settings.temperature,
            max_output_tokens=settings.max_tokens,
        )
        return client.models.generate_content(
            model=model_name,
            contents=f"{system_prompt}\n\n{prompt}",
            config=fallback_config,
        )


def _maybe_continue_gemini(
    client,
    types_module,
    settings: LLMSettings,
    system_prompt: str,
    text: str,
    response,
    *,
    thinking_level: Optional[str] = None,
):
    """Attempt to continue if Gemini hit the max output limit."""
    if not _gemini_finished_by_limit(response):
        return text

    combined = text
    config = _build_gemini_config(
        types_module,
        system_prompt,
        settings,
        thinking_level=thinking_level,
    )
    for _ in range(2):
        continuation_prompt = (
            "You are continuing a response that was cut off.\n"
            "Do NOT repeat any text already provided.\n"
            "Finish the cut-off sentence if needed, then continue with the remaining content.\n\n"
            f"Previous response:\n{combined}\n\n"
            "Continue:"
        )
        next_response = _call_gemini_once(
            client,
            settings.model,
            continuation_prompt,
            config,
            system_prompt,
            settings,
        )
        _add_last_cost(log_gemini_usage_and_cost(settings.model, getattr(next_response, "usage_metadata", None)))
        next_text = (getattr(next_response, "text", None) or "").strip()
        if not next_text:
            break
        combined = (combined.rstrip() + "\n" + _clean_llm_output(next_text).lstrip()).strip()
        if not _gemini_finished_by_limit(next_response):
            break
    return combined


def _gemini_finished_by_limit(response) -> bool:
    """Return True if Gemini indicates output was cut off by token limit."""
    candidates = getattr(response, "candidates", None)
    if not candidates:
        return False
    reason = getattr(candidates[0], "finish_reason", None)
    if reason is None:
        return False
    if isinstance(reason, str):
        reason_name = reason
    else:
        reason_name = getattr(reason, "name", str(reason))
    return reason_name.upper() in {"MAX_TOKENS", "MAX_TOKEN", "LENGTH", "TOKEN_LIMIT"}


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

    # Normalize degree symbol spacing (e.g., "18 °C" -> "18°C").
    text = re.sub(r"(-?\d+(?:\.\d+)?)\s+°\s*([CF])", r"\1°\2", text)

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




def consume_last_cost_cents() -> float:
    """Return and reset the most recent LLM cost (in USD cents)."""
    value = _LAST_COST_CENTS.get()
    _LAST_COST_CENTS.set(0.0)
    return value
