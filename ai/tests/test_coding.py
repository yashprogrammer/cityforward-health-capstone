import json
import re

import pytest

from copilot import coding, terminology as T
from copilot.app import rt as runtime
from copilot.chart import load_chart
from copilot.gateway import Gateway
from copilot.llm import Reply
from tests.conftest import bearer, user_token

WHITAKER, DOYLE = "IP2610-0138", "IP2610-0151"


class CodingFake:
    """Plays the model: proposes codes with quotes taken from the evidence it is shown. `plan`: list of (code, role, regex for the evidence line, quote regex)."""
    name = "codingfake:test"

    def __init__(self, plan, second=None):
        self.plan, self.second, self.prompts = plan, second, []

    def chat(self, system, user):
        self.prompts.append(user)
        plan = self.second if (self.second and len(self.prompts) > 1) else self.plan
        lines = dict(re.findall(r"^\[(E\d+)\] (.*)$", user.split("\n\nYOUR PREVIOUS")[0], flags=re.M))
        out = []
        for code, role, ev_rx, q_rx in plan:
            eid = next((e for e, t in lines.items() if re.search(ev_rx, t, re.I) and re.search(q_rx, t, re.I)), None)
            if eid is None:
                continue
            m = re.search(q_rx, lines[eid], re.I)
            out.append({"code": code, "role": role, "evidence": [eid], "quote": m.group(0) if m else "not in evidence", "rationale": "documented"})
        text = json.dumps({"codes": out})
        return Reply(text, tokens_in=len(user) // 4, tokens_out=len(text) // 4)


PLAN_COPD = [("J44.1", "principal", r"exacerbation", r"acute exacerbation"),
             ("J96.01", "secondary", r"hypoxic respiratory failure", r"hypoxic respiratory failure"),
             ("F17.210", "secondary", r"current smoker", r"current smoker")]


def pid_of(rt, ip):
    return rt.fhir.read("Encounter", ip)["subject"]["reference"].split("/")[1]


def test_terminology_checks():
    assert T.billable("J96.01") and not T.billable("J96.0") and not T.valid("J99.99")
    assert T.excludes1_conflict("R09.02", "J96.01") and not T.excludes1_conflict("J44.1", "J96.01")
    assert T.is_unspecified("J44.9") and not T.is_unspecified("J44.1")


def test_check_proposal_rejects_invented_codes_and_quotes():
    from copilot.chart import Fact
    facts = {"E1": Fact(ref="DocumentReference/n1", kind="note", when=None, text="Progress note: admitted with acute exacerbation of COPD and hypoxic respiratory failure.")}
    p = coding.Proposal(codes=[
        coding.Proposed(code="J44.1", evidence=["E1"], quote="acute exacerbation of COPD"),
        coding.Proposed(code="J96.0", evidence=["E1"], quote="hypoxic respiratory failure"),      # not billable
        coding.Proposed(code="J99.99", evidence=["E1"], quote="hypoxic respiratory failure"),     # not a code
        coding.Proposed(code="J18.9", evidence=["E1"], quote="community acquired pneumonia"),     # quote not in the evidence
        coding.Proposed(code="I10", evidence=["E9"], quote="hypertension noted here"),            # evidence id does not exist
    ])
    ok, problems = coding.check_proposal(p, facts)
    assert [c for _, c in ok] == ["J44.1"] and len(problems) == 4
    assert any("more specific" in x for x in problems)


@pytest.fixture()
def api(rt, payer, clean_db, meditrack, client):
    return client


def test_worksheet_flags_documentation_queries_and_needs_human_decisions(api, rt, meditrack):
    pid = pid_of(rt, WHITAKER)
    coder, att = bearer(user_token(meditrack, WHITAKER, user="him.patel")["access_token"]), bearer(user_token(meditrack, WHITAKER, user="attending.okafor")["access_token"])
    runtime.gateway = Gateway(CodingFake(PLAN_COPD))
    r = api.post(f"/api/patients/{pid}/coding", headers=coder)
    assert r.status_code == 201, r.text
    ws = r.json()
    by = {i["code"]: i for i in ws["items"]}
    assert set(by) >= {"J44.1", "J96.01", "F17.210", "R09.02"}
    assert by["J44.1"]["origin"] == "model" and by["J44.1"]["description"] == T.describe("J44.1")          # official description, not the model's
    assert by["J44.1"]["status"] == "confirms_existing" and by["F17.210"]["status"] == "suggested"   # chronic condition coded on an earlier encounter: new for this stay
    assert any("Excludes1" in f for f in by["R09.02"]["flags"]) and any("Excludes1" in f for f in by["J96.01"]["flags"])
    assert any("cannot be reported together" in q for q in ws["queries"])                                    # a CDI query for the clinician
    assert ws["open_items"] and ws["status"] == "OPEN"
    # cannot finalize with undecided lines; reject needs a reason
    wid = ws["id"]
    assert api.post(f"/api/coding/{wid}/finalize", headers=coder, json={"principal_item": by["J44.1"]["id"]}).status_code == 409
    assert api.post(f"/api/coding/{wid}/items/{by['R09.02']['id']}", headers=coder, json={"action": "reject"}).status_code == 422
    for code, i in by.items():
        body = {"action": "reject", "reason": "integral to J96.01 (Excludes1)"} if code == "R09.02" else {"action": "accept"}
        assert api.post(f"/api/coding/{wid}/items/{i['id']}", headers=coder, json=body).status_code == 200
    # a nurse cannot code; a resident cannot sign off codes
    assert api.post(f"/api/coding/{wid}/finalize", headers=bearer(user_token(meditrack, WHITAKER, user="nurse.park")["access_token"]), json={"principal_item": by["J44.1"]["id"]}).status_code == 403
    assert api.post(f"/api/coding/{wid}/finalize", headers=bearer(user_token(meditrack, WHITAKER, user="res.etran")["access_token"]), json={"principal_item": by["J44.1"]["id"]}).status_code == 403
    done = api.post(f"/api/coding/{wid}/finalize", headers=att, json={"principal_item": by["J44.1"]["id"]})
    assert done.status_code == 200, done.text
    fc = done.json()["final_codes"]
    assert fc[0]["code"] == "J44.1" and fc[0]["role"] == "principal" and {c["code"] for c in fc} == {"J44.1", "J96.01", "F17.210"}
    docs = rt.fhir.search("DocumentReference", patient=pid)
    assert any("CODING_SUMMARY" in json.dumps(d.get("type")) for d in docs)
    chart = load_chart(rt.fhir, pid)       # what we filed is never fed back to the model as evidence
    assert any(d.generated for d in chart.documents) and not any("CODING_SUMMARY" in f.text for f in chart.facts())


def test_cannot_finalize_a_conflicting_code_set(api, rt, meditrack):
    pid = pid_of(rt, WHITAKER)
    coder = bearer(user_token(meditrack, WHITAKER, user="him.patel")["access_token"])
    runtime.gateway = Gateway(CodingFake(PLAN_COPD))
    ws = api.post(f"/api/patients/{pid}/coding", headers=coder).json()
    for i in ws["items"]:
        api.post(f"/api/coding/{ws['id']}/items/{i['id']}", headers=coder, json={"action": "accept"})        # accept everything, including R09.02 + J96.01
    pr = next(i["id"] for i in ws["items"] if i["code"] == "J44.1")
    r = api.post(f"/api/coding/{ws['id']}/finalize", headers=coder, json={"principal_item": pr})
    assert r.status_code == 409 and "Excludes1" in r.json()["detail"]


def test_modify_validates_the_new_code(api, rt, meditrack):
    pid = pid_of(rt, WHITAKER)
    coder = bearer(user_token(meditrack, WHITAKER, user="him.patel")["access_token"])
    runtime.gateway = Gateway(None)                                                                          # chart-only mode
    ws = api.post(f"/api/patients/{pid}/coding", headers=coder).json()
    assert ws["mode"] == "chart-only" and all(i["origin"] == "chart" for i in ws["items"])
    item = ws["items"][0]
    assert api.post(f"/api/coding/{ws['id']}/items/{item['id']}", headers=coder, json={"action": "modify", "code": "J96.0", "reason": "more specific"}).status_code == 422
    ok = api.post(f"/api/coding/{ws['id']}/items/{item['id']}", headers=coder, json={"action": "modify", "code": "J44.1", "reason": "acute exacerbation documented"})
    assert ok.status_code == 200 and ok.json()["items"][0]["final_code"] == "J44.1"


def test_a_bad_first_answer_is_corrected_once(api, rt, meditrack):
    pid = pid_of(rt, WHITAKER)
    coder = bearer(user_token(meditrack, WHITAKER, user="him.patel")["access_token"])
    fake = CodingFake([("J96.0", "secondary", r"hypoxic respiratory failure", r"hypoxic respiratory failure"), ("J44.1", "principal", r"exacerbation", r"acute exacerbation")],
                      second=[("J96.01", "secondary", r"hypoxic respiratory failure", r"hypoxic respiratory failure"), ("J44.1", "principal", r"exacerbation", r"acute exacerbation")])
    runtime.gateway = Gateway(fake)
    ws = api.post(f"/api/patients/{pid}/coding", headers=coder).json()
    assert len(fake.prompts) == 2 and "not billable" in fake.prompts[1]
    assert "J96.01" in {i["code"] for i in ws["items"]}
