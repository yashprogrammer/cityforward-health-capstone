"""Ask a question about ONE patient's chart. Retrieval-augmented, cited, checked.

    retrieve -> answer -> ground --ok--> done
                           |  bad, one retry -> revise -> ground
                           |  bad again / model down / nothing retrieved -> extractive (show the passages, no prose)

The answer must cite the retrieved passages (C1..Cn). `ground` checks every citation and every number against them.
If the chart does not contain the answer the model is told to say so (insufficient_evidence), and that is a valid, honest outcome.
"""
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from . import rag
from .gateway import Gateway
from . import cds
from .grounding import check_statement
from .llm import LLMError

MAX_REVISIONS = 1
K = 8


class Answer(BaseModel):
    answer: str
    citations: list[str] = Field(default_factory=list, description="passage ids such as C2")
    insufficient_evidence: bool = False


class Citation(BaseModel):
    id: str
    source_ref: str
    kind: str
    text: str
    effective: str | None = None


class AskResult(BaseModel):
    question: str
    answer: str
    citations: list[Citation]
    mode: Literal["llm", "extractive"]
    insufficient_evidence: bool = False
    model: str = ""
    problems: list[str] = Field(default_factory=list)
    note: str = ""


class State(TypedDict, total=False):
    gw: Gateway
    principal: object
    context: object
    patient_id: str
    question: str
    hits: list
    evidence: dict[str, str]
    draft: Answer | None
    problems: list[str]
    attempts: int
    note: str
    result: AskResult


SYSTEM = """You answer a clinician's question using ONLY the numbered passages from one patient's chart.
Cite the passage ids that support the answer. Never use outside knowledge for patient facts and never invent a value, date or dose.
If the passages do not contain the answer, set insufficient_evidence to true and say what is missing.
Reply with a single JSON object and nothing else: {"answer": str, "citations": ["C1", ...], "insufficient_evidence": bool}"""


def _retrieve(s: State) -> State:
    hits = rag.search(s["patient_id"], s["question"], k=K, principal=s.get("principal"))
    return {"hits": hits, "evidence": {f"C{i + 1}": h.text for i, h in enumerate(hits)}, "attempts": 0, "problems": [], "draft": None}


def _prompt(s: State, revise: bool = False) -> str:
    body = "PASSAGES\n" + "\n".join(f"[{k}] {v}" for k, v in s["evidence"].items()) + f"\n\nQUESTION: {s['question']}"
    if revise:
        body += "\n\nYOUR PREVIOUS ANSWER:\n" + s["draft"].model_dump_json() + "\n\nIT FAILED THESE CHECKS; return corrected JSON:\n" + "\n".join(f"- {p}" for p in s["problems"])
    return body


def _call(s: State, revise: bool = False) -> State:
    try:
        d = s["gw"].json(SYSTEM, _prompt(s, revise), Answer, purpose="ask", patient_id=s["patient_id"], actor=getattr(s.get("principal"), "user", None), context=s.get("context"))
        return {"draft": d, "problems": []}
    except LLMError as e:
        return {"draft": None, "problems": [str(e)[:200]], "note": f"model unavailable or invalid output: {str(e)[:120]}"}


def _answer(s: State) -> State:
    return _call(s)


def _revise(s: State) -> State:
    return {**_call(s, revise=True), "attempts": s["attempts"] + 1}


def _ground(s: State) -> State:
    d = s.get("draft")
    if d is None or d.insufficient_evidence:
        return {"problems": []} if d else {}
    return {"problems": check_statement(d.answer, d.citations, s["evidence"]) + cds.lint(d.answer)}


def _cites(s: State, ids: list[str]) -> list[Citation]:
    by_id = {f"C{i + 1}": h for i, h in enumerate(s["hits"])}
    return [Citation(id=i, source_ref=by_id[i].source_ref, kind=by_id[i].kind, text=by_id[i].text, effective=by_id[i].effective) for i in ids if i in by_id]


def _done(s: State) -> State:
    d = s["draft"]
    ids = [c for c in d.citations if c in s["evidence"]]
    return {"result": AskResult(question=s["question"], answer=d.answer, citations=_cites(s, ids), mode="llm", insufficient_evidence=d.insufficient_evidence,
                                model=s["gw"].model, problems=[])}


def _extractive(s: State) -> State:
    hits = s["hits"]
    if not hits:
        return {"result": AskResult(question=s["question"], answer="Nothing in this patient's indexed chart matches the question.", citations=[], mode="extractive",
                                    insufficient_evidence=True, note="no passages retrieved (is the patient indexed?)")}
    note = s.get("note") or ("no model configured" if not s["gw"].online else "the model's answer failed citation checks twice")
    return {"result": AskResult(question=s["question"], answer="No verified AI answer. The most relevant passages from the chart are listed below.",
                                citations=_cites(s, list(s["evidence"])[:5]), mode="extractive", model=s["gw"].model, problems=s.get("problems", []), note=note)}


def _after_retrieve(s: State) -> str:
    return "answer" if s["hits"] and s["gw"].online else "extractive"


def _after_ground(s: State) -> str:
    if s.get("draft") is None:
        return "extractive"
    if not s["problems"]:
        return "done"
    return "revise" if s["attempts"] < MAX_REVISIONS else "extractive"


def _build():
    g = StateGraph(State)
    for name, fn in (("retrieve", _retrieve), ("answer", _answer), ("ground", _ground), ("revise", _revise), ("done", _done), ("extractive", _extractive)):
        g.add_node(name, fn)
    g.add_edge(START, "retrieve")
    g.add_conditional_edges("retrieve", _after_retrieve, ["answer", "extractive"])
    g.add_edge("answer", "ground")
    g.add_conditional_edges("ground", _after_ground, ["done", "revise", "extractive"])
    g.add_edge("revise", "ground")
    g.add_edge("done", END)
    g.add_edge("extractive", END)
    return g.compile()


_GRAPH = None


def ask(gw: Gateway, patient_id: str, question: str, principal=None, context=None) -> AskResult:
    global _GRAPH
    _GRAPH = _GRAPH or _build()
    return _GRAPH.invoke({"gw": gw, "patient_id": patient_id, "question": question, "principal": principal, "context": context})["result"]
