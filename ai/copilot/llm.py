"""Model backends. A backend turns (system, user) into text plus token counts; everything else (JSON parsing, redaction, metering,
audit) happens in the gateway, so swapping Groq for Azure AI Foundry, or for a model inside the hospital's VPC, changes one line of config.

  LLM_PROVIDER=groq      OpenAI-compatible, default model openai/gpt-oss-20b (GROQ_API_KEY)
  LLM_PROVIDER=foundry   any OpenAI-compatible endpoint, e.g. Azure AI Foundry or vLLM in the VPC (LLM_BASE_URL, LLM_API_KEY, LLM_MODEL)
  LLM_PROVIDER=offline   no model: callers use their deterministic fallbacks
"""
import json
import os
import re
from dataclasses import dataclass
from typing import Protocol

# USD per 1M tokens (input, output). Illustrative list prices for budgeting exercises: check the provider's current price sheet.
PRICES = {"openai/gpt-oss-20b": (0.075, 0.30), "gpt-oss-20b": (0.075, 0.30), "openai/gpt-oss-120b": (0.15, 0.60), "default": (0.50, 1.50)}


class LLMError(RuntimeError):
    pass


@dataclass
class Reply:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0


class Backend(Protocol):
    name: str

    def chat(self, system: str, user: str) -> Reply: ...


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    pin, pout = PRICES.get(model.split(":", 1)[-1], PRICES["default"])
    return (tokens_in * pin + tokens_out * pout) / 1_000_000


def extract_json(text: str) -> dict:
    """Models wrap JSON in prose or ``` fences. Take the first balanced {...} object."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start = text.find("{")
    if start < 0:
        raise LLMError("no JSON object in the model reply")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            esc = (ch == "\\") and not esc
            if ch == '"' and not esc:
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError as e:
                    raise LLMError(f"malformed JSON from the model: {e}") from e
    raise LLMError("unterminated JSON object in the model reply")


class OpenAICompatible:
    def __init__(self, name: str, base_url: str, api_key: str, model: str, timeout: float = 60):
        from langchain_openai import ChatOpenAI   # lazy: offline mode and tests need no model client
        self.name = f"{name}:{model}"
        self.model = model
        self.base_url, self.api_key = base_url, api_key
        self._chat = ChatOpenAI(base_url=base_url, api_key=api_key, model=model, temperature=0, timeout=timeout, max_retries=1)

    def chat(self, system: str, user: str) -> Reply:
        try:
            r = self._chat.invoke([("system", system), ("user", user)])
        except Exception as e:   # noqa: BLE001 - network, auth, rate limit: the caller decides what to do
            raise LLMError(f"{type(e).__name__}: {e}") from e
        u = getattr(r, "usage_metadata", None) or {}
        return Reply(r.content if isinstance(r.content, str) else str(r.content), u.get("input_tokens", 0), u.get("output_tokens", 0))


class RoutedBackend:
    """Different models for different jobs: a cheap fast model for coding suggestions, a stronger one for summaries.
    Configure with LLM_MODEL_<PURPOSE>=model (e.g. LLM_MODEL_CODING=openai/gpt-oss-20b); the gateway passes the purpose of each call."""

    def __init__(self, default: Backend, overrides: dict[str, Backend]):
        self.default, self.overrides = default, overrides
        self.name = default.name

    def for_purpose(self, purpose: str) -> Backend:
        return self.overrides.get(purpose, self.default)


def get_backend(provider: str | None = None) -> Backend | None:
    """None means offline. Per-purpose model overrides (LLM_MODEL_SUMMARY, LLM_MODEL_CODING, ...) wrap the default in a RoutedBackend."""
    base = _get_backend(provider)
    overrides = {k[len("LLM_MODEL_"):].lower().replace("_", "-"): v for k, v in os.environ.items() if k.startswith("LLM_MODEL_") and v}
    if base is None or not overrides or not isinstance(base, OpenAICompatible):
        return base
    first = base.name.split(":", 1)[0]
    return RoutedBackend(base, {purpose: OpenAICompatible(first, base.base_url, base.api_key, model) for purpose, model in overrides.items()})


def _get_backend(provider: str | None = None) -> Backend | None:
    provider = (provider or os.getenv("LLM_PROVIDER") or ("groq" if os.getenv("GROQ_API_KEY") else "offline")).lower()
    if provider == "groq":
        key = os.getenv("GROQ_API_KEY")
        if not key:
            raise LLMError("GROQ_API_KEY is not set (or use LLM_PROVIDER=offline)")
        return OpenAICompatible("groq", os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1"), key, os.getenv("LLM_MODEL", "openai/gpt-oss-20b"))
    if provider == "foundry":
        base, key = os.getenv("LLM_BASE_URL"), os.getenv("LLM_API_KEY")
        if not (base and key):
            raise LLMError("foundry needs LLM_BASE_URL and LLM_API_KEY")
        return OpenAICompatible("foundry", base, key, os.getenv("LLM_MODEL", "gpt-oss-20b"))
    if provider == "offline":
        return None
    raise LLMError(f"unknown LLM_PROVIDER {provider!r}")
