import re

import pytest

from copilot import db, priorauth_api, rag, summary
from copilot.app import rt as runtime
from copilot.chart import Identity, load_chart
from copilot.gateway import Gateway
from copilot.redact import Redactor, Restorer
from tests.conftest import RuleBasedLLM, ScriptedLLM, bearer, user_token

WHITAKER = "IP2610-0138"


def leaks(ident: Identity, text: str) -> list[str]:
    """Identifiers of the patient that appear verbatim in `text`."""
    probes = [*ident.names, ident.mrn, ident.member_id, ident.street, ident.city, ident.zip, ident.dob, *ident.phones, *ident.emails, *ident.accounts]
    return [x for x in probes if x and len(x) > 2 and re.search(rf"(?<![\w]){re.escape(x)}(?![\w])", text, re.I)]


IDENT = Identity(names=["Robert Hale", "Robert", "Hale"], mrn="CF306377", member_id="W123456789", dob="1964-03-02", street="14 Maple Lane", city="Dublin", zip="43017",
                 phones=["614-555-0192"], accounts=["IP2610-0142"], anchor="2026-10-17")


def test_known_identifiers_dates_and_patterns_are_replaced_and_restorable():
    text = ("Robert Hale (MRN CF306377, DOB 1964-03-02) lives at 14 Maple Lane, Dublin, OH 43017; call 614-555-0192 or 740-555-0111. SSN 999-12-3456. "
            "Admitted 2026-10-17T09:30:00Z, troponin 14250 ng/L on 10/18/2026. Hale is 93 years old. Seen by Dr. Priya Raman and Maria Lopez, RN. Account IP2610-0142, plan id W123456789. "
            "See https://ehr.example.org/x and 10.1.2.3 or a@b.com")
    rs = Restorer()
    out, n = Redactor().apply(text, IDENT, rs)
    assert not leaks(IDENT, out), leaks(IDENT, out)
    for needle in ("999-12-3456", "740-555-0111", "Priya Raman", "Maria Lopez", "ehr.example.org", "10.1.2.3", "a@b.com", "93 years", "2026-10-17", "10/18/2026"):
        assert needle not in out, needle
    assert "[PATIENT]" in out and "[D+0]" in out and "[D+1]" in out and "[DOB]" in out and "90 or older" in out
    assert "14250 ng/L" in out                                  # clinical content survives: that is the point
    back = rs.restore_text(out)
    assert "Robert Hale" in back and "614-555-0192" in back and "Priya Raman" in back and "2026-10-18" in back and "999-12-3456" in back
    assert n >= 15


def test_five_digit_lab_values_are_kept_unless_they_equal_the_patients_own_zip():
    out, _ = Redactor().apply("Troponin 14250 ng/L; sodium 138; platelets 43017 x10^3", IDENT, Restorer())
    assert "14250" in out and "138" in out              # a 5-digit lab value is not mistaken for a zip code
    assert "43017" not in out                           # known limitation, documented: a value equal to the patient's own zip is replaced (errs toward privacy)


@pytest.fixture(scope="module")
def all_charts(rt):
    return [load_chart(rt.fhir, p["id"]) for p in rt.fhir.search("Patient")]


def test_no_prompt_for_any_patient_in_the_hospital_contains_that_patients_identifiers(rt, all_charts):
    """The leakage test: run the real summary pipeline for every patient with a spy model and scan every byte it received."""
    assert len(all_charts) >= 20
    for chart in all_charts:
        spy = ScriptedLLM({"headline": "h", "course": "c", "history": "x", "findings": [{"text": "t", "sources": ["E1"]}], "follow_up": []},
                          {"headline": "h", "course": "c", "history": "x", "findings": [{"text": "t", "sources": ["E1"]}], "follow_up": []})
        summary.summarize(Gateway(spy, redactor=Redactor(), recorder=lambda r: None), chart)
        sent = "\n".join(s + "\n" + u for s, u in spy.prompts)
        assert sent, chart.name
        assert not leaks(chart.identity, sent), (chart.name, leaks(chart.identity, sent))
        assert not re.search(r"\b\d{4}-\d{2}-\d{2}\b", sent), chart.name


def test_redaction_is_invisible_to_the_clinician_end_to_end(rt, payer, clean_db, client, meditrack):
    """Prior auth through the redacting gateway: the model sees placeholders, the draft shows the real statement and date."""
    pid = rt.fhir.read("Encounter", WHITAKER)["subject"]["reference"].split("/")[1]
    spy = RuleBasedLLM({"spo2-qualifying": (r"resting SpO2 87%", r"resting SpO2 (\d+)%"), "face-to-face": (r"face-to-face evaluation", None)})
    runtime.gateway = Gateway(spy, redactor=Redactor(rag.identity_of))
    tok = bearer(user_token(meditrack, WHITAKER, user="pa.hughes")["access_token"])
    rag.index_chart(load_chart(rt.fhir, pid))
    r = client.post(f"/api/patients/{pid}/prior-auth", headers=tok, json={"service_code": "E1390"})
    assert r.status_code == 201, r.text
    sent = "\n".join(u for _, u in spy.prompts)
    ident = rag.identity_of(pid)
    assert not leaks(ident, sent) and "[D" in sent
    q = {x["id"]: x for x in r.json()["requirements"]}["spo2-qualifying"]
    assert q["status"] == "met" and q["value"] == 87.0 and q["measured_on"] == "2026-10-21"        # real date restored locally
    assert "[D" not in q["statement"] and "[PATIENT" not in q["statement"]
    with db.session() as c:
        row = c.execute("SELECT redactions FROM llm_call WHERE purpose='prior-auth'").fetchone()
    assert row["redactions"] > 5


def test_system_prompt_explains_the_placeholders():
    out, rs, n = Redactor().apply_all(["SYSTEM", "Robert Hale"], context=type("C", (), {"identity": IDENT})())
    assert "[PATIENT]" in out[1] and "placeholders" in out[0]
