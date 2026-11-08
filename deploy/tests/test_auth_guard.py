import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / 'deploy/helm/cityforward'
HELM = shutil.which('helm')
pytestmark = pytest.mark.skipif(not HELM, reason='helm is not installed')


def render(*args):
    return subprocess.run([HELM, 'template', 'cityforward', str(CHART), *args], capture_output=True, text=True)


def test_local_profile_explicitly_enables_local_login():
    result = render('-f', str(CHART / 'values-kind.yaml'))
    assert result.returncode == 0
    assert 'name: APP_ENV, value: "local"' in result.stdout
    assert 'name: AUTH_MODE, value: "local"' in result.stdout


def test_azure_profile_rejects_demo_even_if_layered_over_local():
    result = render('-f', str(CHART / 'values-kind.yaml'), '-f', str(CHART / 'values-aks.yaml'), '--set', 'meditrack.auth.mode=demo')
    assert result.returncode != 0
    assert 'Demo authentication is only allowed' in result.stderr


def test_azure_profile_rejects_local_password_login():
    result = render('-f', str(CHART / 'values-aks.yaml'), '--set', 'meditrack.auth.mode=local')
    assert result.returncode != 0
    assert 'Local password login is only allowed' in result.stderr
