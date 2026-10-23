"""Break-the-glass: a clinician who needs a chart outside their unit (a transfer, a consult at 3 a.m.) gets time-boxed access with a documented
reason instead of a phone call to IT. Access is granted immediately, logged loudly, and lands in the privacy officer's review queue (HIPAA 164.312(a)(2)(ii) emergency access).
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import audit, db
from .access import authorize, patient_unit
from .auth import CurrentUser, Principal

router = APIRouter(prefix="/api")
TTL_HOURS = 4


class GlassIn(BaseModel):
    reason: str


@router.post("/patients/{patient_id}/break-glass", status_code=201)
def request_access(patient_id: str, body: GlassIn, p: Principal = CurrentUser):
    authorize(p, "breakglass", about=patient_id)
    if p.patients and patient_id not in p.patients:
        audit.log(p, "breakglass", patient_id, "denied", {"why": "token limited to another patient"})
        raise HTTPException(403, "this token is limited to another patient")
    indexed, unit = patient_unit(patient_id)
    if not indexed:
        raise HTTPException(409, "patient is not indexed yet")
    if p.can_see_unit(unit):
        raise HTTPException(409, "you already have access to this patient's unit")
    if len(body.reason.strip()) < 15:
        raise HTTPException(422, "state the clinical reason (at least 15 characters)")
    expires = datetime.now(timezone.utc) + timedelta(hours=TTL_HOURS)
    with db.system() as c:
        gid = c.execute("INSERT INTO break_glass (user_id, patient_id, reason, expires_at) VALUES (%s,%s,%s,%s) RETURNING id", (p.user, patient_id, body.reason.strip(), expires)).fetchone()["id"]
    audit.log(p, "break-glass", patient_id, detail={"grant": gid, "unit": unit, "expires": expires.isoformat(timespec="minutes")})
    return {"id": gid, "expires_at": expires.isoformat(), "notice": "Access is logged and will be reviewed by the privacy officer."}


@router.get("/break-glass")
def pending(p: Principal = CurrentUser):
    authorize(p, "breakglass.review")
    with db.system() as c:
        return [{**r, "granted_at": r["granted_at"].isoformat(), "expires_at": r["expires_at"].isoformat(), "reviewed_at": r["reviewed_at"].isoformat() if r["reviewed_at"] else None}
                for r in c.execute("SELECT * FROM break_glass ORDER BY (reviewed_at IS NOT NULL), id DESC LIMIT 200").fetchall()]


class ReviewIn(BaseModel):
    note: str


@router.post("/break-glass/{gid}/review")
def review(gid: int, body: ReviewIn, p: Principal = CurrentUser):
    authorize(p, "breakglass.review")
    if len(body.note.strip()) < 5:
        raise HTTPException(422, "record the outcome of your review")
    with db.system() as c:
        r = c.execute("UPDATE break_glass SET reviewed_by=%s, reviewed_at=now(), review_note=%s WHERE id=%s AND reviewed_at IS NULL RETURNING patient_id", (p.user, body.note.strip(), gid)).fetchone()
    if not r:
        raise HTTPException(404, "no open grant with that id")
    audit.log(p, "break-glass.reviewed", r["patient_id"], detail={"grant": gid})
    return {"ok": True}
