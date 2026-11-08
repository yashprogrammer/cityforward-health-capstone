"""Keyless Foundry setup should pass a renewable token provider to the client."""
import sys
from types import ModuleType

from copilot import llm


def test_foundry_managed_identity(monkeypatch):
    identity = ModuleType('azure.identity')
    identity.ManagedIdentityCredential = lambda: 'credential'
    identity.get_bearer_token_provider = lambda credential, scope: (credential, scope)
    azure = ModuleType('azure')
    azure.identity = identity
    monkeypatch.setitem(sys.modules, 'azure', azure)
    monkeypatch.setitem(sys.modules, 'azure.identity', identity)
    monkeypatch.setenv('LLM_BASE_URL', 'https://example.invalid/openai/v1/')
    monkeypatch.setenv('FOUNDRY_AUTH', 'managed_identity')
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    monkeypatch.setattr(llm, 'OpenAICompatible', lambda *args: args)
    result = llm._get_backend('foundry')
    assert result[2] == ('credential', 'https://ai.azure.com/.default')


def test_reasoning_cap_exhausted_is_explained(monkeypatch):
    """A reasoning model can spend the whole output cap thinking and return no text: say so instead of 'no JSON object'."""
    import pytest
    from types import SimpleNamespace
    backend = llm.OpenAICompatible.__new__(llm.OpenAICompatible)
    backend.max_output_tokens = 2000
    backend._chat = SimpleNamespace(invoke=lambda msgs: SimpleNamespace(content="", response_metadata={"finish_reason": "length"}, usage_metadata={}))
    with pytest.raises(llm.LLMError, match="LLM_MAX_OUTPUT_TOKENS"):
        backend.chat("s", "u")
    backend._chat = SimpleNamespace(invoke=lambda msgs: SimpleNamespace(content='{"a": 1}', response_metadata={"finish_reason": "stop"}, usage_metadata={"input_tokens": 3, "output_tokens": 4}))
    assert backend.chat("s", "u").tokens_out == 4
