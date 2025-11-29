"""
Wrappers around OpenAI-compatible APIs and Google Gemini.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import google.generativeai as genai
from openai import OpenAI

from .settings import LLMSettings

logger = logging.getLogger(__name__)


def generate_forecast_text(
    prompt: str,
    system_prompt: str,
    settings: LLMSettings,
) -> str:
    """
    Execute the LLM request and return the cleaned forecast text.
    """
    if settings.is_google:
        return _call_gemini(prompt, system_prompt, settings)
    return _call_openai_compatible(prompt, system_prompt, settings)


def _call_openai_compatible(prompt: str, system_prompt: str, settings: LLMSettings) -> str:
    client = OpenAI(api_key=settings.api_key, base_url=settings.base_url)
    response = client.chat.completions.create(
        model=settings.model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
        stream=False,
    )
    content = response.choices[0].message.content if response.choices else ""
    cleaned = _clean_llm_output(content or "")
    logger.info(
        "LLM usage – model=%s prompt_tokens=%s completion_tokens=%s",
        settings.model,
        getattr(response.usage, "prompt_tokens", None),
        getattr(response.usage, "completion_tokens", None),
    )
    return cleaned


def _call_gemini(prompt: str, system_prompt: str, settings: LLMSettings) -> str:
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

