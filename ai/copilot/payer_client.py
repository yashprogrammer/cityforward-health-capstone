"""The insurer's prior-authorization API (Da Vinci PAS-shaped). Everything about talking to the payer lives here."""
import httpx

from .config import settings

FHIR_JSON = "application/fhir+json"
REVIEW_ACTION_URL = "extension-reviewAction"


class PayerError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"payer {status}: {detail}")
        self.status = status


class PayerClient:
    def __init__(self, http: httpx.Client | None = None, base: str | None = None, api_key: str | None = None):
        s = settings()
        self.http = http or httpx.Client(timeout=20.0)
        self.base = (base or s.payer_base).rstrip("/")
        self.api_key = api_key or s.payer_api_key

    def _check(self, r: httpx.Response) -> dict:
        if r.status_code >= 400:
            try:
                detail = r.json()["issue"][0]["diagnostics"]
            except Exception:   # noqa: BLE001
                detail = r.text[:200]
            raise PayerError(r.status_code, detail)
        return r.json()

    def rule(self, code: str, plan: str | None) -> dict:
        """The policy for a service, and whether this plan requires prior authorization for it."""
        return self._check(self.http.get(f"{self.base}/rules/{code}", params={"plan": plan} if plan else None))

    def submit(self, bundle: dict) -> dict:
        r = self._check(self.http.post(f"{self.base}/fhir/Claim/$submit", json=bundle, headers={"X-Api-Key": self.api_key, "Content-Type": FHIR_JSON}))
        return next(e["resource"] for e in r["entry"] if e["resource"]["resourceType"] == "ClaimResponse")

    def poll(self, claim_response_id: str) -> dict:
        return self._check(self.http.get(f"{self.base}/fhir/ClaimResponse/{claim_response_id}"))


def review_action(cr: dict) -> str:
    """X12 278 review action code of a ClaimResponse: A1 certified, A3 not certified, A4 pended, NA not required."""
    for ext in cr["item"][0].get("extension", []):
        if ext["url"].endswith(REVIEW_ACTION_URL):
            return ext["valueCodeableConcept"]["coding"][0]["code"]
    return "?"
