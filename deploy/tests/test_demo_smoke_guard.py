import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / 'azure-demo' / 'alert_drill.py'
SPEC = importlib.util.spec_from_file_location('cityforward_alert_drill', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize('url', ['https://example.org', 'http://192.0.2.1', 'http://localhost.example.org'])
def test_cookie_impersonation_rejects_non_loopback_before_network(url, monkeypatch):
    monkeypatch.setattr(MODULE, 'http_get', lambda *a, **k: pytest.fail('must not contact the endpoint'))
    with pytest.raises(SystemExit, match='loopback'):
        MODULE.smart_token(url, 'it.admin')


def test_loopback_alone_does_not_enable_demo_impersonation(monkeypatch):
    monkeypatch.setattr(MODULE, 'http_get', lambda *a, **k: (200, {}, b'{"demo_user_switcher": false}'))
    with pytest.raises(SystemExit, match='explicitly enabled'):
        MODULE.smart_token('http://127.0.0.1:8081', 'it.admin')
