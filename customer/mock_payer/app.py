"""Mock payer (fictional "Summit Health Plan" family): a prior-authorization API shaped like Da Vinci PAS.

  GET  /rules                      the payer's policy catalogue (what each service needs)
  GET  /rules/{code}?plan=NAME     one service, and whether prior auth is required for that plan
  POST /fhir/Claim/$submit         submit a PAS-style Bundle (Claim use=preauthorization + supportingInfo) -> ClaimResponse
  GET  /fhir/ClaimResponse/{id}    poll a pended / queued request
  GET  /fhir/ClaimResponse?request={claim identifier}

Outcomes (X12 278 review actions): A1 certified in total, A3 not certified, A4 pended, NA no prior authorization required.
Resubmitting a Claim with the same identifier re-adjudicates it (that is how "more information" is supplied).
"""
import hashlib
import os
import time
from datetime import date, datetime, timedelta, timezone

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .adjudicate import evaluate, parse_date
from .rules import RULES, pa_required

API_KEY = os.getenv("PAYER_API_KEY", "demo-payer-key")
REVIEW_SECONDS = float(os.getenv("PAYER_REVIEW_SECONDS", "3"))
FHIR_JSON = "application/fhir+json"
REVIEW_ACTION = "http://hl7.org/fhir/us/davinci-pas/StructureDefinition/extension-reviewAction"
X12 = "https://codesystem.x12.org/005010/306"

app = FastAPI(title="Mock payer (prior authorization)")
CASES: dict[str, dict] = {}


def outcome(status: int, text: str, code: str = "invalid") -> JSONResponse:
    return JSONResponse({"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": code, "diagnostics": text}]},
                        status_code=status, media_type=FHIR_JSON)


@app.get("/rules")
def rules_catalogue():
    return {"service": [{"code": k, "name": v["name"], "manualReview": v["manual_review"], "requirements": v["requirements"]} for k, v in RULES.items()]}


@app.get("/rules/{code}")
def rule(code: str, plan: str | None = None):
    r = RULES.get(code)
    if not r:
        raise HTTPException(404, f"no policy for service {code}")
    return {"code": code, "name": r["name"], "manualReview": r["manual_review"], "priorAuthRequired": pa_required(code, plan) if plan else None,
            "requirements": r["requirements"]}


def _entries(bundle: dict, rtype: str) -> list[dict]:
    return [e["resource"] for e in bundle.get("entry", []) if e.get("resource", {}).get("resourceType") == rtype]


def _supporting(claim: dict) -> dict[str, dict]:
    out = {}
    for si in claim.get("supportingInfo", []):
        cid = ((si.get("category") or {}).get("coding") or [{}])[0].get("code")
        if not cid:
            continue
        out[cid] = {"value": (si.get("valueQuantity") or {}).get("value"), "date": parse_date(si.get("timingDate") or (si.get("timingPeriod") or {}).get("start")),
                    "text": si.get("valueString"), "attachment": bool(si.get("valueAttachment")), "boolean": si.get("valueBoolean")}
    return out


def _claim_response(case: dict) -> dict:
    ev, rule_ = case["eval"], RULES[case["code"]]
    state = _state(case)
    notes = []
    if state in ("pended-info",):
        notes = [{"number": i + 1, "type": "display", "text": f"Missing: {text} [{rid}]"} for i, (rid, text) in enumerate(ev["missing"])]
    elif state == "denied":
        notes = [{"number": i + 1, "type": "display", "text": d} for i, d in enumerate(ev["denials"])] + \
                [{"number": len(ev["denials"]) + 1, "type": "display", "text": "You may appeal within 60 days with a peer-to-peer request."}]
    elif state == "queued":
        notes = [{"number": 1, "type": "display", "text": "Complete request received. Clinical review in progress; poll for the decision."}]
    action, disp, outc = {"approved": ("A1", "Certified in total", "complete"), "denied": ("A3", "Not certified", "complete"),
                          "pended-info": ("A4", "Pended: additional information required", "queued"),
                          "queued": ("A4", "Pended: clinical review in progress", "queued"),
                          "not-required": ("NA", "Prior authorization is not required for this service under this plan", "complete")}[state]
    cr = {"resourceType": "ClaimResponse", "id": case["id"], "status": "active",
          "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/claim-type", "code": "professional"}]}, "use": "preauthorization",
          "patient": case["patient"], "created": case["submitted_iso"], "insurer": {"display": case["plan"]},
          "request": {"identifier": {"value": case["identifier"]}}, "outcome": outc, "disposition": disp,
          "item": [{"itemSequence": 1, "extension": [{"url": REVIEW_ACTION, "valueCodeableConcept": {"coding": [{"system": X12, "code": action}]}}],
                    "adjudication": [{"category": {"coding": [{"code": "submitted"}]}}]}],
          "meta": {"versionId": str(case["version"])}}
    if notes:
        cr["processNote"] = notes
    if state == "approved":
        start = datetime.now(timezone.utc).date()
        cr["preAuthRef"] = "PA-" + hashlib.sha256(f"{case['identifier']}-{case['version']}".encode()).hexdigest()[:10].upper()
        cr["preAuthPeriod"] = {"start": start.isoformat(), "end": (start + timedelta(days=rule_["approved_days"])).isoformat()}
    return cr


def _state(case: dict) -> str:
    """not-required | pended-info | denied | queued (clinical review pending) | approved."""
    if not case["pa_required"]:
        return "not-required"
    ev = case["eval"]
    if ev["decision"] == "pended":
        return "pended-info"
    if RULES[case["code"]]["manual_review"] and time.time() - case["submitted_ts"] < REVIEW_SECONDS:
        return "queued"
    return ev["decision"]


@app.post("/fhir/Claim/$submit")
async def submit(request: Request, x_api_key: str = Header("")):
    if x_api_key != API_KEY:
        return outcome(401, "valid X-Api-Key required", "login")
    body = await request.json()
    claims = _entries(body, "Claim") if body.get("resourceType") == "Bundle" else []
    if len(claims) != 1:
        return outcome(400, "send a Bundle containing exactly one Claim (use=preauthorization)")
    claim = claims[0]
    if claim.get("use") != "preauthorization":
        return outcome(422, "Claim.use must be 'preauthorization'")
    items = claim.get("item") or []
    code = (((items[0].get("productOrService") or {}).get("coding") or [{}])[0].get("code")) if items else None
    if code not in RULES:
        return outcome(422, f"no medical policy for service '{code}'. Supported: {', '.join(RULES)}", "not-supported")
    identifier = ((claim.get("identifier") or [{}])[0]).get("value")
    patient = claim.get("patient")
    if not identifier or not patient:
        return outcome(400, "Claim.identifier and Claim.patient are required", "required")
    cov = _entries(body, "Coverage")
    plan = (claim.get("insurer") or {}).get("display") or (((cov[0].get("payor") or [{}])[0]).get("display") if cov else None)
    if not plan:
        return outcome(422, "the plan could not be determined: send Claim.insurer.display or a Coverage resource", "required")
    diagnoses = [c["code"] for d in claim.get("diagnosis", []) for c in (d.get("diagnosisCodeableConcept") or {}).get("coding", [])]
    served = parse_date(items[0].get("servicedDate") or (items[0].get("servicedPeriod") or {}).get("start")) or date.today()
    required = pa_required(code, plan)
    prev = CASES.get(identifier)
    case = {"id": prev["id"] if prev else f"cr-{len(CASES) + 1:04d}", "identifier": identifier, "patient": patient, "plan": plan, "code": code,
            "pa_required": required, "submitted_ts": time.time(), "submitted_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "version": (prev["version"] + 1) if prev else 1,
            "eval": evaluate(code, diagnoses, _supporting(claim), served)}
    CASES[identifier] = case
    cr = _claim_response(case)
    return JSONResponse({"resourceType": "Bundle", "type": "collection", "entry": [{"fullUrl": f"urn:uuid:{case['id']}", "resource": cr}]},
                        status_code=200, media_type=FHIR_JSON)


@app.get("/fhir/ClaimResponse/{rid}")
def read_claim_response(rid: str):
    case = next((c for c in CASES.values() if c["id"] == rid), None)
    if not case:
        return outcome(404, f"ClaimResponse/{rid}", "not-found")
    return JSONResponse(_claim_response(case), media_type=FHIR_JSON)


@app.get("/fhir/ClaimResponse")
def search_claim_response(request: Request):
    ident = request.query_params.get("request")
    cases = [c for c in CASES.values() if ident is None or c["identifier"] == ident]
    return JSONResponse({"resourceType": "Bundle", "type": "searchset", "total": len(cases),
                         "entry": [{"resource": _claim_response(c)} for c in cases]}, media_type=FHIR_JSON)


@app.post("/admin/reset")
def reset(x_api_key: str = Header("")):
    if x_api_key != API_KEY:
        raise HTTPException(401)
    CASES.clear()
    return {"ok": True}


@app.get("/healthz")
def healthz():
    return {"ok": True}
