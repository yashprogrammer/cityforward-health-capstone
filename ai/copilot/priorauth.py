"""Prior-authorization drafting assistant (Build Session 3).

The assistant does the part of the job that eats a coordinator's afternoon: reading the chart against the payer's published policy and
assembling the evidence for each requirement. It does NOT decide, attest or submit. A clinician reviews every line, attests the
statements only a clinician can make, and approves before anything leaves the building.

    resolve (code: diagnoses, midnights) -> retrieve (RAG per requirement) -> extract (LLM, one call) -> verify (code) --ok--> assemble
                                                                                 |  bad, one retry -> extract
                                                                                 |  still bad / offline -> candidates only, flagged

Requirement status:  met | candidate | missing | fails_policy | stale | needs_attestation   (the assistant)
                     accepted | attested | clinician_provided                              (a human acted)
"""
import re
from datetime import date, datetime
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from . import rag
from .chart import Chart
from .gateway import Gateway
from .grounding import check_statement
from .llm import LLMError

RESOLVED = {"met", "accepted", "attested", "clinician_provided"}
MAX_REVISIONS = 1

# What in a chart suggests a service might need prior authorization (the coordinator's pattern-matching, made explicit).
SERVICE_SIGNALS = {
    "SNF-ADM": re.compile(r"skilled nursing facility|\bSNF\b|rehab(ilitation)? placement", re.I),
    "E0143": re.compile(r"\bwalker\b", re.I),
    "E1390": re.compile(r"home oxygen|oxygen concentrator|meets criteria for home oxygen", re.I),
    "G0299": re.compile(r"home health", re.I),
}


# ---------------------------------------------------------------------------------------------- models
class Evidence(BaseModel):
    ref: str
    text: str
    date: str | None = None


class Req(BaseModel):
    id: str
    kind: str
    text: str                         # the payer's wording
    status: str = "missing"
    statement: str = ""               # what we will tell the payer (editable by the clinician)
    value: float | None = None
    unit: str = ""
    measured_on: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    note: str = ""                    # why this status, in plain words
    reviewed_by: str | None = None


class Suggestion(BaseModel):
    service_code: str
    service_name: str
    reason: str
    evidence_ref: str
    pa_required: bool
    plan: str | None


class Draft(BaseModel):
    patient_id: str
    encounter_id: str
    unit: str | None
    service_code: str
    service_name: str
    plan: str | None
    pa_required: bool
    requirements: list[Req]
    diagnoses: list[dict]
    letter: str
    mode: Literal["llm", "deterministic"]
    model: str = ""
    problems: list[str] = Field(default_factory=list)

    @property
    def gaps(self) -> list[Req]:
        return [r for r in self.requirements if r.status not in RESOLVED]


# ---------------------------------------------------------------------------------------------- suggestions
def suggest_services(chart: Chart, payer) -> list[Suggestion]:
    """Services that the chart's own words suggest, filtered to those the patient's plan actually requires authorization for."""
    stay = chart.current_encounter()
    found: dict[str, tuple[str, str]] = {}
    for d in chart.documents:
        if not d.text or (stay and d.encounter != stay.id):
            continue
        for code, rx in SERVICE_SIGNALS.items():
            m = rx.search(d.text)
            if m and code not in found:
                found[code] = (f'{d.kind} {d.date[:10]}: "...{d.text[max(0, m.start() - 40):m.end() + 40].strip()}..."', d.ref)
    out = []
    for code, (why, ref) in found.items():
        rule = payer.rule(code, chart.payer)
        out.append(Suggestion(service_code=code, service_name=rule["name"], reason=why, evidence_ref=ref, pa_required=bool(rule.get("priorAuthRequired")), plan=chart.payer))
    return out


# ---------------------------------------------------------------------------------------------- drafting graph
class Item(BaseModel):
    requirement_id: str
    supported: bool = False
    value: float | None = None
    measured_on: str | None = None
    statement: str = ""
    citations: list[str] = Field(default_factory=list)


class Extraction(BaseModel):
    items: list[Item]


class State(TypedDict, total=False):
    gw: Gateway
    chart: Chart
    principal: object
    rule: dict
    service_date: date
    reqs: dict[str, Req]
    pool: dict[str, object]           # C-id -> rag.Hit
    todo: list[str]                   # requirement ids that need the model
    extraction: Extraction | None
    problems: list[str]
    attempts: int
    note: str


def _midnights(chart: Chart, service_date: date) -> int | None:
    stay = chart.current_encounter()
    return (service_date - date.fromisoformat(stay.start[:10])).days if stay else None


def _resolve(s: State) -> State:
    chart, rule, sd = s["chart"], s["rule"], s["service_date"]
    reqs: dict[str, Req] = {}
    for r in rule["requirements"]:
        req = Req(id=r["id"], kind=r["kind"], text=r["text"])
        if r["kind"] == "diagnosis":
            hits = [c for c in chart.conditions if c.icd10 and c.icd10.startswith(tuple(r["icd_prefixes"]))]
            if hits:
                c = hits[0]
                req.status, req.statement = "met", f"{c.display} (ICD-10-CM {c.icd10})"
                req.evidence = [Evidence(ref=c.ref, text=req.statement, date=(c.onset or "")[:10] or None)]
                if c.status == "provisional":
                    req.status, req.note = "candidate", "Diagnosis is still provisional: the clinician must confirm it."
            else:
                req.note = "No qualifying diagnosis on the problem list."
        elif r["id"] == "qualifying-stay":
            n = _midnights(chart, sd)
            stay = chart.current_encounter()
            if n is not None and stay:
                req.value, req.unit, req.measured_on = float(n), "midnights", stay.start[:10]
                req.statement = f"Inpatient since {stay.start[:10]}: {n} midnights as of {sd.isoformat()}."
                req.evidence = [Evidence(ref=f"Encounter/{stay.id}", text=req.statement, date=stay.start[:10])]
                req.status = "met" if n >= r.get("min", 0) else "fails_policy"
                req.note = "" if req.status == "met" else f"Policy requires at least {r.get('min')} midnights."
        elif r["kind"] == "attestation":
            req.status = "needs_attestation"
            req.note = "Only the treating clinician can attest this. The assistant proposes evidence but never attests."
        reqs[req.id] = req
    todo = [i for i, q in reqs.items() if q.status in ("missing", "needs_attestation") and q.kind != "diagnosis"]
    return {"reqs": reqs, "todo": todo, "attempts": 0, "problems": [], "extraction": None}


def _retrieve(s: State) -> State:
    pool: dict[str, object] = {}
    seen: dict[tuple, str] = {}
    for rid in s["todo"]:
        for h in rag.search(s["chart"].patient_id, s["reqs"][rid].text, k=5, principal=s.get("principal"), kinds=["note", "document", "vital", "lab", "procedure"]):
            key = (h.source_ref, h.text)
            if key not in seen:
                seen[key] = f"C{len(seen) + 1}"
                pool[seen[key]] = h
    return {"pool": pool}


SYSTEM = """You help a hospital prior-authorization coordinator assemble evidence for a payer's medical policy.
For each requirement, decide from the numbered chart passages ONLY whether the chart supports it.
- supported=true only if a passage directly documents it. Otherwise supported=false and leave the statement empty.
- statement: one or two factual sentences quoting numbers and findings exactly as written in the passages. Never infer, never add facts.
- For requirements about a measurement, give value (a number) and measured_on (YYYY-MM-DD) exactly as documented. For room-air or resting
  conditions use only measurements the passage says were taken that way.
- citations: the passage ids that support the statement.
- For attestation requirements, supported=true means the passages contain the evidence a clinician would attest to; you are NOT attesting.
Reply with a single JSON object and nothing else:
{"items": [{"requirement_id": str, "supported": bool, "value": number|null, "measured_on": str|null, "statement": str, "citations": ["C1"]}]}"""


def _prompt(s: State, revise: bool = False) -> str:
    reqs = "\n".join(f'- {rid} ({s["reqs"][rid].kind}): {s["reqs"][rid].text}' for rid in s["todo"])
    pool = "\n".join(f"[{cid}] {h.text}" for cid, h in s["pool"].items())
    body = f"REQUIREMENTS\n{reqs}\n\nCHART PASSAGES\n{pool}"
    if revise and s.get("extraction"):
        body += "\n\nYOUR PREVIOUS ANSWER:\n" + s["extraction"].model_dump_json() + "\n\nIT FAILED THESE CHECKS; return the corrected JSON:\n" + "\n".join(f"- {p}" for p in s["problems"])
    return body


def _extract(s: State, revise: bool = False) -> State:
    if not s["todo"]:
        return {"extraction": Extraction(items=[]), "problems": []}
    try:
        ex = s["gw"].json(SYSTEM, _prompt(s, revise), Extraction, purpose="prior-auth", patient_id=s["chart"].patient_id,
                          actor=getattr(s.get("principal"), "user", None), context=s["chart"])
        return {"extraction": ex, "problems": []}
    except LLMError as e:
        return {"extraction": None, "problems": [str(e)[:200]], "note": f"model unavailable or invalid output: {str(e)[:120]}"}


def _revise(s: State) -> State:
    return {**_extract(s, revise=True), "attempts": s["attempts"] + 1}


def _first(s: State, rid: str):
    return next((i for i in (s["extraction"].items if s.get("extraction") else []) if i.requirement_id == rid), None)


def _apply(s: State) -> tuple[dict[str, Req], list[str]]:
    """Verify each extracted item in code and fold it into the requirement. Returns (requirements, problems)."""
    reqs = {k: v.model_copy(deep=True) for k, v in s["reqs"].items()}
    problems: list[str] = []
    pool, sd = s["pool"], s["service_date"]
    spec = {r["id"]: r for r in s["rule"]["requirements"]}
    ev_text = {cid: h.text for cid, h in pool.items()}
    for rid in s["todo"]:
        req, item = reqs[rid], _first(s, rid)
        if item is None or not item.supported:
            req.note = req.note or "The chart does not document this."
            continue
        bad = check_statement(item.statement, item.citations, ev_text)
        cited = [pool[c] for c in item.citations if c in pool]
        if not bad and req.kind == "value":
            nums = {m for h in cited for m in re.findall(r"(?<![\w.])\d+(?:\.\d+)?", h.text)}
            if item.value is None or f"{item.value:g}" not in nums:
                bad.append(f"{rid}: value {item.value} does not appear in the cited passages")
        if bad:
            problems += [f"{rid}: {b}" for b in bad]
            continue
        req.statement, req.value = item.statement, item.value
        req.evidence = [Evidence(ref=h.source_ref, text=h.text[:400], date=(h.effective or "")[:10] or None) for h in cited]
        when = _iso_date(item.measured_on) or (cited[0].effective or "")[:10] or None
        req.measured_on = when
        r = spec[rid]
        if req.kind == "attestation":
            req.status, req.note = "needs_attestation", "Evidence found; the clinician must attest."
        elif req.kind == "value":
            req.unit = r.get("unit", "")
            if "max" in r and item.value is not None and item.value > r["max"]:
                req.status, req.note = "fails_policy", f"Documented {item.value:g}{req.unit} but policy requires {r['max']:g}{req.unit} or lower: the payer will deny."
            elif "min" in r and item.value is not None and item.value < r["min"]:
                req.status, req.note = "fails_policy", f"Documented {item.value:g} but policy requires at least {r['min']:g}."
            elif "within_days" in r and when and (sd - date.fromisoformat(when)).days > r["within_days"]:
                req.status, req.note = "stale", f"Measurement is from {when}; policy wants one within {r['within_days']} days."
            else:
                req.status = "met"
        else:
            req.status = "met"
    return reqs, problems


def _iso_date(v: str | None) -> str | None:
    try:
        return date.fromisoformat(v[:10]).isoformat() if v else None
    except ValueError:
        return None


def _verify(s: State) -> State:
    if s.get("extraction") is None:
        return {}
    _, problems = _apply(s)
    return {"problems": problems}


def _assemble(s: State) -> State:
    reqs, problems = _apply(s) if s.get("extraction") is not None else (s["reqs"], [])
    llm_ok = s.get("extraction") is not None
    if not llm_ok:
        # no usable model answer: show the best retrieved passage per requirement as an unverified CANDIDATE for the clinician
        for rid in s["todo"]:
            req = reqs[rid]
            best = next((h for h in s["pool"].values() if _overlap(req.text, h.text) > 0.0), None)
            if req.kind != "attestation" and best:
                req.status, req.statement = "candidate", ""
                req.evidence = [Evidence(ref=best.source_ref, text=best.text[:400], date=(best.effective or "")[:10] or None)]
                req.note = "Retrieved by search, not verified by a model: read it and accept or reject."
    return {"reqs": reqs, "problems": problems if llm_ok else s.get("problems", [])}


def _overlap(a: str, b: str) -> float:
    wa = {w for w in re.findall(r"[a-z]{4,}", a.lower())}
    wb = {w for w in re.findall(r"[a-z]{4,}", b.lower())}
    return len(wa & wb) / (len(wa) or 1)


def _after_extract(s: State) -> str:
    return "verify" if s.get("extraction") is not None else "assemble"


def _after_verify(s: State) -> str:
    if not s["problems"] or s["attempts"] >= MAX_REVISIONS:
        return "assemble"
    return "revise"


def _build():
    g = StateGraph(State)
    for name, fn in (("resolve", _resolve), ("retrieve", _retrieve), ("extract", _extract), ("verify", _verify), ("revise", _revise), ("assemble", _assemble)):
        g.add_node(name, fn)
    g.add_edge(START, "resolve")
    g.add_edge("resolve", "retrieve")
    g.add_conditional_edges("retrieve", lambda s: "extract" if s["gw"].online and s["todo"] else "assemble", ["extract", "assemble"])
    g.add_conditional_edges("extract", _after_extract, ["verify", "assemble"])
    g.add_conditional_edges("verify", _after_verify, ["revise", "assemble"])
    g.add_conditional_edges("revise", _after_extract, ["verify", "assemble"])
    g.add_edge("assemble", END)
    return g.compile()


_GRAPH = None


def draft_prior_auth(gw: Gateway, chart: Chart, rule: dict, pa_required: bool, principal=None) -> Draft:
    global _GRAPH
    _GRAPH = _GRAPH or _build()
    stay = chart.current_encounter()
    sd = date.fromisoformat(chart.as_of)    # "today" in the chart's own clock (synthetic data is dated to a fixed demo day)
    out = _GRAPH.invoke({"gw": gw, "chart": chart, "rule": rule, "service_date": sd, "principal": principal})
    reqs = list(out["reqs"].values())
    llm_used = out.get("extraction") is not None and bool(out.get("todo"))
    diagnoses = relevant_diagnoses(chart, rule)
    d = Draft(patient_id=chart.patient_id, encounter_id=stay.id if stay else "", unit=chart.unit, service_code=rule["code"], service_name=rule["name"],
              plan=chart.payer, pa_required=pa_required, requirements=reqs, diagnoses=diagnoses, letter="",
              mode="llm" if llm_used else "deterministic", model=gw.model if llm_used else "", problems=out.get("problems", []))
    d.letter = compose_letter(chart, d)
    return d


def relevant_diagnoses(chart: Chart, rule: dict) -> list[dict]:
    """Claim diagnoses: those that satisfy a diagnosis requirement first, then the stay's other coded diagnoses (max 6)."""
    prefixes = tuple(p for r in rule["requirements"] if r["kind"] == "diagnosis" for p in r["icd_prefixes"])
    coded = [c for c in chart.problem_list() if c.icd10]
    coded.sort(key=lambda c: (not c.icd10.startswith(prefixes) if prefixes else False))
    return [{"code": c.icd10, "display": c.display, "ref": c.ref} for c in coded[:6]]


def compose_letter(chart: Chart, d: Draft) -> str:
    """The letter of medical necessity, assembled from verified statements only (no free-form model prose goes to a payer)."""
    lines = [f"Re: Prior authorization request - {d.service_name} ({d.service_code})",
             f"Patient: {chart.name}   DOB: {chart.birth_date}   Plan: {chart.payer or 'n/a'}   Member ID: {chart.member_id or 'n/a'}",
             f"Hospital account: {d.encounter_id}", "",
             "Diagnoses: " + "; ".join(f"{x['display']} ({x['code']})" for x in d.diagnoses), "",
             "Supporting documentation by policy criterion:"]
    for i, r in enumerate(d.requirements, 1):
        mark = {"met": "Documented", "accepted": "Documented (reviewed)", "attested": "Attested by treating clinician", "clinician_provided": "Provided by clinician"}.get(r.status, "NOT YET DOCUMENTED")
        lines.append(f"{i}. {r.text}\n   {mark}." + (f" {r.statement}" if r.statement and r.status in RESOLVED else ""))
        for e in r.evidence[:2]:
            if r.status in RESOLVED:
                lines.append(f"   Source: {e.ref}" + (f", {e.date}" if e.date else ""))
    lines += ["", "Sincerely,", "(treating clinician signature on approval)"]
    return "\n".join(lines)


def refresh_letter(chart: Chart, d: Draft) -> str:
    return compose_letter(chart, d)


# ---------------------------------------------------------------------------------------------- the PAS claim bundle
def build_pas_bundle(chart: Chart, d: Draft, claim_identifier: str) -> dict:
    sd = chart.as_of
    info = []
    for n, r in enumerate(d.requirements, 1):
        if r.status not in RESOLVED:
            continue
        si: dict = {"sequence": n, "category": {"coding": [{"system": "https://cityforward.example/pa-requirement", "code": r.id}]}}
        if r.kind == "value" and r.value is not None:
            si["valueQuantity"] = {"value": r.value, "unit": r.unit}
            si["timingDate"] = r.measured_on or sd
            if r.statement:
                si["valueString"] = r.statement
        elif r.kind == "attestation":
            si["valueBoolean"] = r.status == "attested"
        elif r.kind == "diagnosis":
            continue
        else:
            si["valueString"] = r.statement
        info.append(si)
    claim = {"resourceType": "Claim", "id": claim_identifier, "status": "active",
             "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/claim-type", "code": "professional"}]}, "use": "preauthorization",
             "identifier": [{"system": "https://cityforward.example/pa-request", "value": claim_identifier}],
             "patient": {"reference": f"Patient/{chart.patient_id}", "display": chart.name}, "created": sd,
             "insurer": {"display": chart.payer}, "priority": {"coding": [{"code": "normal"}]},
             "insurance": [{"sequence": 1, "focal": True, "coverage": {"display": chart.payer}}],
             "diagnosis": [{"sequence": i + 1, "diagnosisCodeableConcept": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": x["code"], "display": x["display"]}]}}
                           for i, x in enumerate(d.diagnoses)],
             "item": [{"sequence": 1, "productOrService": {"coding": [{"system": "https://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets", "code": d.service_code, "display": d.service_name}]},
                       "servicedDate": sd}],
             "supportingInfo": info}
    coverage = {"resourceType": "Coverage", "status": "active", "subscriberId": chart.member_id, "beneficiary": {"reference": f"Patient/{chart.patient_id}"},
                "payor": [{"display": chart.payer}]}
    return {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": claim}, {"resource": coverage}]}


def packet_pdf(chart: Chart, d: Draft, decision: str = "") -> bytes:
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 9, "CityForward Health - Prior Authorization Packet", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for line in d.letter.split("\n"):
        pdf.multi_cell(0, 5.5, line.encode("latin-1", "replace").decode("latin-1"), new_x="LMARGIN", new_y="NEXT")
    if decision:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 10)
        pdf.multi_cell(0, 6, f"Payer decision: {decision}", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
