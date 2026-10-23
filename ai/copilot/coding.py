"""ICD-10-CM coding assistant with the coder in the loop (Course lecture: coding with a clinician in the loop).

The model PROPOSES codes from the documentation. Code then decides what may be shown:
  * the code must exist in ICD-10-CM and be billable (most specific level)                    -> else sent back once, then dropped
  * the quote the model gives must literally appear in the cited chart text                   -> else dropped
  * descriptions always come from the official code set, never from the model
  * Excludes1 pairs, unspecified codes and provisional diagnoses are flagged as documentation queries
A coder or the attending accepts, rejects (reason) or modifies every line, and picks the principal diagnosis. Nothing is final without them.
"""
import re
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from . import terminology as T
from .chart import Chart
from .gateway import Gateway
from .llm import LLMError
from .summary import select_facts

MAX_REVISIONS = 1


class Proposed(BaseModel):
    code: str
    role: Literal["principal", "secondary"] = "secondary"
    evidence: list[str] = Field(default_factory=list)
    quote: str = ""
    rationale: str = ""


class Proposal(BaseModel):
    codes: list[Proposed]


class Source(BaseModel):
    ref: str
    text: str


class Item(BaseModel):
    id: str
    code: str
    description: str
    role: str = "secondary"
    status: str = "suggested"      # suggested | confirms_existing | refines_existing | existing | existing_not_supported
    origin: str = "model"          # model | chart
    existing_code: str | None = None
    evidence: list[Source] = Field(default_factory=list)
    quote: str = ""
    rationale: str = ""
    flags: list[str] = Field(default_factory=list)
    decision: str | None = None    # accepted | rejected | modified
    final_code: str | None = None
    reason: str = ""
    decided_by: str | None = None


class Worksheet(BaseModel):
    patient_id: str
    encounter_id: str
    unit: str | None
    items: list[Item]
    queries: list[str]
    mode: Literal["llm", "chart-only"]
    model: str = ""
    problems: list[str] = Field(default_factory=list)


class State(TypedDict, total=False):
    gw: Gateway
    chart: Chart
    principal: object
    facts: dict
    proposal: Proposal | None
    problems: list[str]
    attempts: int
    valid: list
    note: str


SYSTEM = """You are a clinical coding assistant. From the numbered chart evidence, propose ICD-10-CM diagnosis codes for this hospital stay.
Rules: use only conditions the clinicians documented (never infer one from a lab value alone); give the most specific code the documentation supports;
mark exactly one code as principal (the condition mainly responsible for the admission). For each code give the evidence ids and a short verbatim
quote (8-200 characters) copied exactly from that evidence, plus a one-sentence rationale. Do not code symptoms that are integral to a documented diagnosis.
Reply with a single JSON object and nothing else:
{"codes": [{"code": "J44.1", "role": "principal", "evidence": ["E3"], "quote": "exact words from the evidence", "rationale": str}]}"""


def _norm_text(t: str) -> str:
    return re.sub(r"\s+", " ", t.lower()).strip()


def _prompt(s: State, revise: bool = False) -> str:
    body = "EVIDENCE\n" + "\n".join(f"[{k}] {f.text}" for k, f in s["facts"].items())
    if revise:
        body += "\n\nYOUR PREVIOUS ANSWER:\n" + s["proposal"].model_dump_json() + "\n\nIT FAILED THESE CHECKS; return the corrected JSON:\n" + "\n".join(f"- {p}" for p in s["problems"])
    return body


def _context(s: State) -> State:
    return {"facts": select_facts(s["chart"], limit=70), "attempts": 0, "problems": [], "proposal": None, "valid": []}


def _call(s: State, revise: bool = False) -> State:
    try:
        p = s["gw"].json(SYSTEM, _prompt(s, revise), Proposal, purpose="coding", patient_id=s["chart"].patient_id,
                         actor=getattr(s.get("principal"), "user", None), context=s["chart"])
        return {"proposal": p}
    except LLMError as e:
        return {"proposal": None, "problems": [str(e)[:200]], "note": f"model unavailable or invalid output: {str(e)[:120]}"}


def _suggest(s: State) -> State:
    return _call(s)


def _revise(s: State) -> State:
    return {**_call(s, revise=True), "attempts": s["attempts"] + 1}


def check_proposal(p: Proposal, facts: dict) -> tuple[list[tuple[Proposed, str]], list[str]]:
    """(accepted items with normalized code, problems). Pure function: this is where the model is not trusted."""
    ok, problems, seen = [], [], set()
    for it in p.codes:
        code = T.pretty(it.code.strip())
        if not T.valid(code):
            problems.append(f"{it.code}: not an ICD-10-CM code")
            continue
        if not T.billable(code):
            kids = ", ".join(T.children(code)[:6])
            problems.append(f"{code}: not billable, a more specific code is required" + (f" (children: {kids})" if kids else ""))
            continue
        bad = [e for e in it.evidence if e not in facts]
        cited = [facts[e].text for e in it.evidence if e in facts]
        if bad or not cited:
            problems.append(f"{code}: evidence ids {bad or '(none)'} do not exist")
            continue
        q = _norm_text(it.quote)
        if len(q) < 8 or not any(q in _norm_text(c) for c in cited):
            problems.append(f"{code}: the quote does not appear verbatim in the cited evidence")
            continue
        if code in seen:
            continue
        seen.add(code)
        ok.append((it.model_copy(update={"code": code}), code))
    return ok, problems


def _validate(s: State) -> State:
    if s.get("proposal") is None:
        return {}
    ok, problems = check_proposal(s["proposal"], s["facts"])
    return {"valid": ok, "problems": problems}


def _after_suggest(s: State) -> str:
    return "validate" if s.get("proposal") is not None else "finish"


def _after_validate(s: State) -> str:
    return "revise" if s["problems"] and s["attempts"] < MAX_REVISIONS else "finish"


def _finish(s: State) -> State:
    return {}


def _build():
    g = StateGraph(State)
    for name, fn in (("context", _context), ("suggest", _suggest), ("validate", _validate), ("revise", _revise), ("finish", _finish)):
        g.add_node(name, fn)
    g.add_edge(START, "context")
    g.add_conditional_edges("context", lambda s: "suggest" if s["gw"].online else "finish", ["suggest", "finish"])
    g.add_conditional_edges("suggest", _after_suggest, ["validate", "finish"])
    g.add_conditional_edges("validate", _after_validate, ["revise", "finish"])
    g.add_conditional_edges("revise", _after_suggest, ["validate", "finish"])
    g.add_edge("finish", END)
    return g.compile()


_GRAPH = None


def build_worksheet(gw: Gateway, chart: Chart, principal=None) -> Worksheet:
    global _GRAPH
    _GRAPH = _GRAPH or _build()
    out = _GRAPH.invoke({"gw": gw, "chart": chart, "principal": principal})
    stay = chart.current_encounter()
    facts = out["facts"]
    existing = [c for c in chart.conditions if c.icd10 and stay and c.encounter == stay.id]
    llm_used = out.get("proposal") is not None
    items: list[Item] = []
    for it, code in out.get("valid", []):
        ev = [Source(ref=facts[e].ref, text=facts[e].text[:400]) for e in it.evidence if e in facts]
        item = Item(id="", code=code, description=T.describe(code), role=it.role, evidence=ev, quote=it.quote.strip(), rationale=it.rationale)
        same = next((c for c in existing if T.norm(c.icd10) == T.norm(code)), None)
        near = next((c for c in existing if T.norm(c.icd10)[:3] == T.norm(code)[:3]), None)
        if same:
            item.status, item.existing_code = "confirms_existing", same.icd10
        elif near:
            item.status, item.existing_code = "refines_existing", near.icd10
        items.append(item)
    covered = {T.norm(i.code) for i in items} | {T.norm(i.existing_code) for i in items if i.existing_code}
    for c in existing:                       # codes already in the EHR that nothing above covers
        if T.norm(c.icd10) in covered:
            continue
        if not T.valid(c.icd10):
            continue
        items.append(Item(id="", code=T.pretty(c.icd10), description=T.describe(c.icd10), origin="chart", status="existing" if not llm_used else "existing_not_supported",
                          evidence=[Source(ref=c.ref, text=f"Diagnosis: {c.display} [ICD-10-CM {c.icd10}]")],
                          flags=["provisional in the EHR"] if c.status == "provisional" else []))
    flag_and_query(items, existing)
    for n, i in enumerate(items, 1):
        i.id = f"i{n}"
    queries = _queries(items)
    return Worksheet(patient_id=chart.patient_id, encounter_id=stay.id if stay else "", unit=chart.unit, items=items, queries=queries,
                     mode="llm" if llm_used else "chart-only", model=gw.model if llm_used else "", problems=out.get("problems", []))


def flag_and_query(items: list[Item], existing) -> None:
    prov = {T.norm(c.icd10) for c in existing if c.status == "provisional"}
    for i in items:
        if T.is_unspecified(i.code):
            i.flags.append("unspecified code: ask the clinician for more detail")
        if T.norm(i.code) in prov or (i.existing_code and T.norm(i.existing_code) in prov):
            if "provisional in the EHR" not in i.flags:
                i.flags.append("provisional in the EHR")
    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            if T.excludes1_conflict(items[a].code, items[b].code):
                msg = f"Excludes1 conflict with {items[b].code}"
                items[a].flags.append(msg)
                items[b].flags.append(f"Excludes1 conflict with {items[a].code}")


def _queries(items: list[Item]) -> list[str]:
    q = []
    for i in items:
        for f in i.flags:
            if f.startswith("unspecified"):
                q.append(f"{i.code} {i.description}: documentation is non-specific. Can the treating clinician document the type/acuity/site?")
            elif f.startswith("provisional"):
                q.append(f"{i.code} {i.description}: documented as provisional in the EHR. Is the diagnosis confirmed, ruled out, or still uncertain at discharge?")
            elif f.startswith("Excludes1"):
                other = f.rsplit(" ", 1)[-1]
                if i.code < other:       # one query per pair
                    q.append(f"{i.code} and {other} cannot be reported together (ICD-10-CM Excludes1). Which condition is correct?")
    return list(dict.fromkeys(q))


def worksheet_pdf(w: Worksheet, patient_name: str, final_codes: list[dict]) -> bytes:
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 9, "CityForward Health - ICD-10-CM Coding Worksheet", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for line in [f"Patient: {patient_name}   Account: {w.encounter_id}", "", "Final code set (coder-approved):"] + \
            [f"  {'PRINCIPAL' if c['role'] == 'principal' else 'secondary '}  {c['code']}  {c['description']}" for c in final_codes]:
        pdf.multi_cell(0, 5.5, line.encode("latin-1", "replace").decode("latin-1"), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
