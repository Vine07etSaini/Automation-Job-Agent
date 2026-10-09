"""Read-only checks for other LLM providers: Groq (hosted, API key) and Ollama (local models)."""

from __future__ import annotations

import os

import httpx
from pydantic import BaseModel

GROQ_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
TIMEOUT = 8.0


class OllamaModel(BaseModel):
    name: str
    size_gb: float


def groq_available() -> bool:
    return bool(os.getenv("GROQ_API_KEY"))


def groq_model() -> str:
    return os.getenv("GROQ_MODEL") or DEFAULT_GROQ_MODEL


def ollama_host() -> str:
    return (os.getenv("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST).rstrip("/")


def ollama_model() -> str:
    return os.getenv("OLLAMA_MODEL", "")


def groq_models(client: httpx.Client | None = None) -> list[str]:
    """Model ids your Groq key can use. A bad key raises httpx.HTTPStatusError (401)."""
    if not groq_available():
        raise RuntimeError("GROQ_API_KEY is not set. Get a key at https://console.groq.com/keys and add it to .env.")
    client = client or httpx.Client(timeout=TIMEOUT)
    r = client.get(f"{GROQ_URL}/models", headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"})
    r.raise_for_status()
    return sorted(m["id"] for m in r.json().get("data", []))


def ollama_models(client: httpx.Client | None = None) -> list[OllamaModel]:
    """Models already downloaded on this machine. Raises httpx.ConnectError when Ollama is not running."""
    client = client or httpx.Client(timeout=TIMEOUT)
    r = client.get(f"{ollama_host()}/api/tags")
    r.raise_for_status()
    return [OllamaModel(name=m["name"], size_gb=round(m.get("size", 0) / 1e9, 1)) for m in r.json().get("models", [])]
