import json
import time

import pytest

from copilot import audit, db, events
from copilot.app import rt as runtime
from copilot.config import settings
from copilot.gateway import Gateway
from meditrack.backend import events as mt_events
from meditrack.backend.db import connect_once
from tests.conftest import bearer, user_token

DOYLE, WHITAKER, HALE = "IP2610-0151", "IP2610-0138", "IP2610-0142"
URL = "http://testserver/webhooks/meditrack"


@pytest.fixture()
def wired(rt, payer, clean_db, meditrack, client):
    """MediTrack's real outbox, delivering into our real endpoint, in one process."""
    runtime.gateway = Gateway(None)
    with connect_once() as c:
        c.execute("DELETE FROM SYS_WEBHOOK"); c.execute("DELETE FROM SYS_WH_OUT"); c.commit()
    r = meditrack.post("/int/admin/webhooks", json={"name": "ai", "url": URL, "secret": settings().webhook_secret, "events": "*"})
    assert r.status_code == 200
    return client


def deliver(sender_client):
    """Run MediTrack's dispatcher once, with our app as the network."""
    return mt_events.deliver_due(sender_client)


def inbox(client_, meditrack, status=None):
    admin = bearer(user_token(meditrack, HALE, user="it.admin")["access_token"])
    return client_.get("/api/events", headers=admin, params={"status": status} if status else None).json()


def test_discharge_advised_end_to_end_prepares_prior_auth_drafts(wired, meditrack, rt):
    assert meditrack.post(f"/int/ip/{DOYLE}/discharge-advise").status_code == 200
    assert deliver(wired) == 1                                                  # signed CloudEvent accepted by our endpoint
    rows = inbox(wired, meditrack)
    assert len(rows) == 1 and rows[0]["type"] == "encounter.discharge-advised" and rows[0]["status"] == "RECEIVED"
    assert events.process_due() == 1
    done = inbox(wired, meditrack, "DONE")[0]
    assert sorted(done["result"]["prior_auth_drafts"]) and len(done["result"]["prior_auth_drafts"]) == 2          # SNF + walker, for the humans to review
    with db.system() as c:
        pas = c.execute("SELECT service_code, status, created_by FROM prior_auth ORDER BY service_code").fetchall()
    assert [(x["service_code"], x["status"], x["created_by"]) for x in pas] == [("E0143", "DRAFT", "svc.event-worker"), ("SNF-ADM", "DRAFT", "svc.event-worker")]
    # processing again (the same event replayed by the EHR) must not create more drafts
    out = meditrack.get("/int/admin/webhooks/outbox").json()[0]
    assert meditrack.post(f"/int/admin/webhooks/outbox/{out['ID']}/replay").status_code == 200
    assert deliver(wired) == 1
    assert events.process_due() == 0
    assert len(inbox(wired, meditrack)) == 1                                    # the duplicate was recognised by event id
    with db.system() as c:
        assert c.execute("SELECT count(*) n FROM prior_auth").fetchone()["n"] == 2


def test_discharged_queues_the_coding_worksheet_once(wired, meditrack):
    assert meditrack.post(f"/int/ip/{WHITAKER}/discharge-advise").status_code == 200
    meditrack.post(f"/int/ip/{WHITAKER}/discharge", json={"disposition": "HOME"})
    deliver(wired)
    assert events.process_due() == 2
    with db.system() as c:
        ws = c.execute("SELECT created_by, status FROM coding_worksheet").fetchall()
    assert len(ws) == 1 and ws[0]["created_by"] == "svc.event-worker" and ws[0]["status"] == "OPEN"


def test_signature_failures_are_rejected_before_parsing(client):
    body = json.dumps({"specversion": "1.0", "id": "e-1", "type": "org.cityforward.meditrack.document.added.v1", "data": {"patientId": "1"}})
    ts = str(int(time.time()))
    good = f"t={ts},v1={events.sign(settings().webhook_secret, ts, body)}"
    assert client.post("/webhooks/meditrack", content=body, headers={"x-meditrack-signature": good}).status_code == 200
    for header in ("", "garbage", f"t={ts},v1={'0' * 64}", f"t={int(ts) - 3600},v1={events.sign(settings().webhook_secret, str(int(ts) - 3600), body)}"):
        assert client.post("/webhooks/meditrack", content=body, headers={"x-meditrack-signature": header}).status_code == 401, header
    tampered = body.replace('"1"', '"2"')
    assert client.post("/webhooks/meditrack", content=tampered, headers={"x-meditrack-signature": good}).status_code == 401
    big = "x" * 70000
    assert client.post("/webhooks/meditrack", content=big, headers={"x-meditrack-signature": good}).status_code == 413


def _signed(client, ev):
    body = json.dumps(ev)
    ts = str(int(time.time()))
    return client.post("/webhooks/meditrack", content=body, headers={"x-meditrack-signature": f"t={ts},v1={events.sign(settings().webhook_secret, ts, body)}"})


def test_unknown_malformed_and_self_generated_events(client, rt, clean_db):
    ce = {"specversion": "1.0", "type": "org.cityforward.meditrack.document.added.v1", "data": {"patientId": "1", "encounterId": "IP1"}}
    assert _signed(client, {**ce, "id": "own-1", "data": {**ce["data"], "docType": "PRIOR_AUTH"}}).json()["status"] == "ignored"       # our own packet: no feedback loop
    assert _signed(client, {**ce, "id": "t-1", "type": "org.cityforward.meditrack.bed.moved.v1"}).json()["status"] == "ignored"
    assert _signed(client, {**ce, "id": "x-1", "type": "com.other.thing.v1"}).status_code == 400
    assert _signed(client, {"hello": "world"}).status_code == 400
    assert _signed(client, {**ce, "id": "ok-1"}).json()["status"] == "accepted"
    assert _signed(client, {**ce, "id": "ok-1"}).json()["status"] == "duplicate"


def test_payload_fhir_base_is_never_trusted(client, rt, clean_db, monkeypatch):
    seen = []
    orig = rt.fhir.read
    monkeypatch.setattr(rt.fhir, "everything", lambda pid: seen.append(rt.fhir.base) or (_ for _ in ()).throw(RuntimeError("stop")))
    ev = {"specversion": "1.0", "id": "ssrf-1", "type": "org.cityforward.meditrack.document.added.v1",
          "data": {"patientId": "1", "encounterId": "IP2610-0142", "fhirBase": "https://attacker.example/fhir"}}
    assert _signed(client, ev).status_code == 200
    events.process_due()
    assert seen == [rt.fhir.base] and "attacker" not in rt.fhir.base


def test_failures_are_retried_with_backoff_then_dead_lettered_and_can_be_replayed(client, rt, clean_db, meditrack, monkeypatch):
    ev = {"specversion": "1.0", "id": "fail-1", "type": "org.cityforward.meditrack.document.added.v1", "data": {"patientId": "1", "encounterId": "IP2610-0142"}}
    _signed(client, ev)
    monkeypatch.setattr(rt.fhir, "everything", lambda pid: (_ for _ in ()).throw(RuntimeError("EHR down")))
    assert events.process_due() == 1
    row = inbox(client, meditrack)[0]
    assert row["status"] == "RETRY" and row["attempts"] == 1 and "EHR down" in row["last_error"]
    assert events.process_due() == 0                                            # not due yet: backoff
    for _ in range(len(events.RETRY_DELAYS) + 1):
        with db.system() as c:
            c.execute("UPDATE event_inbox SET next_try = now() WHERE status='RETRY'")
        events.process_due()
    assert inbox(client, meditrack)[0]["status"] == "DEAD"
    monkeypatch.undo()
    admin = bearer(user_token(meditrack, HALE, user="it.admin")["access_token"])
    assert client.post("/api/events/fail-1/replay", headers=admin).status_code == 200
    assert events.process_due() == 1 and inbox(client, meditrack)[0]["status"] == "DONE"
    assert client.get("/api/events", headers=bearer(user_token(meditrack, HALE, user="nurse.park")["access_token"])).status_code == 403
