"""Thin wrapper around the Google Gen AI SDK (Gemini) using an API key."""

from __future__ import annotations

import logging
import os
import time
from typing import TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.8-flash"
RETRYABLE = {429, 500, 502, 503, 504}

T = TypeVar("T", bound=BaseModel)


class GeminiNotConfigured(RuntimeError):
    pass


def gemini_available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def model_name() -> str:
    return os.getenv("GEMINI_MODEL") or DEFAULT_MODEL


def make_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise GeminiNotConfigured("GEMINI_API_KEY is not set. Add it to your .env file.")
    return genai.Client(api_key=api_key)


class Gemini:
    def __init__(self, model: str | None = None, client: genai.Client | None = None):
        self.client = client or make_client()
        self.model = model or model_name()

    def _generate(self, contents: str, config: types.GenerateContentConfig, retries: int = 4):
        for attempt in range(retries):
            try:
                return self.client.models.generate_content(model=self.model, contents=contents, config=config)
            except errors.APIError as e:
                if e.code not in RETRYABLE or attempt == retries - 1:
                    raise
                wait = 2 ** (attempt + 1)
                log.warning("Gemini error %s, retrying in %ss", e.code, wait)
                time.sleep(wait)

    def generate_json(self, prompt: str, schema: type[T], system: str | None = None, temperature: float = 0.2) -> T:
        """Generate a response validated against a Pydantic model."""
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=schema,
        )
        resp = self._generate(prompt, config)
        if isinstance(resp.parsed, schema):
            return resp.parsed
        return schema.model_validate_json(resp.text)

    def generate_text(self, prompt: str, system: str | None = None, temperature: float = 0.4) -> str:
        config = types.GenerateContentConfig(system_instruction=system, temperature=temperature)
        return self._generate(prompt, config).text or ""
