"""Optional Groq integration. Everything here degrades gracefully when no key is set."""
from __future__ import annotations

import os

import requests

from backend.logger import get_logger

log = get_logger("webpilot.ai")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def groq_configured() -> bool:
    return bool(os.environ.get("GROQ_API_KEY", "").strip())


def groq_model() -> str:
    return os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")


def groq_chat(messages: list[dict], json_mode: bool = False, max_tokens: int = 500) -> str:
    """Return the assistant message text. Raises RuntimeError on any failure."""
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GROQ_API_KEY not configured")
    model = groq_model()
    # Reasoning models (e.g. openai/gpt-oss-*) spend part of the token budget on hidden
    # reasoning, so give them headroom and ask for low reasoning effort.
    payload = {"model": model, "messages": messages, "temperature": 0,
               "max_tokens": max(max_tokens, 1500)}
    if model.startswith("openai/gpt-oss"):
        payload["reasoning_effort"] = "low"
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def post(body):
        try:
            return requests.post(GROQ_URL, json=body, timeout=30, headers=headers)
        except requests.RequestException as exc:
            raise RuntimeError(f"Groq request failed: {type(exc).__name__}")

    r = post(payload)
    if r.status_code == 400 and json_mode:
        # Some models reject or mis-handle JSON mode (e.g. tool_use_failed). Retry once as
        # plain text with an explicit instruction; the caller validates the JSON anyway.
        retry = dict(payload)
        retry.pop("response_format", None)
        retry["messages"] = list(messages) + [{"role": "system", "content":
            "Respond with a single JSON object only. No markdown, no explanations, no tool calls."}]
        r = post(retry)
    if r.status_code != 200:
        detail = ""
        try:  # Groq's own error text (never contains our key)
            detail = str(r.json().get("error", {}).get("message", ""))[:160]
        except ValueError:
            pass
        raise RuntimeError(f"Groq returned HTTP {r.status_code}" + (f" ({detail})" if detail else ""))
    try:
        content = r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError, TypeError):
        raise RuntimeError("Unexpected Groq response format")
    if not content or not content.strip():
        raise RuntimeError("Groq returned an empty message (model may have used its whole token budget)")
    return content
