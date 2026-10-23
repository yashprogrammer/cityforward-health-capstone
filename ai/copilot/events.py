"""Receiving the EHR's events (Build Session 5): verify, store, acknowledge fast, process safely.

    EHR --signed CloudEvent--> POST /webhooks/meditrack
        1. verify HMAC over the raw body + timestamp window      (401 otherwise; nothing parsed before this)
        2. store in event_inbox keyed by event id                 (duplicate -> 200 'duplicate', not processed twice)
        3. return 200 immediately                                 (the EHR must not wait on AI work or it will retry)
    worker: claim due rows (FOR UPDATE SKIP LOCKED, safe with several replicas) -> handler -> DONE | RETRY(backoff) | DEAD

Events carry IDs only. Handlers fetch the CURRENT state from the FHIR API ("re-sync from the source of truth"), so late, duplicate or out-of-order
events cannot corrupt anything. The payload's `fhirBase` is never trusted (that would let anyone who can sign an event aim our credentials at their server).
"""
import hashlib
import hmac
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from . import audit, coding_api, db, priorauth, priorauth_api, rag
from .access import authorize
from .auth import CurrentUser, Principal
from .chart import load_chart
from .config import settings

router = APIRouter()
MAX_BODY = 64 * 1024
TOLERANCE_S = 300
RETRY_DELAYS = [10, 30, 120, 600, 1800]      # seconds; DEAD after the last attempt fails
TYPE_PREFIX = "org.cityforward.meditrack."
WORKER = Principal(user="svc.event-worker", role="SERVICE", name="Event worker", units=["ALL"])
OWN_DOC_TYPES = {"PRIOR_AUTH", "CODING_SUMMARY"}      # documents this service filed itself: ignore their events (no loops)


# ---------------------------------------------------------------------------------------------- verification
def sign(secret: str, ts: str, body: bytes | str) -> str:
    body = body.decode() if isinstance(body, bytes) else body
    return hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()


def verify_signature(header: str, body: bytes, now: float | None = None) -> None:
    """Raises HTTPException(401) unless the header carries a valid, fresh signature from a current secret."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts, sig = parts["t"], parts["v1"]
        age = (now or time.time()) - int(ts)
    except (ValueError, KeyError):
        raise HTTPException(401, "malformed signature header") from None
    if abs(age) > TOLERANCE_S:
        raise HTTPException(401, "signature timestamp outside the allowed window")
    secrets = [s for s in (settings().webhook_secret, os.getenv("AI_WEBHOOK_SECRET_PREVIOUS")) if s]      # rotation: accept the previous secret briefly
    if not any(hmac.compare_digest(sig, sign(s, ts, body)) for s in secrets):
        raise HTTPException(401, "bad signature")


# ---------------------------------------------------------------------------------------------- endpoint
@router.post("/webhooks/meditrack")
async def receive(request: Request):
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(413, "event too large")
    verify_signature(request.headers.get("x-meditrack-signature", ""), raw)
    try:
        ev = json.loads(raw)
        eid, etype = ev["id"], ev["type"]
        data = ev.get("data") or {}
        if ev.get("specversion") != "1.0" or not isinstance(eid, str) or not etype.startswith(TYPE_PREFIX):
            raise ValueError("not a MediTrack CloudEvent")
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise HTTPException(400, f"invalid event: {e}") from None
    short = etype.removeprefix(TYPE_PREFIX).removesuffix(".v1")
    ignored = short not in HANDLERS or (short == "document.added" and data.get("docType") in OWN_DOC_TYPES)
    with db.system() as c:
        row = c.execute("""INSERT INTO event_inbox (event_id, type, patient_id, encounter_id, unit, status) VALUES (%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (event_id) DO NOTHING RETURNING id""",
                        (eid, short, str(data.get("patientId") or ""), str(data.get("encounterId") or ""), data.get("unit"), "IGNORED" if ignored else "RECEIVED")).fetchone()
    if row is None:
        return JSONResponse({"status": "duplicate"}, status_code=200)
    audit.log("svc.webhook", f"event.{short}", str(data.get("patientId") or "") or None, detail={"event_id": eid, "status": "ignored" if ignored else "queued"})
    return JSONResponse({"status": "ignored" if ignored else "accepted"}, status_code=200)


# ---------------------------------------------------------------------------------------------- handlers
def _rt():
    from .app import rt
    return rt


def _sync(patient_id: str):
    chart = load_chart(_rt().get_fhir(), patient_id)
    return chart, rag.index_chart(chart)


def on_document_added(ev: dict) -> dict:
    _, idx = _sync(ev["patient_id"])
    return {"indexed": idx["chunks"], "added": idx["added"]}


def on_discharge_advised(ev: dict) -> dict:
    """Discharge was advised: this is the clock that costs money. Have the paperwork ready before anyone asks."""
    chart, idx = _sync(ev["patient_id"])
    made = []
    from .payer_client import PayerClient  # noqa: F401  (payer reached through priorauth_api.deps)
    for sug in priorauth.suggest_services(chart, priorauth_api.deps.get_payer()):
        if sug.pa_required and not priorauth_api.open_request(chart.patient_id, sug.service_code):
            made.append(priorauth_api.create_draft_for(WORKER, chart.patient_id, sug.service_code, chart=chart)["id"])
    return {"indexed": idx["chunks"], "prior_auth_drafts": made}


def on_discharged(ev: dict) -> dict:
    """Discharged: queue the coding worksheet for the HIM coder."""
    chart, idx = _sync(ev["patient_id"])
    stay = chart.current_encounter()
    ws = None
    if stay and not coding_api.open_worksheet(chart.patient_id, stay.id):
        ws = coding_api.create_for(WORKER, chart.patient_id, chart=chart)["id"]
    return {"indexed": idx["chunks"], "coding_worksheet": ws}


HANDLERS = {"document.added": on_document_added, "encounter.discharge-advised": on_discharge_advised, "encounter.discharged": on_discharged}


# ---------------------------------------------------------------------------------------------- worker
def process_due(limit: int = 10) -> int:
    """One pass: claim due rows, run handlers, record the outcome. Returns how many rows were attempted."""
    with db.system() as c:
        rows = c.execute("""UPDATE event_inbox SET attempts = attempts + 1 WHERE id IN (
                              SELECT id FROM event_inbox WHERE status IN ('RECEIVED','RETRY') AND next_try <= now() ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED)
                            RETURNING *""", (limit,)).fetchall()
    for r in rows:
        ev = {"event_id": r["event_id"], "type": r["type"], "patient_id": r["patient_id"], "encounter_id": r["encounter_id"], "unit": r["unit"]}
        try:
            result = HANDLERS[r["type"]](ev)
            status, err, nxt = "DONE", None, None
        except Exception as e:   # noqa: BLE001 - any failure is retried, then dead-lettered
            result, err = None, f"{type(e).__name__}: {e}"[:300]
            if r["attempts"] > len(RETRY_DELAYS):
                status, nxt = "DEAD", None
            else:
                status, nxt = "RETRY", datetime.now(timezone.utc) + timedelta(seconds=RETRY_DELAYS[r["attempts"] - 1])
        with db.system() as c:
            c.execute("""UPDATE event_inbox SET status=%s, last_error=%s, result=%s, processed_at=CASE WHEN %s='DONE' THEN now() ELSE processed_at END,
                         next_try=COALESCE(%s, next_try) WHERE id=%s""", (status, err, json.dumps(result) if result is not None else None, status, nxt, r["id"]))
        audit.log("svc.event-worker", f"event.{r['type']}.{status.lower()}", r["patient_id"] or None, "allowed" if status == "DONE" else "error",
                  {"event_id": r["event_id"], "attempt": r["attempts"], **({"error": err} if err else {})})
    return len(rows)


def worker_loop() -> None:
    while True:
        try:
            n = process_due()
        except Exception as e:   # noqa: BLE001 - a worker must never die
            print(f"[event-worker] {e}")
            n = 0
        time.sleep(0.5 if n else float(os.getenv("EVENT_POLL_SECONDS", "2")))


def start_worker() -> None:
    if os.getenv("EVENT_WORKER", "on") != "off":
        threading.Thread(target=worker_loop, daemon=True, name="event-worker").start()


# ---------------------------------------------------------------------------------------------- operations (IT)
@router.get("/api/events")
def inbox(status: str | None = None, p: Principal = CurrentUser):
    authorize(p, "events.view")
    with db.system() as c:
        rows = c.execute("SELECT * FROM event_inbox WHERE (%s::text IS NULL OR status=%s) ORDER BY id DESC LIMIT 200", (status, status)).fetchall()
    return [{**r, "received_at": r["received_at"].isoformat(), "next_try": r["next_try"].isoformat(), "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None} for r in rows]


@router.post("/api/events/{event_id}/replay")
def replay(event_id: str, p: Principal = CurrentUser):
    authorize(p, "events.replay")
    with db.system() as c:
        r = c.execute("UPDATE event_inbox SET status='RECEIVED', attempts=0, next_try=now(), last_error=NULL WHERE event_id=%s AND status IN ('DEAD','DONE','RETRY') RETURNING id", (event_id,)).fetchone()
    if not r:
        raise HTTPException(404, "nothing to replay")
    audit.log(p, "event.replay", None, detail={"event_id": event_id})
    return {"ok": True}
