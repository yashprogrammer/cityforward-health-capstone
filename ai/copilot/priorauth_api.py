"""Prior-authorization endpoints and persistence: draft -> clinician review -> approve -> submit -> payer decision -> filed back to the chart."""
import json
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import db, priorauth, rag
from .access import authorize
from .auth import CurrentUser, Principal
from .chart import load_chart
from .payer_client import PayerClient, PayerError, review_action

router = APIRouter(prefix="/api")


class Deps:
    """Collaborators, swapped by tests."""
    payer: PayerClient | None = None

    def get_payer(self) -> PayerClient:
        self.payer = self.payer or PayerClient()
        return self.payer


deps = Deps()


def _rt():
    from .app import rt   # late import: app imports this module
    return rt


def _row(pa_id: str) -> dict:
    with db.session() as c:
        r = c.execute("SELECT * FROM prior_auth WHERE id=%s", (pa_id,)).fetchone()
    if not r:
        raise HTTPException(404, "no such prior authorization")
    return r


def _load(pa_id: str, p: Principal, action: str) -> dict:
    r = _row(pa_id)
    authorize(p, action, r["patient_id"], unit=r["unit"])
    return r


def _view(r: dict) -> dict:
    reqs = [priorauth.Req(**q) for q in r["requirements"]]
    gaps = [q.id for q in reqs if q.status not in priorauth.RESOLVED]
    return {**{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()}, "gaps": gaps, "can_approve": not gaps and r["status"] == "DRAFT"}


def _event(r: dict, who: str, what: str) -> list:
    return [*r["history"], {"at": priorauth.now_iso(), "by": who, "what": what}]


def _save(r: dict, **changes) -> dict:
    r = {**r, **changes}
    with db.session() as c:
        c.execute("""UPDATE prior_auth SET status=%s, requirements=%s, letter=%s, approved_by=%s, approved_at=%s, submitted_by=%s, submitted_at=%s,
                     claim_identifier=%s, payer_ref=%s, payer_action=%s, payer_notes=%s, preauth_ref=%s, history=%s WHERE id=%s""",
                  (r["status"], json.dumps(r["requirements"]), r["letter"], r["approved_by"], r["approved_at"], r["submitted_by"], r["submitted_at"],
                   r["claim_identifier"], r["payer_ref"], r["payer_action"], json.dumps(r["payer_notes"]) if r["payer_notes"] is not None else None,
                   r["preauth_ref"], json.dumps(r["history"]), r["id"]))
    return r


def _chart_and_draft(r: dict):
    chart = load_chart(_rt().get_fhir(), r["patient_id"])
    d = priorauth.Draft(patient_id=r["patient_id"], encounter_id=r["encounter_id"], unit=r["unit"], service_code=r["service_code"], service_name=r["service_name"],
                        plan=r["plan"], pa_required=True, requirements=[priorauth.Req(**q) for q in r["requirements"]], diagnoses=r["diagnoses"],
                        letter=r["letter"] or "", mode=r["mode"], model=r["model"] or "", problems=r["problems"])
    return chart, d


# ------------------------------------------------------------------------------------------------ endpoints
@router.get("/patients/{patient_id}/prior-auth/suggestions")
def suggestions(patient_id: str, p: Principal = CurrentUser):
    authorize(p, "priorauth.view", patient_id)
    chart = load_chart(_rt().get_fhir(), patient_id)
    return priorauth.suggest_services(chart, deps.get_payer())


class DraftIn(BaseModel):
    service_code: str


@router.post("/patients/{patient_id}/prior-auth", status_code=201)
def create_draft(patient_id: str, body: DraftIn, p: Principal = CurrentUser):
    authorize(p, "priorauth.draft")                     # role first (cheap), then the patient once we know the unit
    rt = _rt()
    chart = load_chart(rt.get_fhir(), patient_id)
    authorize(p, "priorauth.draft", patient_id, unit=chart.unit)
    rag.index_chart(chart, p)       # the retrieval step needs a fresh index of this chart
    try:
        rule = deps.get_payer().rule(body.service_code, chart.payer)
    except PayerError as e:
        raise HTTPException(422 if e.status == 404 else 502, str(e)) from e
    rule["code"] = body.service_code
    if not rule.get("priorAuthRequired"):
        raise HTTPException(409, f"{chart.payer or 'self-pay'} does not require prior authorization for {body.service_code}")
    d = priorauth.draft_prior_auth(rt.get_gateway(), chart, rule, True, principal=p)
    pa_id = "PA-" + uuid.uuid4().hex[:10].upper()
    status = "DRAFT"
    hist = [{"at": priorauth.now_iso(), "by": p.user, "what": f"draft created ({d.mode}{', ' + d.model if d.model else ''}); {len(d.gaps)} open requirement(s)"}]
    with db.session() as c:
        c.execute("""INSERT INTO prior_auth (id, patient_id, encounter_id, unit, service_code, service_name, plan, status, requirements, diagnoses, letter, mode, model,
                     problems, created_by, history) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                  (pa_id, patient_id, d.encounter_id, d.unit, d.service_code, d.service_name, d.plan, status,
                   json.dumps([q.model_dump() for q in d.requirements]), json.dumps(d.diagnoses), d.letter, d.mode, d.model, json.dumps(d.problems), p.user, json.dumps(hist)))
    return _view(_row(pa_id))


@router.get("/patients/{patient_id}/prior-auth")
def list_for_patient(patient_id: str, p: Principal = CurrentUser):
    authorize(p, "priorauth.view", patient_id)
    with db.session() as c:
        return [_view(r) for r in c.execute("SELECT * FROM prior_auth WHERE patient_id=%s ORDER BY created_at DESC", (patient_id,)).fetchall()]


@router.get("/prior-auth/{pa_id}")
def get_one(pa_id: str, p: Principal = CurrentUser):
    return _view(_load(pa_id, p, "priorauth.view"))


class ReviewIn(BaseModel):
    action: str                   # accept | reject | attest | provide
    statement: str | None = None
    value: float | None = None
    measured_on: str | None = None


@router.post("/prior-auth/{pa_id}/requirements/{req_id}")
def review_requirement(pa_id: str, req_id: str, body: ReviewIn, p: Principal = CurrentUser):
    r = _row(pa_id)
    if r["status"] not in ("DRAFT", "PENDED"):
        raise HTTPException(409, f"request is {r['status']}; only drafts and pended requests can be edited")
    authorize(p, "priorauth.attest" if body.action == "attest" else "priorauth.edit", r["patient_id"], unit=r["unit"])
    reqs = [priorauth.Req(**q) for q in r["requirements"]]
    req = next((q for q in reqs if q.id == req_id), None)
    if req is None:
        raise HTTPException(404, "no such requirement")
    if body.action == "accept":
        if req.status not in ("candidate", "stale", "fails_policy"):
            raise HTTPException(409, f"nothing to accept (status {req.status})")
        if req.status == "fails_policy":
            raise HTTPException(409, "the documented value does not meet the policy; provide a different measurement or do not submit")
        if not (req.statement or body.statement):
            raise HTTPException(422, "write the statement you are accepting (what the evidence shows)")
        req.statement, req.status = body.statement or req.statement, "accepted"
    elif body.action == "attest":
        if req.kind != "attestation" or req.status not in ("needs_attestation",):
            raise HTTPException(409, "only open attestation requirements can be attested")
        req.status, req.statement = "attested", body.statement or f"Attested by {p.name or p.user}."
    elif body.action == "provide":
        if not body.statement or len(body.statement.strip()) < 20:
            raise HTTPException(422, "statement of at least 20 characters required")
        req.statement, req.status = body.statement.strip(), "clinician_provided"
        if req.kind == "value":
            if body.value is None:
                raise HTTPException(422, "value required for a measurement requirement")
            req.value, req.measured_on = body.value, body.measured_on
    elif body.action == "reject":
        req.status, req.statement, req.evidence = "missing", "", []
    else:
        raise HTTPException(422, "action must be accept, reject, attest or provide")
    req.reviewed_by = p.user
    chart, d = _chart_and_draft(r)
    d.requirements = [req if q.id == req_id else q for q in reqs]
    # editing a pended request reopens it: the clinician signs the new evidence again before it goes back to the payer
    return _view(_save(r, status="DRAFT", requirements=[q.model_dump() for q in d.requirements], letter=priorauth.compose_letter(chart, d),
                       history=_event(r, p.user, f"{body.action} {req_id}")))


class ApproveIn(BaseModel):
    submit_with_gaps_reason: str | None = None


@router.post("/prior-auth/{pa_id}/approve")
def approve(pa_id: str, body: ApproveIn | None = None, p: Principal = CurrentUser):
    r = _load(pa_id, p, "priorauth.approve")
    if r["status"] != "DRAFT":
        raise HTTPException(409, f"request is {r['status']}")
    gaps = [q["id"] for q in r["requirements"] if q["status"] not in priorauth.RESOLVED]
    reason = (body.submit_with_gaps_reason if body else None) or ""
    if gaps and len(reason.strip()) < 15:
        raise HTTPException(409, f"open requirements: {', '.join(gaps)}. Resolve them, or approve with a written reason (the payer will probably pend the request).")
    what = "approved for submission" + (f" with gaps {gaps}: {reason}" if gaps else "")
    return _view(_save(r, status="APPROVED", approved_by=p.user, approved_at=priorauth.now_iso(), history=_event(r, p.user, what)))


def _apply_payer_response(r: dict, cr: dict, who: str) -> dict:
    action = review_action(cr)
    status = {"A1": "CERTIFIED", "A3": "DENIED", "NA": "NOT_REQUIRED"}.get(action)
    notes = [n["text"] for n in cr.get("processNote", [])]
    if status is None:      # A4: pended - either missing information or clinical review in progress
        status = "PENDED" if any(t.startswith("Missing:") for t in notes) else "QUEUED"
    return _save(r, status=status, payer_ref=cr["id"], payer_action=action, payer_notes=notes, preauth_ref=cr.get("preAuthRef"),
                 history=_event(r, who, f"payer: {action} {cr.get('disposition', '')}"))


@router.post("/prior-auth/{pa_id}/submit")
def submit(pa_id: str, p: Principal = CurrentUser):
    r = _load(pa_id, p, "priorauth.submit")
    if r["status"] != "APPROVED":
        raise HTTPException(409, f"a clinician must approve the request first (status {r['status']})" if r["status"] in ("DRAFT", "PENDED") else f"request is {r['status']}")
    chart, d = _chart_and_draft(r)
    identifier = r["claim_identifier"] or f"{r['id']}"
    try:
        cr = deps.get_payer().submit(priorauth.build_pas_bundle(chart, d, identifier))
    except PayerError as e:
        raise HTTPException(502, f"payer rejected the submission: {e}") from e
    r = _save(r, claim_identifier=identifier, submitted_by=p.user, submitted_at=priorauth.now_iso(), status="SUBMITTED")
    r = _apply_payer_response(r, cr, "payer")
    _file_packet(chart, d, r)
    return _view(r)


@router.post("/prior-auth/{pa_id}/refresh")
def refresh(pa_id: str, p: Principal = CurrentUser):
    r = _load(pa_id, p, "priorauth.view")
    if r["status"] != "QUEUED" or not r["payer_ref"]:
        return _view(r)
    try:
        cr = deps.get_payer().poll(r["payer_ref"])
    except PayerError as e:
        raise HTTPException(502, str(e)) from e
    r = _apply_payer_response(r, cr, "payer")
    if r["status"] in ("CERTIFIED", "DENIED"):
        chart, d = _chart_and_draft(r)
        _file_packet(chart, d, r)
    return _view(r)


def _file_packet(chart, d, r: dict) -> None:
    """Write the packet back into the patient's chart so the next person (and the auditor) finds it where they expect it."""
    try:
        decision = f"{r['payer_action']} {r['status']}" + (f" ({r['preauth_ref']})" if r["preauth_ref"] else "")
        _rt().get_fhir().post_document_pdf(chart.patient_id, r["encounter_id"], f"{r['id']} {r['service_code']} prior authorization", priorauth.packet_pdf(chart, d, decision))
    except Exception as e:   # noqa: BLE001 - filing is best-effort; the request itself is already recorded
        print(f"[priorauth] could not file packet: {e}")
