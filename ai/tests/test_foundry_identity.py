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
