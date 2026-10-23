"""One tiny seam between the graph and whichever model answers.

Providers (env `LLM_PROVIDER`):
  groq     Groq's OpenAI-compatible endpoint, default model openai/gpt-oss-20b (needs GROQ_API_KEY)
  foundry  any OpenAI-compatible endpoint, e.g. Azure AI Foundry (LLM_BASE_URL, LLM_API_KEY, LLM_MODEL)
  offline  no model at all: the graph falls back to the deterministic template summary

The graph only ever calls `complete_json(system, user)`; that is the whole contract, so tests plug in a scripted fake.
"""
import json
import os
import re
from typing import Protocol


class LLM(Protocol):
    name: str

    def complete_json(self, system: str, user: str) -> dict: ...


class LLMError(RuntimeError):
    pass


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
                    raise LLMError(f"model returned malformed JSON: {e}") from e
    raise LLMError("unterminated JSON object in the model reply")


class OpenAICompatible:
    def __init__(self, name: str, base_url: str, api_key: str, model: str, timeout: float = 60):
        from langchain_openai import ChatOpenAI   # imported lazily: offline mode needs no model client
        self.name = f"{name}:{model}"
        self._chat = ChatOpenAI(base_url=base_url, api_key=api_key, model=model, temperature=0, timeout=timeout, max_retries=1)

    def complete_json(self, system: str, user: str) -> dict:
        try:
            reply = self._chat.invoke([("system", system), ("user", user)])
        except Exception as e:   # noqa: BLE001 - network, auth, rate limit: the graph decides what to do
            raise LLMError(f"{type(e).__name__}: {e}") from e
        return extract_json(reply.content if isinstance(reply.content, str) else str(reply.content))


def get_llm(provider: str | None = None) -> LLM | None:
    """None means offline."""
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
