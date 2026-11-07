"""Read a synthetic patient chart from MediTrack using a read-only SMART client.

The private key stays on the POC server. MediTrack stores only its public key.
This prototype uses a system account; it is not a staff-authorized SMART launch.
"""
import base64
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import jwt


class FhirError(RuntimeError):
    """A safe-to-display failure without tokens or chart contents."""


class FhirClient:
    def __init__(self, base: str, public_base: str, client_id: str, key_file: str, *, http=None):
        self.base = base.rstrip('/')
        self.public_base = public_base.rstrip('/')
        self.client_id = client_id
        self.key_file = Path(key_file)
        self.http = http or httpx.Client(timeout=20, follow_redirects=False, trust_env=False)
        self._token = None
        self._expires = 0.0

    def token(self):
        if self._token and self._expires > time.time() + 30:
            return self._token
        now = int(time.time())
        try:
            key = self.key_file.read_text()
            assertion = jwt.encode({'iss': self.client_id, 'sub': self.client_id,
                                    'aud': self.public_base + '/smart/token', 'iat': now,
                                    'exp': now + 120, 'jti': uuid.uuid4().hex},
                                   key, algorithm='RS256', headers={'kid': 'poc-reader'})
            r = self.http.post(self.base + '/smart/token', data={
                'grant_type': 'client_credentials', 'scope': 'system/*.read',
                'client_assertion_type': 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
                'client_assertion': assertion,
            })
            r.raise_for_status()
            result = r.json()
            self._token = result['access_token']
            self._expires = time.time() + min(int(result.get('expires_in', 300)), 300)
            return self._token
        except (OSError, httpx.HTTPError, ValueError, KeyError, jwt.PyJWTError) as exc:
            raise FhirError('Could not authenticate the POC with the EHR. Check the service setup.') from exc

    def _get(self, path, **params):
        for attempt in range(2):
            try:
                r = self.http.get(self.base + path, params=params,
                                  headers={'Authorization': 'Bearer ' + self.token(), 'Accept': 'application/fhir+json'})
                if r.status_code == 401 and attempt == 0:
                    self._token = None
                    continue
                r.raise_for_status()
                return r
            except httpx.HTTPError as exc:
                raise FhirError('Could not read the requested data from the EHR.') from exc
        raise FhirError('The EHR rejected the POC service credentials.')

    def patients(self):
        """Return the hospital's synthetic patients for the POC selector."""
        bundle = self._get('/fhir/Patient', _count=200).json()
        return [entry['resource'] for entry in bundle.get('entry', [])]

    def chart(self, patient_id):
        if not re.fullmatch(r'[A-Za-z0-9.-]{1,64}', str(patient_id)):
            raise FhirError('Invalid patient identifier.')
        bundle = self._get(f'/fhir/Patient/{patient_id}/$everything').json()
        for entry in bundle.get('entry', []):
            resource = entry.get('resource', {})
            if resource.get('resourceType') != 'DocumentReference':
                continue
            for part in resource.get('content', []):
                attachment = part.get('attachment', {})
                if not attachment.get('contentType', '').startswith('text/') or attachment.get('data') or not attachment.get('url'):
                    continue
                # Never fetch a URL supplied by the record. Only request a known
                # Binary path on our configured EHR server.
                path = urlsplit(attachment['url']).path
                if not re.fullmatch(r'/fhir/Binary/[A-Za-z0-9.-]{1,128}', path):
                    raise FhirError('The EHR returned an unsupported note reference.')
                note = self._get(path).content
                if len(note) > 256_000:
                    raise FhirError('An EHR note is too large for this POC.')
                attachment['data'] = base64.b64encode(note).decode('ascii')
        return bundle
