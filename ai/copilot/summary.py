"""Hospital-stay summary for the clinician taking over care (Build Session 1's graph, now grounded in the real chart and cited by FHIR reference).

    build_context -> summarize -> ground --ok--> done
                                    |  bad, one retry -> revise -> ground
                                    |  bad again / model down -> template

Every statement cites evidence ids (E1..); the result maps each id back to the FHIR resource, which is what the dashboard's "Why" panel shows.
"""
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from .chart import Chart, Fact
from .gateway import Gateway
from . import cache, cds
from .grounding import check_statement
from .llm import LLMError

MAX_REVISIONS = 1
PROMPT_VERSION = "summary-v1"      # bump when SYSTEM or the evidence format changes: it is part of the cache key


class Finding(BaseModel):
    text: str
    sources: list[str] = Field(default_factory=list)


class Draft(BaseModel):
    headline: str
    course: str = Field(description="what happened this stay, 2-4 sentences")
    history: str = Field(description="relevant earlier history, 1-3 sentences")
    findings: list[Finding] = Field(default_factory=list)
    follow_up: list[Finding] = Field(default_factory=list, description="open items a clinician should check")


class Source(BaseModel):
    id: str
    ref: str
    kind: str
    text: str


class SummaryResult(BaseModel):
    patient_id: str
    encounter_id: str | None
    summary: Draft
    sources: dict[str, Source]
    mode: Literal["llm", "template"]
    model: str = ""
    problems: list[str] = Field(default_factory=list)
    note: str = ""
    cached: bool = False


class State(TypedDict, total=False):
    gw: Gateway
    chart: Chart
    principal: object
    facts: dict[str, Fact]
    draft: Draft | None
    problems: list[str]
    attempts: int
    note: str
    result: SummaryResult


def select_facts(chart: Chart, limit: int = 60) -> dict[str, Fact]:
    """Choose what the model sees: the current stay in full, history only as diagnoses and meds. Numbered E1.."""
    stay = chart.current_encounter()
    sid = stay.id if stay else None
    keep: list[Fact] = []
    for f in chart.facts(note_chars=900):
        if f.kind in ("patient", "diagnosis", "procedure"):
            keep.append(f)
        elif f.kind == "medication" and "active" in f.text:
            keep.append(f)
        elif f.kind == "encounter" and f.encounter == sid:
            keep.append(f)
        elif f.kind in ("lab", "vital", "note", "document") and (f.encounter == sid or sid is None):
            keep.append(f)
    keep.sort(key=lambda f: (["patient", "encounter", "diagnosis", "procedure", "medication", "lab", "vital", "note", "document"].index(f.kind), f.when or ""))
    notes = [f for f in keep if f.kind in ("note", "document")][-8:]
    keep = [f for f in keep if f.kind not in ("note", "document")] + notes
    seen, out = set(), []
    for f in keep:
        if f.text not in seen:
            seen.add(f.text)
            out.append(f)
    return {f"E{i + 1}": f for i, f in enumerate(out[:limit])}


SYSTEM = """You summarize a hospital patient's chart for the clinician taking over care.
Use ONLY the numbered evidence. Never invent a fact, dose, date or number. Cite the evidence ids that support every statement.
Put anything important that is missing or unresolved (a pending result, no documented plan) under follow_up instead of guessing.
Reply with a single JSON object and nothing else:
{"headline": str, "course": str, "history": str, "findings": [{"text": str, "sources": ["E1"]}], "follow_up": [{"text": str, "sources": ["E1"]}]}"""


def _prompt(s: State, revise: bool = False) -> str:
    body = "EVIDENCE\n" + "\n".join(f"[{k}] {f.text}" for k, f in s["facts"].items())
    if revise:
        body += "\n\nYOUR PREVIOUS DRAFT:\n" + s["draft"].model_dump_json() + "\n\nIT FAILED THESE CHECKS; fix and return corrected JSON:\n" + "\n".join(f"- {p}" for p in s["problems"])
    return body


def _context(s: State) -> State:
    return {"facts": select_facts(s["chart"]), "attempts": 0, "problems": [], "draft": None}


def _call(s: State, revise: bool = False) -> State:
    try:
        d = s["gw"].json(SYSTEM, _prompt(s, revise), Draft, purpose="summary", patient_id=s["chart"].patient_id,
                         actor=getattr(s.get("principal"), "user", None), context=s["chart"])
        return {"draft": d, "problems": []}
    except LLMError as e:
        return {"draft": None, "problems": [str(e)[:200]], "note": f"model unavailable or invalid output: {str(e)[:120]}"}


def _summarize(s: State) -> State:
    return _call(s)


def _revise(s: State) -> State:
    return {**_call(s, revise=True), "attempts": s["attempts"] + 1}


def _ground(s: State) -> State:
    d = s.get("draft")
    if d is None:
        return {}
    ev = {k: f.text for k, f in s["facts"].items()}
    problems = [] if d.findings else ["no findings were given"]
    for st in d.findings + d.follow_up:
        problems += check_statement(st.text, st.sources, ev) + cds.lint(st.text)
    problems += cds.lint(f"{d.headline}. {d.course}. {d.history}")
    return {"problems": problems}


def _sources(s: State) -> dict[str, Source]:
    return {k: Source(id=k, ref=f.ref, kind=f.kind, text=f.text) for k, f in s["facts"].items()}


def _done(s: State) -> State:
    stay = s["chart"].current_encounter()
    return {"result": SummaryResult(patient_id=s["chart"].patient_id, encounter_id=stay.id if stay else None, summary=s["draft"], sources=_sources(s),
                                    mode="llm", model=s["gw"].model)}


def template_draft(chart: Chart, facts: dict[str, Fact]) -> Draft:
    """What a plain script produces from the same evidence: boring, and always grounded."""
    by_kind = lambda k: [(i, f) for i, f in facts.items() if f.kind == k]   # noqa: E731
    dx = [f.text.removeprefix("Diagnosis: ").split(" [")[0] for _, f in by_kind("diagnosis")]
    stay = [(i, f) for i, f in by_kind("encounter")][-1:]
    pending = [(i, f) for i, f in by_kind("lab") if "NO RESULT YET" in f.text]
    findings = [Finding(text=f.text, sources=[i]) for i, f in by_kind("diagnosis")[:4] + by_kind("procedure")[:3] + by_kind("lab")[:4]]
    return Draft(
        headline=f"{chart.name}, {chart.age}{chart.sex}: " + (", ".join(dx[:3]) if dx else "no diagnoses on file"),
        course=stay[0][1].text if stay else "No hospital stay in the chart.", history=f"{len(chart.problem_list())} distinct diagnoses and {len(chart.active_medications())} active medications on file.",
        findings=findings or [Finding(text="No structured findings available.", sources=["E1"])],
        follow_up=[Finding(text=f.text, sources=[i]) for i, f in pending] or [Finding(text="Template summary (no AI used): a clinician must review the chart.", sources=["E1"])])


def _template(s: State) -> State:
    note = s.get("note") or ("no model configured" if not s["gw"].online else "the model's summary failed grounding checks twice")
    stay = s["chart"].current_encounter()
    return {"result": SummaryResult(patient_id=s["chart"].patient_id, encounter_id=stay.id if stay else None, summary=template_draft(s["chart"], s["facts"]),
                                    sources=_sources(s), mode="template", model=s["gw"].model, problems=s.get("problems", []), note=note)}


def _after_context(s: State) -> str:
    return "summarize" if s["gw"].online else "template"


def _after_ground(s: State) -> str:
    if s.get("draft") is None:
        return "template"
    if not s["problems"]:
        return "done"
    return "revise" if s["attempts"] < MAX_REVISIONS else "template"


def _build():
    g = StateGraph(State)
    for name, fn in (("build_context", _context), ("summarize", _summarize), ("ground", _ground), ("revise", _revise), ("done", _done), ("template", _template)):
        g.add_node(name, fn)
    g.add_edge(START, "build_context")
    g.add_conditional_edges("build_context", _after_context, ["summarize", "template"])
    g.add_edge("summarize", "ground")
    g.add_conditional_edges("ground", _after_ground, ["done", "revise", "template"])
    g.add_edge("revise", "ground")
    g.add_edge("done", END)
    g.add_edge("template", END)
    return g.compile()


_GRAPH = None


def summarize(gw: Gateway, chart: Chart, principal=None) -> SummaryResult:
    global _GRAPH
    _GRAPH = _GRAPH or _build()
    return _GRAPH.invoke({"gw": gw, "chart": chart, "principal": principal})["result"]


def summarize_cached(gw: Gateway, chart: Chart, principal) -> SummaryResult:
    """Same as `summarize`, but an unchanged chart + prompt + model costs no second model call."""
    if not gw.online:
        return summarize(gw, chart, principal)
    facts = select_facts(chart)
    key = cache.make_key("summary", PROMPT_VERSION, gw.model, chart.patient_id, [f.text for f in facts.values()])
    hit = cache.get("summary", key, principal)
    if hit:
        return SummaryResult.model_validate({**hit, "cached": True})
    res = summarize(gw, chart, principal)
    if res.mode == "llm":
        cache.put("summary", key, chart.patient_id, chart.unit, res.model_dump(), principal)
    return res
