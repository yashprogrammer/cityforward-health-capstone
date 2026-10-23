"""Outbound webhooks: signed CloudEvents, outbox, retries, dead letter, replay."""
import hashlib
import hmac
import json
import time

import httpx
import pytest

from meditrack.backend import events
from meditrack.backend.db import connect_once

SECRET = "whsec-test"


@pytest.fixture(autouse=True, scope="module")
def pristine(seeded):
    from meditrack.backend.seed import seed
    seed()
    yield


@pytest.fixture()
def subscription(client):
    with connect_once() as c:
        c.execute("DELETE FROM SYS_WEBHOOK"); c.execute("DELETE FROM SYS_WH_OUT"); c.commit()
    r = client.post("/int/admin/webhooks", json={"name": "test", "url": "http://receiver.test/hooks", "secret": SECRET, "events": "encounter.discharge-advised,document.added"})
    assert r.status_code == 200
    return r.json()["id"]


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_event_is_queued_with_the_change_and_delivered_signed(client, subscription):
    assert client.post("/int/ip/IP2610-0142/discharge-advise").status_code == 200
    out = client.get("/int/admin/webhooks/outbox").json()
    assert [o["STS"] for o in out] == ["PENDING"] and out[0]["EVT_TYP"] == "org.cityforward.meditrack.encounter.discharge-advised.v1"
    seen = []

    def receiver(request: httpx.Request):
        seen.append(request)
        return httpx.Response(204)

    assert events.deliver_due(_client(receiver)) == 1
    req = seen[0]
    body = req.content.decode()
    ts = dict(p.split("=") for p in req.headers["x-meditrack-signature"].split(","))
    expected = hmac.new(SECRET.encode(), f"{ts['t']}.{body}".encode(), hashlib.sha256).hexdigest()
    assert hmac.compare_digest(ts["v1"], expected)
    ce = json.loads(body)
    assert ce["specversion"] == "1.0" and ce["subject"] == "Encounter/IP2610-0142" and req.headers["content-type"] == "application/cloudevents+json"
    assert set(ce["data"]) == {"patientId", "encounterId", "unit", "fhirBase"}      # IDs only
    assert "Hale" not in body and "CF306377" not in body                           # no patient data in the event
    assert client.get("/int/admin/webhooks/outbox").json()[0]["STS"] == "SENT"


def test_only_subscribed_event_types_are_queued(client, subscription):
    client.post("/int/ip/IP2610-0138/discharge-advise")
    client.post("/int/ip/IP2610-0138/discharge", json={"disposition": "HOME"})   # encounter.discharged is not subscribed
    types = [o["EVT_TYP"].split(".meditrack.")[1] for o in client.get("/int/admin/webhooks/outbox").json()]
    assert types == ["encounter.discharge-advised.v1"]


def test_retries_back_off_then_dead_letter_then_replay(client, subscription):
    client.post("/int/ip/IP2610-0151/discharge-advise")
    calls = []

    def failing(request):
        calls.append(1)
        return httpx.Response(503)

    http = _client(failing)
    for attempt in range(1, len(events.RETRY_DELAYS) + 2):
        assert events.deliver_due(http) == 1
        row = client.get("/int/admin/webhooks/outbox").json()[0]
        assert row["ATTEMPTS"] == attempt and row["LAST_ERR"] == "HTTP 503"
        if attempt <= len(events.RETRY_DELAYS):
            assert row["STS"] == "PENDING"
            assert events.deliver_due(http) == 0          # not due yet: backing off
            with connect_once() as c:                     # fast-forward the clock
                c.execute("UPDATE SYS_WH_OUT SET NEXT_TRY = 0"); c.commit()
    assert row["STS"] == "FAILED"                          # dead letter after the retry budget
    ok = []
    assert client.post(f"/int/admin/webhooks/outbox/{row['ID']}/replay").status_code == 200
    assert events.deliver_due(_client(lambda r: (ok.append(1), httpx.Response(200))[1])) == 1 and ok
    assert client.get("/int/admin/webhooks/outbox").json()[0]["STS"] == "SENT"


def test_network_errors_are_retried_too(client, subscription):
    client.post("/int/ip/IP2610-0204/discharge-advise")

    def down(request):
        raise httpx.ConnectError("refused")

    events.deliver_due(_client(down))
    row = client.get("/int/admin/webhooks/outbox").json()[0]
    assert row["STS"] == "PENDING" and "ConnectError" in row["LAST_ERR"]


def test_document_added_event_carries_the_doc_type_only(client, subscription):
    from conftest import backend_token
    # a document upload through the legacy UI path
    client.post("/int/ip/IP2610-0151/docs", data={"doc_typ": "DEVICE_LOG"}, files={"file": ("x.pdf", b"%PDF-1.4 test", "application/pdf")})
    ce = json.loads(client.get("/int/admin/webhooks/outbox").json() and _payload("document.added"))
    assert ce["data"]["docType"] == "DEVICE_LOG" and ce["data"]["encounterId"] == "IP2610-0151"


def _payload(kind: str) -> str:
    with connect_once() as c:
        return c.execute("SELECT PAYLOAD FROM SYS_WH_OUT WHERE EVT_TYP LIKE %s ORDER BY ID DESC LIMIT 1", (f"%{kind}%",)).fetchone()["PAYLOAD"]
