"""Outbound event webhooks (MediTrack v3.4): tell other systems that something happened in the chart.

Design (the same one every EHR vendor ends up with):
  * transactional outbox: the event is written in the SAME database transaction as the change that caused it,
    so an event is never lost and never announces something that was rolled back;
  * a dispatcher thread delivers outbox rows: HMAC-signed CloudEvents over HTTPS, exponential backoff, dead letter
    after the retry budget, manual replay;
  * the payload carries IDs only, no patient data (minimum necessary). The receiver fetches what it needs through the
    FHIR API with its own token, which is audited and scope-limited.
Delivery is at-least-once, so receivers must be idempotent (the CloudEvent id is the idempotency key).
"""
import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import smart
from .db import audit, connect, now, row, rows

router = APIRouter()
TYPE_PREFIX = "org.cityforward.meditrack."
RETRY_DELAYS = [5, 15, 45, 120, 300]    # seconds; the outbox row is dead-lettered after the last one fails
SOURCE = "https://meditrack.cityforward.example"
EVENTS = ["encounter.discharge-advised", "encounter.discharged", "document.added"]


def signature(secret: str, ts: str, body: str) -> str:
    return "t=%s,v1=%s" % (ts, hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest())


def emit(conn, event_type: str, ip_no: str, extra: dict | None = None) -> int:
    """Queue an event for every active subscription that wants it. Call inside the caller's transaction."""
    adm = row(conn, "SELECT IP_NO, PT_ID, WRD_CD FROM IP_ADM_DTL WHERE IP_NO=%s", ip_no)
    if not adm:
        return 0
    n = 0
    for wh in rows(conn, "SELECT * FROM SYS_WEBHOOK WHERE IS_ACTV=1"):
        wanted = [e.strip() for e in wh["EVENTS"].split(",")]
        if "*" not in wanted and event_type not in wanted:
            continue
        evt_id = str(uuid.uuid4())
        ce = {"specversion": "1.0", "id": evt_id, "source": SOURCE, "type": TYPE_PREFIX + event_type + ".v1",
              "subject": f"Encounter/{ip_no}", "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "datacontenttype": "application/json",
              "data": {"patientId": str(adm["PT_ID"]), "encounterId": ip_no, "unit": adm["WRD_CD"], "fhirBase": smart.FHIR_BASE, **(extra or {})}}
        conn.execute("INSERT INTO SYS_WH_OUT (WH_ID, EVT_ID, EVT_TYP, PAYLOAD, STS, ATTEMPTS, NEXT_TRY, CRT_DT) VALUES (%s,%s,%s,%s,'PENDING',0,%s,%s)",
                     (wh["WH_ID"], evt_id, ce["type"], json.dumps(ce), time.time(), now()))
        n += 1
    return n


def deliver_due(client: httpx.Client | None = None) -> int:
    """One dispatcher pass: try every due outbox row once. Returns how many rows were attempted."""
    own = client is None
    client = client or httpx.Client(timeout=5.0)
    try:
        with connect() as c:
            due = rows(c, """SELECT o.*, w.WH_URL, w.WH_SECRET FROM SYS_WH_OUT o JOIN SYS_WEBHOOK w ON w.WH_ID = o.WH_ID
                             WHERE o.STS='PENDING' AND o.NEXT_TRY <= %s ORDER BY o.ID LIMIT 50""", time.time())
        for o in due:
            ts = str(int(time.time()))
            headers = {"Content-Type": "application/cloudevents+json", "X-MediTrack-Signature": signature(o["WH_SECRET"], ts, o["PAYLOAD"]),
                       "X-MediTrack-Delivery": str(o["ID"]), "User-Agent": "MediTrack-Webhooks/3.4"}
            err = None
            try:
                r = client.post(o["WH_URL"], content=o["PAYLOAD"], headers=headers)
                if not 200 <= r.status_code < 300:
                    err = f"HTTP {r.status_code}"
            except httpx.HTTPError as e:
                err = f"{type(e).__name__}: {e}"[:200]
            with connect() as c:
                if err is None:
                    c.execute("UPDATE SYS_WH_OUT SET STS='SENT', ATTEMPTS=ATTEMPTS+1, SENT_DT=%s, LAST_ERR=NULL WHERE ID=%s", (now(), o["ID"]))
                else:
                    attempts = o["ATTEMPTS"] + 1
                    if attempts > len(RETRY_DELAYS):
                        c.execute("UPDATE SYS_WH_OUT SET STS='FAILED', ATTEMPTS=%s, LAST_ERR=%s WHERE ID=%s", (attempts, err, o["ID"]))
                    else:
                        c.execute("UPDATE SYS_WH_OUT SET ATTEMPTS=%s, NEXT_TRY=%s, LAST_ERR=%s WHERE ID=%s",
                                  (attempts, time.time() + RETRY_DELAYS[attempts - 1], err, o["ID"]))
        return len(due)
    finally:
        if own:
            client.close()


def dispatcher_loop() -> None:
    client = httpx.Client(timeout=5.0)
    while True:
        try:
            deliver_due(client)
        except Exception as e:   # noqa: BLE001 - a dispatcher must never die
            print(f"[webhooks] {e}")
        time.sleep(float(os.getenv("WEBHOOK_POLL_SECONDS", "1.5")))


def start_dispatcher() -> None:
    threading.Thread(target=dispatcher_loop, daemon=True, name="webhook-dispatcher").start()


# ---------------------------------------------------------------- admin (IT)
class WebhookIn(BaseModel):
    name: str
    url: str
    secret: str
    events: str = "*"


@router.get("/int/admin/webhooks")
def list_webhooks():
    with connect() as c:
        subs = rows(c, "SELECT WH_ID, WH_NM, WH_URL, EVENTS, IS_ACTV, CRT_BY, CRT_DT FROM SYS_WEBHOOK ORDER BY WH_ID")
        counts = {r["WH_ID"]: r for r in rows(c, """SELECT WH_ID, COUNT(*) FILTER (WHERE STS='SENT') SENT, COUNT(*) FILTER (WHERE STS='PENDING') PENDING,
                                                    COUNT(*) FILTER (WHERE STS='FAILED') FAILED FROM SYS_WH_OUT GROUP BY WH_ID""")}
    return [{**s, **{k: counts.get(s["WH_ID"], {}).get(k, 0) for k in ("SENT", "PENDING", "FAILED")}} for s in subs]


@router.post("/int/admin/webhooks")
def add_webhook(body: WebhookIn):
    unknown = [e for e in body.events.split(",") if e.strip() not in EVENTS + ["*"]]
    if unknown:
        raise HTTPException(400, f"UNKNOWN EVENT TYPES: {unknown}")
    with connect() as c:
        wh_id = c.execute("INSERT INTO SYS_WEBHOOK (WH_NM, WH_URL, WH_SECRET, EVENTS, IS_ACTV, CRT_BY, CRT_DT) VALUES (%s,%s,%s,%s,1,%s,%s) RETURNING WH_ID",
                          (body.name, body.url, body.secret, body.events, "it.admin", now())).fetchone()["WH_ID"]
        audit(c, "it.admin", "WEBHOOK_ADDED", body.name)
    return {"ok": True, "id": wh_id}


@router.post("/int/admin/webhooks/{wh_id}/toggle")
def toggle_webhook(wh_id: int):
    with connect() as c:
        c.execute("UPDATE SYS_WEBHOOK SET IS_ACTV = 1 - IS_ACTV WHERE WH_ID=%s", (wh_id,))
    return {"ok": True}


@router.get("/int/admin/webhooks/outbox")
def outbox():
    with connect() as c:
        return rows(c, "SELECT ID, WH_ID, EVT_ID, EVT_TYP, STS, ATTEMPTS, LAST_ERR, CRT_DT, SENT_DT FROM SYS_WH_OUT ORDER BY ID DESC LIMIT 100")


@router.post("/int/admin/webhooks/outbox/{out_id}/replay")
def replay(out_id: int):
    with connect() as c:
        cur = c.execute("UPDATE SYS_WH_OUT SET STS='PENDING', ATTEMPTS=0, NEXT_TRY=%s, LAST_ERR=NULL WHERE ID=%s AND STS IN ('FAILED','SENT')", (time.time(), out_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "NOTHING TO REPLAY")
        audit(c, "it.admin", "WEBHOOK_REPLAY", str(out_id))
    return {"ok": True}
