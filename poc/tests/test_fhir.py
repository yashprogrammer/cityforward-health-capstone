import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from summarizer.fhir import FhirClient, FhirError
from summarizer.records import load_bundle


@pytest.fixture
def client(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path = tmp_path / 'test-key.pem'
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    bundle = {'resourceType': 'Bundle', 'entry': [
        {'resource': {'resourceType': 'Patient', 'id': '1', 'name': [{'given': ['Test'], 'family': 'Patient'}], 'birthDate': '1970-01-01'}},
        {'resource': {'resourceType': 'Encounter', 'id': 'stay', 'class': {'code': 'IMP'}, 'period': {'start': '2026-01-01'}}},
        {'resource': {'resourceType': 'Observation', 'id': 'obs', 'category': [{'coding': [{'code': 'laboratory'}]}], 'code': {'text': 'Test result'}, 'effectiveDateTime': '2026-01-01'}},
        {'resource': {'resourceType': 'DocumentReference', 'id': 'note', 'date': '2026-01-01', 'content': [{'attachment': {'contentType': 'text/plain', 'url': 'https://untrusted.example/fhir/Binary/note-1'}}]}},
    ]}
    def handler(request):
        if request.url.path == '/smart/token':
            from urllib.parse import parse_qs
            data = parse_qs(request.content.decode())
            assert data['scope'] == ['system/*.read']
            jwt.decode(data['client_assertion'][0], key.public_key(), algorithms=['RS256'], audience='https://ehr.example/smart/token')
            return httpx.Response(200, json={'access_token': 'test-token', 'expires_in': 300})
        assert request.headers['authorization'] == 'Bearer test-token'
        if request.url.path == '/fhir/Patient':
            return httpx.Response(200, json={'entry': [bundle['entry'][0]]})
        if request.url.path == '/fhir/Patient/1/$everything':
            return httpx.Response(200, json=bundle)
        if request.url.path == '/fhir/Binary/note-1':
            assert request.url.host == 'ehr-internal'
            return httpx.Response(200, content=b'Synthetic clinical note')
        return httpx.Response(404)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return FhirClient('http://ehr-internal', 'https://ehr.example', 'poc-reader', str(key_path), http=http)


def test_loads_live_ehr_patient_and_linked_notes(client):
    assert client.patients()[0]['id'] == '1'
    chart = load_bundle(client.chart('1'))
    assert chart.patient_id == '1'
    assert chart.observations[0].name == 'Test result'
    assert chart.notes[0].text == 'Synthetic clinical note'


def test_refuses_invalid_patient_id(client):
    with pytest.raises(FhirError, match='Invalid patient identifier'):
        client.chart('../other')
