import json

import psycopg
import pytest

from copilot import audit, db, rag
from copilot.app import rt as runtime
from copilot.chart import load_chart
from copilot.gateway import Gateway
from tests.conftest import ScriptedLLM, bearer, user_token

HALE, WHITAKER = "IP2610-0142", "IP2610-0138"


@pytest.fixture()
def hale(rt, clean_db, client, meditrack):
    pid = rt.fhir.read("Encounter", HALE)["subject"]["reference"].split("/")[1]
    tok = bearer(user_token(meditrack, HALE)["access_token"])
    assert client.post(f"/api/patients/{pid}/index", headers=tok).status_code == 200
    return pid, tok


def test_access_is_recorded_allowed_and_denied_and_the_chain_verifies(hale, client, meditrack, rt):
    pid, tok = hale
    runtime.gateway = Gateway(None)
    client.post(f"/api/patients/{pid}/ask", headers=tok, json={"question": "aspirin"})
    nurse = bearer(user_token(meditrack, HALE, user="nurse.park")["access_token"])            # wrong unit
    assert client.post(f"/api/patients/{pid}/ask", headers=nurse, json={"question": "aspirin"}).status_code == 403
    pa = bearer(user_token(meditrack, HALE, user="pa.hughes")["access_token"])
    assert client.post(f"/api/patients/{pid}/coding", headers=pa).status_code == 403          # wrong role
    admin = bearer(user_token(meditrack, HALE, user="it.admin")["access_token"])
    rows = client.get("/api/audit", headers=admin, params={"patient": pid}).json()
    seen = {(r["actor"], r["action"], r["outcome"]) for r in rows}
    assert ("attending.raman", "ask", "allowed") in seen and ("attending.raman", "index", "allowed") in seen
    assert ("nurse.park", "ask", "denied") in seen and ("pa.hughes", "coding.suggest", "denied") in seen
    assert ("it.admin", "audit.view", "allowed") in seen                                       # reading the log is itself logged
    assert client.get("/api/audit/verify", headers=admin).json()["ok"] is True
    assert client.get("/api/audit", headers=nurse).status_code == 403                          # clinicians cannot browse the log


def test_audit_rows_hold_ids_and_outcomes_not_clinical_text(hale, client, meditrack, rt):
    pid, tok = hale
    chart = load_chart(rt.fhir, pid)
    runtime.gateway = Gateway(ScriptedLLM({"answer": "x", "citations": [], "insufficient_evidence": True}))
    client.post(f"/api/patients/{pid}/ask", headers=tok, json={"question": "What is Robert Hale's troponin?"})
    with db.session() as c:
        blob = json.dumps([dict(r, ts=str(r["ts"])) for r in c.execute("SELECT * FROM audit_log").fetchall()])
    assert "troponin" not in blob.lower() and "Hale" not in blob and chart.identity.mrn not in blob
    call = [r for r in audit.search(action="ai.call")][0]
    assert call["detail"]["purpose"] == "ask" and call["detail"]["tokens_in"] > 0


def test_database_refuses_to_rewrite_history_and_tampering_is_detected(hale, client, meditrack):
    pid, tok = hale
    admin = bearer(user_token(meditrack, HALE, user="it.admin")["access_token"])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.session() as c:
            c.execute("UPDATE audit_log SET actor='nobody' WHERE id=1")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.session() as c:
            c.execute("DELETE FROM audit_log")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.session() as c:
            c.execute("TRUNCATE audit_log")
    assert client.get("/api/audit/verify", headers=admin).json()["ok"]
    # a DBA who disables the trigger and edits a row is caught by the hash chain
    with db.session() as c:
        c.execute("ALTER TABLE audit_log DISABLE TRIGGER USER")
        c.execute("UPDATE audit_log SET outcome='allowed', action='nothing to see' WHERE id=(SELECT min(id) FROM audit_log)")
        c.execute("ALTER TABLE audit_log ENABLE TRIGGER USER")
    verdict = client.get("/api/audit/verify", headers=admin).json()
    assert verdict["ok"] is False and verdict["first_bad_id"] >= 1
