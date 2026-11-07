import pytest

from meditrack.backend.auth_config import validate_auth_config


def test_demo_must_be_explicitly_local():
    assert validate_auth_config({"APP_ENV": "local", "AUTH_MODE": "demo"}).mode == "demo"
    with pytest.raises(RuntimeError, match="only permitted"):
        validate_auth_config({"APP_ENV": "deployment", "AUTH_MODE": "demo"})
    with pytest.raises(RuntimeError, match="only permitted"):
        validate_auth_config({"AUTH_MODE": "demo"})


def test_deployment_and_oidc_fail_closed_until_configured():
    with pytest.raises(RuntimeError, match="not enabled"):
        validate_auth_config({})
    assert validate_auth_config({"APP_ENV": "local", "AUTH_MODE": "local"}).mode == "local"
    with pytest.raises(RuntimeError, match="FHIR authentication"):
        validate_auth_config({"APP_ENV": "local", "AUTH_MODE": "local", "FHIR_AUTH": "off"})
    with pytest.raises(RuntimeError, match="only permitted"):
        validate_auth_config({"APP_ENV": "deployment", "AUTH_MODE": "local"})
    with pytest.raises(RuntimeError, match="FHIR authentication"):
        validate_auth_config({"APP_ENV": "deployment", "AUTH_MODE": "oidc", "FHIR_AUTH": "off"})
    with pytest.raises(RuntimeError, match="APP_ENV"):
        validate_auth_config({"APP_ENV": "production", "AUTH_MODE": "demo"})


def test_oidc_requires_explicit_configuration_and_https():
    env = {'APP_ENV': 'deployment', 'AUTH_MODE': 'oidc', 'ENABLE_ENTRA_OIDC': '1',
           'ENTRA_TENANT_ID': '11111111-1111-1111-1111-111111111111',
           'ENTRA_CLIENT_ID': '22222222-2222-2222-2222-222222222222',
           'ENTRA_MANAGED_IDENTITY_CLIENT_ID': '33333333-3333-3333-3333-333333333333',
           'PUBLIC_BASE_URL': 'https://ehr.example'}
    assert validate_auth_config(env).mode == 'oidc'
    for changes in ({'PUBLIC_BASE_URL': 'http://ehr.example'}, {'ENTRA_CLIENT_ID': 'bad'}, {'ENABLE_ENTRA_OIDC': '0'}):
        with pytest.raises(RuntimeError):
            validate_auth_config({**env, **changes})
