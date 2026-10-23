"""Coding worksheet endpoints: propose -> coder decides line by line -> finalize (one principal diagnosis, no Excludes1 conflicts)."""
import json
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import coding, db, rag
from . import terminology as T
from .access import authorize
from .auth import CurrentUser, Principal
from .chart import load_chart
from .priorauth import now_iso

router = APIRouter(prefix="/api")


def _rt():
    from .app import rt
    return rt


def _row(wid: str) -> dict:
    with db.session() as c:
        r = c.execute("SELECT * FROM coding_worksheet WHERE id=%s", (wid,)).fetchone()
    if not r:
        raise HTTPException(404, "no such worksheet")
    return r


def _view(r: dict) -> dict:
    open_items = [i["id"] for i in r["items"] if not i["decision"]]
    return {**{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()}, "open_items": open_items}


def _save(r: dict, **ch) -> dict:
    r = {**r, **ch}
    with db.session() as c:
        c.execute("UPDATE coding_worksheet SET status=%s, items=%s, finalized_by=%s, finalized_at=%s, history=%s WHERE id=%s",
                  (r["status"], json.dumps(r["items"]), r["finalized_by"], r["finalized_at"], json.dumps(r["history"]), r["id"]))
    return r


@router.post("/patients/{patient_id}/coding", status_code=201)
def create(patient_id: str, p: Principal = CurrentUser):
    authorize(p, "coding.suggest")                      # role first (cheap), then the patient once we know the unit
    rt = _rt()
    chart = load_chart(rt.get_fhir(), patient_id)
    authorize(p, "coding.suggest", patient_id, unit=chart.unit)
    rag.index_chart(chart, p)
    w = coding.build_worksheet(rt.get_gateway(), chart, principal=p)
    wid = "CW-" + uuid.uuid4().hex[:10].upper()
    hist = [{"at": now_iso(), "by": p.user, "what": f"worksheet created ({w.mode}{', ' + w.model if w.model else ''}); {len(w.items)} line(s), {len(w.queries)} documentation query(ies)"}]
    with db.session() as c:
        c.execute("""INSERT INTO coding_worksheet (id, patient_id, encounter_id, unit, status, items, queries, mode, model, problems, created_by, history)
                     VALUES (%s,%s,%s,%s,'OPEN',%s,%s,%s,%s,%s,%s,%s)""",
                  (wid, patient_id, w.encounter_id, w.unit, json.dumps([i.model_dump() for i in w.items]), json.dumps(w.queries), w.mode, w.model,
                   json.dumps(w.problems), p.user, json.dumps(hist)))
    return _view(_row(wid))


@router.get("/patients/{patient_id}/coding")
def list_for_patient(patient_id: str, p: Principal = CurrentUser):
    authorize(p, "coding.view", patient_id)
    with db.session() as c:
        return [_view(r) for r in c.execute("SELECT * FROM coding_worksheet WHERE patient_id=%s ORDER BY created_at DESC", (patient_id,)).fetchall()]


@router.get("/coding/{wid}")
def get_one(wid: str, p: Principal = CurrentUser):
    r = _row(wid)
    authorize(p, "coding.view", r["patient_id"], unit=r["unit"])
    return _view(r)


class DecideIn(BaseModel):
    action: str                    # accept | reject | modify
    code: str | None = None
    reason: str = ""


@router.post("/coding/{wid}/items/{item_id}")
def decide(wid: str, item_id: str, body: DecideIn, p: Principal = CurrentUser):
    r = _row(wid)
    authorize(p, "coding.decide", r["patient_id"], unit=r["unit"])
    if r["status"] != "OPEN":
        raise HTTPException(409, "worksheet is final")
    item = next((i for i in r["items"] if i["id"] == item_id), None)
    if item is None:
        raise HTTPException(404, "no such line")
    if body.action == "accept":
        item["decision"], item["final_code"] = "accepted", item["code"]
    elif body.action == "reject":
        if len(body.reason.strip()) < 5:
            raise HTTPException(422, "a reason is required to reject a suggested code")
        item["decision"], item["final_code"] = "rejected", None
    elif body.action == "modify":
        code = T.pretty((body.code or "").strip())
        if not T.billable(code):
            raise HTTPException(422, f"{body.code!r} is not a billable ICD-10-CM code")
        if len(body.reason.strip()) < 5:
            raise HTTPException(422, "a reason is required to change a code")
        item["decision"], item["final_code"] = "modified", code
    else:
        raise HTTPException(422, "action must be accept, reject or modify")
    item["reason"], item["decided_by"] = body.reason, p.user
    hist = [*r["history"], {"at": now_iso(), "by": p.user, "what": f"{body.action} {item['code']}" + (f" -> {item['final_code']}" if body.action == "modify" else "")}]
    return _view(_save(r, history=hist))


class FinalizeIn(BaseModel):
    principal_item: str


@router.post("/coding/{wid}/finalize")
def finalize(wid: str, body: FinalizeIn, p: Principal = CurrentUser):
    r = _row(wid)
    authorize(p, "coding.decide", r["patient_id"], unit=r["unit"])
    if r["status"] != "OPEN":
        raise HTTPException(409, "worksheet is already final")
    undecided = [i["id"] for i in r["items"] if not i["decision"]]
    if undecided:
        raise HTTPException(409, f"decide every line first: {', '.join(undecided)}")
    final = [i for i in r["items"] if i["decision"] in ("accepted", "modified")]
    if not any(i["id"] == body.principal_item for i in final):
        raise HTTPException(422, "principal_item must be one of the accepted or modified lines")
    codes = [i["final_code"] for i in final]
    if len(set(map(T.norm, codes))) != len(codes):
        raise HTTPException(409, "the same code appears twice")
    for a in range(len(codes)):
        for b in range(a + 1, len(codes)):
            if T.excludes1_conflict(codes[a], codes[b]):
                raise HTTPException(409, f"{codes[a]} and {codes[b]} cannot be reported together (Excludes1)")
    for i in r["items"]:
        i["role"] = "principal" if i["id"] == body.principal_item else "secondary"
    chart = load_chart(_rt().get_fhir(), r["patient_id"])
    from .coding import Worksheet
    w = Worksheet(patient_id=r["patient_id"], encounter_id=r["encounter_id"], unit=r["unit"], items=[], queries=[], mode=r["mode"])
    final_codes = [{"code": i["final_code"], "description": T.describe(i["final_code"]), "role": i["role"]} for i in final]
    final_codes.sort(key=lambda c: c["role"] != "principal")
    r = _save(r, status="FINAL", finalized_by=p.user, finalized_at=now_iso(), history=[*r["history"], {"at": now_iso(), "by": p.user, "what": f"finalized {len(final_codes)} code(s)"}])
    try:
        _rt().get_fhir().post_document_pdf(r["patient_id"], r["encounter_id"], f"{wid} coding worksheet", coding.worksheet_pdf(w, chart.name, final_codes), doc_type="CODING_SUMMARY")
    except Exception as e:   # noqa: BLE001 - filing is best-effort
        print(f"[coding] could not file worksheet: {e}")
    return {**_view(r), "final_codes": final_codes}
