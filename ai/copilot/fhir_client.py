"""Our door into MediTrack: SMART Backend Services (system-to-system) + a few FHIR reads.

Flow (HL7 SMART Backend Services): sign a short-lived JWT with our private key (`private_key_jwt`), POST it to the token endpoint
as `client_credentials`, get a 5-minute bearer token with `system/*.read` scopes, call FHIR with it. MediTrack holds only our PUBLIC key.
"""
import json
import threading
import time
import uuid
from urllib.parse import urlparse

import httpx
import jwt

from .config import settings

SCOPE = "system/*.read system/DocumentReference.write"


class FhirError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"FHIR {status}: {detail}")
        self.status = status


class FhirClient:
    def __init__(self, http: httpx.Client | None = None, base: str | None = None, client_id: str | None = None, key_pem: str | None = None, kid: str | None = None):
        s = settings()
        self.http = http or httpx.Client(timeout=15.0)
        self.base = (base or s.meditrack_base).rstrip("/")
        self.client_id, self.kid = client_id or s.client_id, kid or s.key_kid
        self.key_pem = key_pem or s.key_pem
        self._tok: tuple[str, float] | None = None
        self._aud: str | None = None
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- auth
    def _token_audience(self) -> str:
        """The token endpoint exactly as MediTrack advertises it (that is the JWT audience), which may be its public URL."""
        if self._aud is None:
            r = self.http.get(f"{self.base}/fhir/.well-known/smart-configuration")
            r.raise_for_status()
            self._aud = r.json()["token_endpoint"]
        return self._aud

    def _assertion(self, aud: str) -> str:
        if not self.key_pem:
            raise FhirError(0, "no AI_BACKEND_KEY_PEM / AI_BACKEND_KEY_FILE configured")
        t = int(time.time())
        return jwt.encode({"iss": self.client_id, "sub": self.client_id, "aud": aud, "iat": t, "exp": t + 120, "jti": uuid.uuid4().hex},
                          self.key_pem, algorithm="RS256", headers={"kid": self.kid})

    def token(self) -> str:
        with self._lock:
            if self._tok and self._tok[1] > time.time() + 30:
                return self._tok[0]
            aud = self._token_audience()
            r = self.http.post(f"{self.base}{urlparse(aud).path}", data={
                "grant_type": "client_credentials", "scope": SCOPE,
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer", "client_assertion": self._assertion(aud)})
            if r.status_code != 200:
                raise FhirError(r.status_code, r.text[:200])
            body = r.json()
            self._tok = (body["access_token"], time.time() + body.get("expires_in", 300))
            return self._tok[0]

    # ---------------------------------------------------------------- reads
    def _get(self, path: str, **params) -> httpx.Response:
        for attempt in (1, 2):
            r = self.http.get(f"{self.base}{path}", params=params or None, headers={"Authorization": f"Bearer {self.token()}", "Accept": "application/fhir+json"})
            if r.status_code == 401 and attempt == 1:     # token expired between cache check and use
                self._tok = None
                continue
            if r.status_code >= 400:
                raise FhirError(r.status_code, r.text[:200])
            return r
        raise AssertionError("unreachable")

    def search(self, rtype: str, **params) -> list[dict]:
        out, page = [], 1
        while True:
            b = self._get(f"/fhir/{rtype}", _count=200, _page=page, **params).json()
            out += [e["resource"] for e in b.get("entry", [])]
            if len(b.get("entry", [])) < 200:
                return out
            page += 1

    def read(self, rtype: str, rid: str) -> dict:
        return self._get(f"/fhir/{rtype}/{rid}").json()

    def everything(self, patient_id: str) -> list[dict]:
        b = self._get(f"/fhir/Patient/{patient_id}/$everything").json()
        return [e["resource"] for e in b.get("entry", [])]

    def binary(self, url_or_id: str) -> tuple[bytes, str]:
        """Fetch an attachment. The URL in the resource is MediTrack's public address; we always go through our own route."""
        rid = url_or_id.rsplit("/Binary/", 1)[-1]
        r = self._get(f"/fhir/Binary/{rid}")
        return r.content, r.headers.get("content-type", "")

    def post_document_pdf(self, patient_id: str, encounter_id: str, title: str, pdf: bytes, doc_type: str = "PRIOR_AUTH") -> dict:
        body = {"resourceType": "DocumentReference", "status": "current", "type": {"text": doc_type},
                "subject": {"reference": f"Patient/{patient_id}"}, "context": {"encounter": [{"reference": f"Encounter/{encounter_id}"}]},
                "content": [{"attachment": {"contentType": "application/pdf", "title": title, "data": __import__("base64").b64encode(pdf).decode()}}]}
        r = self.http.post(f"{self.base}/fhir/DocumentReference", content=json.dumps(body),
                           headers={"Authorization": f"Bearer {self.token()}", "Content-Type": "application/fhir+json"})
        if r.status_code >= 400:
            raise FhirError(r.status_code, r.text[:200])
        return r.json()
