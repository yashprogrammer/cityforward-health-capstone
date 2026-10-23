"""The live incident drill: "What almost went wrong".

A well-meaning change ships a new prompt-assembly path and, as a side effect, the model gateway is wired WITHOUT its de-identification step. Nothing crashes. Features work. Clinicians are happy.
The drill replays that incident against the real pipelines on synthetic patients and walks the response from the incident response plan (docs/compliance/incident-response-plan.md, playbook P1):

    T0  baseline        healthy system: prompts are clean
    T1  the bad deploy  redaction silently off; clinicians keep working; identifiers flow into prompts
    T2  detection       what notices, and how fast (the canary is instant; the metric alert needs an hour of traffic)
    T3  containment     switch the AI off; clinical features keep working in labelled no-AI mode
    T4  scoping         who was exposed, when, to whom: from metering + audit (prompts are not stored, so scope is reconstructed)
    T5  fix and verify  restore the redactor; leakage scan and canary are green again
    T6  report          timeline, scope, breach-assessment worksheet and the notification deadline

    PYTHONPATH=ai:customer python -m incident.drill            (add --pause to run it live with a room)
Everything here is synthetic. No model is called: a spy backend records what WOULD have been sent.
"""
import json
import sys
from datetime import datetime, timedelta, timezone

from copilot import audit, canary, db, discharge, summary
from copilot.auth import SYSTEM
from copilot.chart import load_chart
from copilot.gateway import Gateway
from copilot.llm import Reply
from copilot.redact import Redactor
from copilot import rag

PATIENTS = ["IP2610-0142", "IP2610-0151", "IP2610-0138"]


class SpyModel:
    """A stand-in model that records every prompt it receives and answers with a minimal valid summary."""
    name = "spy:model"

    def __init__(self):
        self.prompts: list[str] = []

    def chat(self, system, user):
        self.prompts.append(system + "\n" + user)
        text = json.dumps({"headline": "h", "course": "c", "history": "x", "findings": [{"text": "t", "sources": ["E1"]}], "follow_up": []})
        return Reply(text, tokens_in=len(user) // 4, tokens_out=40)


def leaked(chart, prompts) -> list[str]:
    ident = chart.identity
    blob = "\n".join(prompts)
    return [x for x in (*ident.names, ident.mrn, ident.dob, ident.street, *ident.phones, *ident.accounts) if x and len(x) > 2 and x in blob]


def detect_redaction_gap(min_calls: int = 3, window_minutes: int = 15) -> dict:
    """The production alert `RedactionStoppedWorking`, as a query: model calls that went out with nothing redacted.
    (A first version summed redactions over all traffic and stayed silent while healthy and broken traffic mixed: an alert on a total masks a partial failure.)"""
    with db.system() as c:
        r = c.execute("SELECT count(*) calls, count(*) FILTER (WHERE redactions = 0) unredacted FROM llm_call WHERE ts > now() - make_interval(mins => %s)", (window_minutes,)).fetchone()
    return {"firing": r["unredacted"] >= min_calls, **r}


def scope(since: datetime) -> dict:
    """Which patients' prompts went out unredacted, to which model, when, and which users were using the system."""
    with db.system() as c:
        calls = c.execute("SELECT ts, purpose, patient_id, actor, model, tokens_in FROM llm_call WHERE ts >= %s AND redactions = 0 AND status <> 'error' ORDER BY ts", (since,)).fetchall()
    patients = sorted({r["patient_id"] for r in calls if r["patient_id"]})
    return {"first_unredacted_call": calls[0]["ts"].isoformat(timespec="seconds") if calls else None, "last_unredacted_call": calls[-1]["ts"].isoformat(timespec="seconds") if calls else None,
            "calls": len(calls), "patients": patients, "purposes": sorted({r["purpose"] for r in calls}), "models": sorted({r["model"] for r in calls}), "tokens_sent": sum(r["tokens_in"] for r in calls),
            "users": sorted({r["actor"] for r in calls if r["actor"]})}


def traffic(gw: Gateway, rt, spy: SpyModel, charts: list, ) -> None:
    """A morning of normal use: clinicians open the stay summary for each patient."""
    for chart in charts:
        summary.summarize(gw, chart, SYSTEM)


def run_drill(rt, pause=lambda label: None) -> dict:
    from copilot.app import rt as runtime
    t: list[tuple[str, str]] = []

    def note(phase: str, text: str) -> None:
        t.append((datetime.now(timezone.utc).strftime("%H:%M:%S"), f"{phase}  {text}"))

    with db.system() as c:                                    # a clean slate for the drill's evidence
        c.execute("TRUNCATE llm_call")
    charts = []
    for ip in PATIENTS:
        ch = load_chart(rt.fhir, rt.fhir.read("Encounter", ip)["subject"]["reference"].split("/")[1])
        rag.index_chart(ch)
        charts.append(ch)

    # T0 ---------------------------------------------------------------- baseline
    pause("T0 baseline")
    spy = SpyModel()
    healthy = Gateway(spy, redactor=Redactor(rag.identity_of))
    traffic(healthy, rt, spy, charts)
    base_leaks = {c.name: leaked(c, spy.prompts) for c in charts}
    note("T0", f"healthy gateway: {len(spy.prompts)} prompts, identifier leaks: {sum(map(len, base_leaks.values()))}; canary ok: {canary.run(healthy)['ok']}")
    assert not any(base_leaks.values())

    # T1 ---------------------------------------------------------------- the bad deploy
    pause("T1 the bad deploy")
    spy.prompts.clear()
    started = datetime.now(timezone.utc)
    broken = Gateway(spy, redactor=None)                       # the regression: the redactor is no longer wired in
    runtime.gateway = broken
    traffic(broken, rt, spy, charts)
    t1_leaks = {c.name: leaked(c, spy.prompts) for c in charts}
    note("T1", f"bad deploy live: {len(spy.prompts)} prompts went out; features all worked; names/MRNs/dates in prompts: {sum(map(len, t1_leaks.values()))}")

    # T2 ---------------------------------------------------------------- detection
    pause("T2 detection")
    c_before = canary.run(healthy)
    c_now = canary.run(broken)
    gap = detect_redaction_gap()
    note("T2", f"canary against the live gateway: ok={c_now['ok']} (would leak {len(c_now['leaked'])} synthetic identifiers) -> alert RedactionCanaryFailed after 2 minutes")
    note("T2", f"metric alert RedactionStoppedWorking: firing={gap['firing']} ({gap['unredacted']} of {gap['calls']} recent calls went out with nothing redacted) -> needs real traffic")

    # T3 ---------------------------------------------------------------- containment
    pause("T3 containment")
    spy_calls = len(spy.prompts)
    contained = Gateway(None)                                   # LLM_PROVIDER=offline: always safe, every feature has a labelled fallback
    runtime.gateway = contained
    res = summary.summarize(contained, charts[0], SYSTEM)
    note("T3", f"AI switched off. Clinical work continues: summary served in '{res.mode}' mode ({res.note}); prompts sent since containment: {len(spy.prompts) - spy_calls}")

    # T4 ---------------------------------------------------------------- scoping
    pause("T4 scoping")
    sc = scope(started - timedelta(seconds=1))
    note("T4", f"scope from metering + audit (prompt text is not stored): {sc['calls']} unredacted calls, {len(sc['patients'])} patient(s), purposes {sc['purposes']}, model {sc['models']}, window {sc['first_unredacted_call']} to {sc['last_unredacted_call']}")

    # T5 ---------------------------------------------------------------- fix and verify
    pause("T5 fix and verify")
    spy.prompts.clear()
    fixed = Gateway(spy, redactor=Redactor(rag.identity_of))
    runtime.gateway = fixed
    traffic(fixed, rt, spy, charts)
    after = {c.name: leaked(c, spy.prompts) for c in charts}
    c_after = canary.run(fixed)
    note("T5", f"redactor restored: leaks {sum(map(len, after.values()))}, canary ok={c_after['ok']}. Regression test added to CI: the leakage test over every patient (ai/tests/test_redact.py)")
    assert not any(after.values()) and c_after["ok"]

    # T6 ---------------------------------------------------------------- report
    discovered = started + timedelta(hours=1)
    report = {"timeline": t, "scope": sc, "leaks_during_incident": t1_leaks, "canary": {"before": c_before["ok"], "during": c_now["ok"], "after": c_after["ok"]},
              "detection": gap, "notification_deadline_if_reportable": (discovered + timedelta(days=60)).date().isoformat()}
    pause("T6 report")
    audit.log("drill", "incident.drill.completed", detail={"calls": sc["calls"], "patients": len(sc["patients"])})
    return report


def render(report: dict) -> str:
    sc = report["scope"]
    lines = ["# Incident report (drill): redaction bypass", "", "## Timeline (UTC)", *[f"- {ts}  {text}" for ts, text in report["timeline"]], "",
             "## Scope", f"- {sc['calls']} model calls carried unredacted identifiers, covering {len(sc['patients'])} patient(s): {', '.join(sc['patients'])}",
             f"- window {sc['first_unredacted_call']} to {sc['last_unredacted_call']}; model(s): {', '.join(sc['models'])}; purposes: {', '.join(sc['purposes'])}; users: {', '.join(sc['users']) or 'system'}", "",
             "## Breach risk assessment worksheet (the covered entity decides; 45 CFR 164.402)",
             "1. Nature and extent of PHI involved, and likelihood of re-identification: names, MRNs, dates, addresses in free text for the patients above: HIGH identifiability.",
             "2. Who received it: the model provider named above. Contract status (BAA, retention, no-training): ______ (this decides whether this is a disclosure to a business associate or an impermissible disclosure).",
             "3. Was the PHI actually acquired or viewed: provider logs / retention settings: ______",
             "4. Extent to which the risk has been mitigated: deletion confirmation from the provider: ______",
             f"If reportable, individual notification is due without unreasonable delay and no later than {report['notification_deadline_if_reportable']} (60 days from discovery, taking discovery as one hour after the start of this drill).", "",
             "## What would have prevented or shortened it", "- the redaction canary against the live gateway (fires in minutes, no traffic needed)", "- the leakage test over every patient as a required CI check on any change under `ai/copilot`",
             "- a gateway that cannot be built around a real model client without a redactor (implemented after this drill: `Gateway.__init__`, test `test_a_real_model_client_cannot_be_wired_without_redaction`)"]
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    pause = (lambda label: input(f"\n--- {label} (Enter) ")) if "--pause" in argv else (lambda label: None)
    from evalkit.harness import full_stack
    with full_stack() as (_m, rt):
        report = run_drill(rt, pause)
    print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
