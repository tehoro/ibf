"""
Wrappers around OpenAI-compatible APIs and Google Gemini.
"""

from __future__ import annotations

import logging
import re
import json
from typing import Any, Optional

import google.generativeai as genai
from openai import OpenAI

from .settings import LLMSettings

logger = logging.getLogger(__name__)


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
    logger.info(
        "LLM usage – model=%s prompt_tokens=%s completion_tokens=%s",
        settings.model,
        getattr(response.usage, "prompt_tokens", None),
        getattr(response.usage, "completion_tokens", None),
    )
    return cleaned


def _call_gemini(prompt: str, system_prompt: str, settings: LLMSettings) -> str:
    """Invoke the Google Gemini SDK and return cleaned text."""
    genai.configure(api_key=settings.api_key)
    model = genai.GenerativeModel(
        model_name=settings.model,
        generation_config={
            "temperature": settings.temperature,
            "max_output_tokens": settings.max_tokens,
        },
        system_instruction=system_prompt,
    )
    response = model.generate_content(prompt, stream=False)
    if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
        text = response.candidates[0].content.parts[0].text
    else:
        raise RuntimeError(f"Gemini response was empty or blocked: {getattr(response.prompt_feedback, 'block_reason', 'unknown')}")
    return _clean_llm_output(text or "")


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

