"""The legacy hospital has integration doors but no AI vendor by default."""
from meditrack.backend.seed import register_integrations


class RecordingConnection:
    def __init__(self):
        self.statements = []

    def execute(self, sql, parameters):
        self.statements.append((sql, parameters))


def test_no_ai_integration_is_registered_without_configuration(monkeypatch):
    for name in ('AI_LAUNCH_URL', 'AI_BACKEND_JWKS', 'AI_BACKEND_JWKS_FILE', 'AI_WEBHOOK_URL'):
        monkeypatch.delenv(name, raising=False)
    conn = RecordingConnection()
    register_integrations(conn)
    assert not conn.statements


def test_configured_vendor_gets_smart_client_and_link(monkeypatch):
    for name in ('AI_BACKEND_JWKS', 'AI_BACKEND_JWKS_FILE', 'AI_WEBHOOK_URL'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('AI_LAUNCH_URL', 'http://localhost:8001/ai/launch')
    conn = RecordingConnection()
    register_integrations(conn)
    assert len(conn.statements) == 2
    assert 'SMT_CLIENT' in conn.statements[0][0]
    assert 'SYS_EXT_LNK' in conn.statements[1][0]
