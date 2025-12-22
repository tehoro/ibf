"""
Settings/secret loading helpers.
"""

from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache
from typing import Optional

from pydantic import BaseModel, Field
from dotenv import load_dotenv


def _load_dotenv() -> None:
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        load_dotenv(dotenv_path=cwd_env, override=True)


_load_dotenv()


class Secrets(BaseModel):
    """
    Container for API keys loaded from environment variables.

    Attributes:
        openweathermap_api_key: Key for OpenWeatherMap.
        google_api_key: Key for Google Maps/Geocoding.
        openai_api_key: Key for OpenAI.
        gemini_api_key: Key for the Gemini API (direct Google).
    """
    openweathermap_api_key: Optional[str] = Field(default=None, alias="OPENWEATHERMAP_API_KEY")
    google_api_key: Optional[str] = Field(default=None, alias="GOOGLE_API_KEY")
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    gemini_api_key: Optional[str] = Field(default=None, alias="GEMINI_API_KEY")

    model_config = {
        "populate_by_name": True,
    }


@lru_cache(maxsize=1)
def get_secrets() -> Secrets:
    """
    Load secrets from environment/.env exactly once.

    Returns:
        A Secrets object populated from environment variables.
    """
    values = {field.alias: os.getenv(field.alias) for field in Secrets.model_fields.values()}
    return Secrets(**values)
