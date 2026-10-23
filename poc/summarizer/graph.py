"""The patient-history summarizer as a LangGraph state graph.

    build_context -> summarize -> ground --ok--------------------------> done
                                     |  bad, attempts left -> revise -> ground
                                     |  bad, out of attempts / model down -> template -> done

Why a graph and not one prompt: the *checking* is code. The model writes the summary and cites evidence ids;
`ground` verifies every citation exists in the chart and every number it quotes appears in the cited evidence.
A failed check sends the specific problems back to the model once. If it still fails (or no model is reachable),
the deterministic template summary is returned and the result says so. A summary is never shown unchecked.
"""
import re
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, ValidationError

from .llm import LLM, LLMError
from .records import PatientRecord

MAX_REVISIONS = 1


# ------------------------------------------------------------------------------------------ schema
class Finding(BaseModel):
    text: str
    sources: list[str] = Field(default_factory=list, description="evidence ids such as E3")


class HistorySummary(BaseModel):
    headline: str
    course: str = Field(description="what happened this stay, in 2-4 sentences")
    history: str = Field(description="relevant earlier history, in 1-3 sentences")
    findings: list[Finding] = Field(default_factory=list)
    follow_up: list[Finding] = Field(default_factory=list, description="open items a clinician should check")


class Result(BaseModel):
    summary: HistorySummary
    evidence: dict[str, str]
    mode: Literal["llm", "template"]
    model: str = ""
    problems: list[str] = Field(default_factory=list, description="grounding problems found on the last check")
    attempts: int = 0
    note: str = ""


class State(TypedDict, total=False):
    record: PatientRecord
    llm: LLM | None
    evidence: dict[str, str]
    draft: HistorySummary | None
    problems: list[str]
    attempts: int
    result: Result
    note: str


# ------------------------------------------------------------------------------------------ evidence
def _fmt_obs(o) -> str:
    v = "NO RESULT YET (pending)" if o.value is None else f"{o.value:g} {o.unit}".strip()
    return f"{o.date[:10]} {o.name}: {v}" + (f" ({o.flag})" if o.flag else "")


def build_evidence(rec: PatientRecord, max_notes_chars: int = 1800) -> dict[str, str]:
    """A numbered list of short facts. The model may cite only these ids."""
    ev: dict[str, str] = {}

    def add(text: str) -> None:
        ev[f"E{len(ev) + 1}"] = text

    add(f"Patient: {rec.name}, {rec.age} y {rec.sex}; payer {rec.payer or 'self-pay'}; chart date {rec.as_of}")
    inpatient = [e for e in rec.encounters if e.cls == "IMP"]
    for e in inpatient[-2:]:
        add(f"Inpatient encounter {e.start[:10]} to {(e.end or 'ongoing')[:10]}: {e.type}" + (f"; reason: {e.reason}" if e.reason else ""))
    for c in rec.problem_list()[:12]:
        add(f"Diagnosis: {c.display}" + (f" [ICD-10-CM {c.icd10}]" if c.icd10 else "") + (f", onset {c.onset[:10]}" if c.onset else ""))
    for p in sorted(rec.procedures, key=lambda p: p.date or "")[-4:]:
        add(f"Procedure {(p.date or '')[:10]}: {p.name}")
    for m in rec.active_medications()[:14]:
        add(f"Active medication: {m.name}" + (f" - {m.sig}" if m.sig else ""))
    for latest, prev in rec.latest_by_test("laboratory")[:12]:
        add("Lab " + _fmt_obs(latest) + (f"; previous {prev.value:g} on {prev.date[:10]}" if prev and prev.value is not None else ""))
    for latest, _ in rec.latest_by_test("vital-signs")[:6]:
        add("Vital " + _fmt_obs(latest))
    for n in rec.notes[-4:]:
        txt = re.sub(r"\s+", " ", n.text)[:max_notes_chars]
        add(f"Note {n.date[:10]} ({n.kind or 'clinical note'}, {n.author or 'unknown author'}): {txt}")
    return ev


SYSTEM = """You summarize a patient's chart for the clinician taking over care.
Use ONLY the numbered evidence. Never invent a fact, dose, date or number. Cite the evidence ids that support every statement.
If something important is missing from the evidence, say so under follow_up instead of guessing.
Reply with a single JSON object and nothing else:
{"headline": str, "course": str, "history": str,
 "findings": [{"text": str, "sources": ["E1", ...]}], "follow_up": [{"text": str, "sources": [...]}]}"""


def _user_prompt(ev: dict[str, str], problems: list[str] | None = None, previous: HistorySummary | None = None) -> str:
    body = "EVIDENCE\n" + "\n".join(f"[{k}] {v}" for k, v in ev.items())
    if problems and previous:
        body += "\n\nYOUR PREVIOUS DRAFT:\n" + previous.model_dump_json() + "\n\nIT FAILED THESE CHECKS, fix them and return the corrected JSON:\n" + "\n".join(f"- {p}" for p in problems)
    return body


# ------------------------------------------------------------------------------------------ grounding
_NUM = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")


def check_grounding(s: HistorySummary, ev: dict[str, str]) -> list[str]:
    """Deterministic checks. Returns a list of problems; empty means the summary is acceptable."""
    problems = []
    items = s.findings + s.follow_up
    if not s.findings:
        problems.append("no findings were given")
    for f in items:
        if not f.sources:
            problems.append(f'statement has no citation: "{f.text[:60]}"')
            continue
        bad = [x for x in f.sources if x not in ev]
        if bad:
            problems.append(f'unknown evidence id {bad} in: "{f.text[:60]}"')
            continue
        cited = " ".join(ev[x] for x in f.sources)
        cited_nums = set(_NUM.findall(cited))
        for num in _NUM.findall(f.text):
            if num not in cited_nums and num not in ("1", "2", "3"):   # tiny counts like "2 stents" are prose, not data
                problems.append(f'number {num} in "{f.text[:60]}" does not appear in the cited evidence {f.sources}')
    return problems


# ------------------------------------------------------------------------------------------ template (no model)
def template_summary(rec: PatientRecord, ev: dict[str, str]) -> HistorySummary:
    """What a plain script produces from the same evidence: boring, and always grounded."""
    ids = {k: v for k, v in ev.items()}

    def pick(prefix: str, limit: int) -> list[Finding]:
        return [Finding(text=v.split(": ", 1)[-1] if prefix in ("Diagnosis", "Active") else v, sources=[k]) for k, v in ids.items() if v.startswith(prefix)][:limit]

    inpatient = [(k, v) for k, v in ids.items() if v.startswith("Inpatient encounter")]
    course = inpatient[-1][1] if inpatient else "No inpatient stay in the chart."
    dx = [v.split("[")[0].replace("Diagnosis: ", "").strip() for v in ids.values() if v.startswith("Diagnosis")][:3]
    return HistorySummary(
        headline=f"{rec.name}, {rec.age}{rec.sex}: " + (", ".join(dx) if dx else "no diagnoses on file"),
        course=course, history=f"{len(rec.problem_list())} distinct diagnoses and {len(rec.active_medications())} active medications on file.",
        findings=pick("Diagnosis", 5) + pick("Active medication", 5) + pick("Lab", 5),
        follow_up=[Finding(text="Automatic template summary: a clinician must review the chart.", sources=["E1"])])


# ------------------------------------------------------------------------------------------ nodes
def _node_context(s: State) -> State:
    return {"evidence": build_evidence(s["record"]), "attempts": 0, "problems": [], "draft": None}


def _ask(s: State, user: str) -> State:
    try:
        raw = s["llm"].complete_json(SYSTEM, user)
        draft = HistorySummary.model_validate(raw)
    except (LLMError, ValidationError) as e:
        msg = str(e).splitlines()[0][:200]
        return {"draft": None, "problems": [f"model call failed: {msg}"], "note": f"model unavailable or invalid output ({type(e).__name__})"}
    return {"draft": draft, "problems": []}


def _node_summarize(s: State) -> State:
    return _ask(s, _user_prompt(s["evidence"]))


def _node_revise(s: State) -> State:
    out = _ask(s, _user_prompt(s["evidence"], s["problems"], s["draft"]))
    return {**out, "attempts": s["attempts"] + 1}


def _node_ground(s: State) -> State:
    if s.get("draft") is None:
        return {}
    return {"problems": check_grounding(s["draft"], s["evidence"])}


def _node_done(s: State) -> State:
    return {"result": Result(summary=s["draft"], evidence=s["evidence"], mode="llm", model=s["llm"].name, attempts=s["attempts"], problems=[])}


def _node_template(s: State) -> State:
    rec, ev = s["record"], s["evidence"]
    note = s.get("note") or ("no model configured" if s.get("llm") is None else "the model's summary failed grounding checks twice")
    return {"result": Result(summary=template_summary(rec, ev), evidence=ev, mode="template", model=getattr(s.get("llm"), "name", ""),
                             problems=s.get("problems", []), attempts=s.get("attempts", 0), note=note)}


def _route_start(s: State) -> str:
    return "summarize" if s.get("llm") is not None else "template"


def _route_after_ground(s: State) -> str:
    if s.get("draft") is None:
        return "template"
    if not s["problems"]:
        return "done"
    return "revise" if s["attempts"] < MAX_REVISIONS else "template"


def build_graph():
    g = StateGraph(State)
    g.add_node("build_context", _node_context)
    g.add_node("summarize", _node_summarize)
    g.add_node("ground", _node_ground)
    g.add_node("revise", _node_revise)
    g.add_node("done", _node_done)
    g.add_node("template", _node_template)
    g.add_edge(START, "build_context")
    g.add_conditional_edges("build_context", _route_start, ["summarize", "template"])
    g.add_edge("summarize", "ground")
    g.add_conditional_edges("ground", _route_after_ground, ["done", "revise", "template"])
    g.add_edge("revise", "ground")
    g.add_edge("done", END)
    g.add_edge("template", END)
    return g.compile()


_GRAPH = None


def summarize(record: PatientRecord, llm: LLM | None) -> Result:
    global _GRAPH
    _GRAPH = _GRAPH or build_graph()
    return _GRAPH.invoke({"record": record, "llm": llm})["result"]
