"""
Settings/secret loading helpers.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()


class Secrets(BaseModel):
    openweathermap_api_key: Optional[str] = Field(default=None, alias="OPENWEATHERMAP_API_KEY")
    google_api_key: Optional[str] = Field(default=None, alias="GOOGLE_API_KEY")
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")

    model_config = {
        "populate_by_name": True,
    }


@lru_cache(maxsize=1)
def get_secrets() -> Secrets:
    """
    Load secrets from environment/.env exactly once.
    """
    values = {field.alias: os.getenv(field.alias) for field in Secrets.model_fields.values()}
    return Secrets(**values)

