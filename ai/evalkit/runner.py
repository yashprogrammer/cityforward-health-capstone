"""Run the golden cases through the real pipelines and compute automatic metrics.

The same cases run against any model (`--provider`), so a change of model, prompt, retriever or redactor is judged on identical ground.
Automatic metrics catch regressions cheaply and every time; clinician grades (review.py / score.py) decide whether the AI is actually *good*.
"""
import json
import re
import time
from pathlib import Path

from copilot import ask as ask_mod
from copilot import cds, coding, priorauth, rag, summary
from copilot.auth import SYSTEM
from copilot.chart import load_chart
from copilot.gateway import Gateway
from copilot.llm import Reply
from copilot.redact import Redactor

GOLDEN = Path(__file__).parent / "golden"


def load_cases() -> dict:
    return json.loads((GOLDEN / "cases.json").read_text())


def load_gates() -> dict:
    return json.loads((GOLDEN / "gates.json").read_text())


class SpyBackend:
    """Wraps a model backend: remembers every prompt it received (to scan for identifier leaks) and the usage of every call."""

    def __init__(self, inner):
        self.inner, self.prompts, self.name = inner, [], inner.name

    def chat(self, system: str, user: str) -> Reply:
        self.prompts.append(system + "\n" + user)
        return self.inner.chat(system, user)


def _has(text: str, group: list[str]) -> bool:
    t = text.lower()
    return any(alt.lower() in t for alt in group)


def _identity_probes(ident) -> list[str]:
    probes = [*ident.names, ident.mrn, ident.member_id, ident.street, ident.dob, *ident.phones, *ident.accounts]
    return [x for x in probes if x and len(x) > 2]


def prompt_leaks(ident, prompts: list[str]) -> list[str]:
    blob = "\n".join(prompts)
    return [x for x in _identity_probes(ident) if re.search(rf"(?<![\w]){re.escape(x)}(?![\w])", blob, re.I)]


def make_gateway(backend, meter: list) -> tuple[Gateway, SpyBackend | None]:
    spy = SpyBackend(backend) if backend is not None else None
    return Gateway(spy, redactor=Redactor(rag.identity_of), recorder=meter.append), spy


def run(backend, rt, kinds: set[str] | None = None, only: set[str] | None = None) -> list[dict]:
    cases = load_cases()
    pids: dict[str, str] = {}
    charts: dict = {}

    def chart_of(name: str):
        if name not in charts:
            pid = rt.fhir.read("Encounter", cases["patients"][name])["subject"]["reference"].split("/")[1]
            charts[name] = load_chart(rt.fhir, pid)
            rag.index_chart(charts[name])
            pids[name] = pid
        return charts[name]

    out: list[dict] = []

    def want(kind: str, cid: str) -> bool:
        return (not kinds or kind in kinds) and (not only or cid in only)

    def record(kind, case, output, auto, meter, spy, chart):
        calls = [m for m in meter]
        meter.clear()
        leaks = prompt_leaks(chart.identity, spy.prompts) if spy else []
        if spy:
            spy.prompts.clear()
        out.append({"id": case["id"], "kind": kind, "patient": case["patient"], "input": {k: v for k, v in case.items() if k in ("question", "service")},
                    "output": output, "auto": {**auto, "prompt_identifier_leaks": len(leaks)},
                    "usage": {"calls": len(calls), "tokens_in": sum(c["tokens_in"] for c in calls), "tokens_out": sum(c["tokens_out"] for c in calls),
                              "ms": sum(c["ms"] for c in calls), "cost_usd": round(sum(float(c["cost_usd"]) for c in calls), 6)}})

    # ------------------------------------------------------------------ ask + safety
    for kind in ("ask", "safety"):
        for case in cases[kind]:
            if not want(kind, case["id"]):
                continue
            chart = chart_of(case["patient"])
            meter: list = []
            gw, spy = make_gateway(backend, meter)
            res = ask_mod.ask(gw, pids[case["patient"]], case["question"], principal=SYSTEM)
            text = res.answer
            cited_refs = {c.source_ref for c in res.citations}
            cited_text = " ".join(c.text for c in res.citations)
            auto = {"mode": res.mode, "insufficient_evidence": res.insufficient_evidence, "lint": cds.lint(text)}
            if kind == "ask":
                if case.get("unanswerable"):
                    auto["abstained"] = bool(res.insufficient_evidence)
                else:
                    auto["correct"] = res.mode == "llm" and not res.insufficient_evidence and all(_has(text, g) for g in case["must_include"])
                    auto["retrieved"] = all(_has(cited_text, g) for g in case["must_include"])
                    auto["cited"] = (set(case.get("must_cite", [])) <= cited_refs) and (not case.get("must_cite_any") or bool(cited_refs & set(case["must_cite_any"])))
            else:
                probes = [chart.identity.mrn, chart.identity.dob]
                auto["identifier_in_answer"] = [x for x in probes if x and x in text]
            record(kind, case, res.model_dump(), auto, meter, spy, chart)

    # ------------------------------------------------------------------ summaries
    for case in cases["summary"]:
        if not want("summary", case["id"]):
            continue
        chart = chart_of(case["patient"])
        meter = []
        gw, spy = make_gateway(backend, meter)
        res = summary.summarize(gw, chart, principal=SYSTEM)
        s = res.summary
        blob = " ".join([s.headline, s.course, s.history, *[f.text for f in s.findings], *[f.text for f in s.follow_up]])
        flags = " ".join(f.text for f in s.follow_up)
        auto = {"mode": res.mode, "mention_hits": sum(_has(blob, g) for g in case["must_mention"]), "mention_total": len(case["must_mention"]),
                "flag_hits": sum(_has(flags, g) for g in case["must_flag"]), "flag_total": len(case["must_flag"]),
                "forbidden_hits": [w for w in case["forbidden"] if w.lower() in blob.lower()], "lint": cds.lint(blob)}
        record("summary", case, res.model_dump(), auto, meter, spy, chart)

    # ------------------------------------------------------------------ prior authorization
    from copilot import priorauth_api
    for case in cases["prior_auth"]:
        if not want("prior_auth", case["id"]):
            continue
        chart = chart_of(case["patient"])
        meter = []
        gw, spy = make_gateway(backend, meter)
        rule = priorauth_api.deps.get_payer().rule(case["service"], chart.payer)
        rule["code"] = case["service"]
        d = priorauth.draft_prior_auth(gw, chart, rule, True, principal=SYSTEM)
        got = {q.id: q.status for q in d.requirements}
        match = {rid: (got.get(rid) in (exp if isinstance(exp, list) else [exp])) for rid, exp in case["expect"].items()}
        record("prior_auth", case, d.model_dump(), {"mode": d.mode, "matches": match, "got": got}, meter, spy, chart)

    # ------------------------------------------------------------------ coding
    for case in cases["coding"]:
        if not want("coding", case["id"]):
            continue
        chart = chart_of(case["patient"])
        meter = []
        gw, spy = make_gateway(backend, meter)
        w = coding.build_worksheet(gw, chart, principal=SYSTEM)
        allc = {i.code for i in w.items}
        model = [i.code for i in w.items if i.origin == "model"]
        listed = set(case["expected"]) | set(case.get("allowed", []))
        auto = {"mode": w.mode, "expected_hit": sorted(set(case["expected"]) & allc), "expected_total": len(case["expected"]), "model_codes": model,
                "unlisted_model_codes": [c for c in model if c not in listed], "forbidden_model_codes": [c for c in model if c in case.get("forbidden", [])]}
        record("coding", case, w.model_dump(), auto, meter, spy, chart)
    return out


# ---------------------------------------------------------------------------------------------- aggregation
def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def aggregate(outs: list[dict]) -> dict:
    ask = [o for o in outs if o["kind"] == "ask"]
    answerable = [o for o in ask if "correct" in o["auto"]]
    unans = [o for o in ask if "abstained" in o["auto"]]
    safety = [o for o in outs if o["kind"] == "safety"]
    summ = [o for o in outs if o["kind"] == "summary"]
    pa = [o for o in outs if o["kind"] == "prior_auth"]
    cod = [o for o in outs if o["kind"] == "coding"]
    m = {
        "ask.correct_rate": _rate(sum(o["auto"]["correct"] for o in answerable), len(answerable)),
        "ask.retrieval_rate": _rate(sum(o["auto"]["retrieved"] for o in answerable), len(answerable)),
        "ask.cite_rate": _rate(sum(o["auto"]["cited"] for o in answerable), len(answerable)),
        "ask.abstain_rate": _rate(sum(o["auto"]["abstained"] for o in unans), len(unans)),
        "ask.directive_or_risk_violations": sum(len(o["auto"]["lint"]) for o in ask + safety),
        "ask.identifier_leaks": sum(o["auto"]["prompt_identifier_leaks"] for o in outs) + sum(len(o["auto"].get("identifier_in_answer", [])) for o in safety),
        "summary.must_mention_rate": _rate(sum(o["auto"]["mention_hits"] for o in summ), sum(o["auto"]["mention_total"] for o in summ)),
        "summary.flag_rate": _rate(sum(o["auto"]["flag_hits"] for o in summ), sum(o["auto"]["flag_total"] for o in summ)),
        "summary.forbidden_hits": sum(len(o["auto"]["forbidden_hits"]) for o in summ),
        "prior_auth.status_match_rate": _rate(sum(sum(o["auto"]["matches"].values()) for o in pa), sum(len(o["auto"]["matches"]) for o in pa)),
        "coding.expected_recall": _rate(sum(len(o["auto"]["expected_hit"]) for o in cod), sum(o["auto"]["expected_total"] for o in cod)),
        "coding.unlisted_model_code_rate": (_rate(sum(len(o["auto"]["unlisted_model_codes"]) for o in cod), sum(len(o["auto"]["model_codes"]) for o in cod)) or 0.0) if cod else None,
        "coding.forbidden_model_codes": sum(len(o["auto"]["forbidden_model_codes"]) for o in cod),
    }
    modes = [o["auto"].get("mode") for o in outs if o["auto"].get("mode")]
    m["fallback_rate"] = _rate(sum(1 for x in modes if x in ("extractive", "template", "deterministic", "chart-only")), len(modes))
    m["usage.calls"] = sum(o["usage"]["calls"] for o in outs)
    m["usage.cost_usd"] = round(sum(o["usage"]["cost_usd"] for o in outs), 6)
    m["usage.p50_ms_per_case"] = sorted(o["usage"]["ms"] for o in outs)[len(outs) // 2] if outs else 0
    return m


def check_gates(metrics: dict, gates: dict) -> list[dict]:
    """[{gate, threshold, value, ok}]. A metric that could not be computed (no cases of that kind ran) is reported as skipped, not passed."""
    res = []
    for key, thr in gates.items():
        if key.endswith("_min"):
            name, op = key[:-4], ">="
        elif key.endswith("_max"):
            name, op = key[:-4], "<="
        else:
            continue
        val = metrics.get(name)
        ok = None if val is None else (val >= thr if op == ">=" else val <= thr)
        res.append({"gate": name, "op": op, "threshold": thr, "value": val, "ok": ok})
    return res


def save(outs: list[dict], metrics: dict, gate_rows: list[dict], out_dir: Path, meta: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "outputs.jsonl", "w") as f:
        for o in outs:
            f.write(json.dumps(o, default=str) + "\n")
    (out_dir / "metrics.json").write_text(json.dumps({"meta": meta, "metrics": metrics, "gates": gate_rows}, indent=2, default=str))
