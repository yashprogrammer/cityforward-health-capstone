"""Discharge readiness: three specialist agents and a deterministic supervisor (course lecture: multi-agent design; docs/multi-agent-design.md).

    supervisor (plain code, no model)
        ├── summary agent     reads the chart, writes the cited stay summary           (purpose "summary",    needs action `summary`)
        ├── coding agent      proposes ICD-10-CM codes for the coder                   (purpose "coding",     needs action `coding.suggest`)
        └── prior-auth agent  one run per service the plan will want authorized        (purpose "prior-auth", needs action `priorauth.draft`)

Why split at all? Each agent has a different job, permission, model, failure mode and evaluation (the golden set scores them separately), and one failing must not stop the others.
Why a code supervisor and not an "orchestrator model"? Routing here is a rule (which services does the chart mention, which roles may do what). A model in that seat adds cost,
latency and a new way to be wrong, for no flexibility anyone needs. Agents never call each other; they share only the chart and write only their own artifacts.
"""
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
from typing import Callable

from fastapi import HTTPException
from pydantic import BaseModel, Field

from . import coding_api, dashboard, db, priorauth, priorauth_api, summary
from .access import ROLE_ACTIONS
from .auth import Principal
from .chart import Chart

AGENT_TIMEOUT_S = 120


class AgentResult(BaseModel):
    agent: str
    status: str                       # ok | degraded | failed | skipped | nothing-to-do
    seconds: float = 0.0
    detail: str = ""
    artifacts: list[str] = Field(default_factory=list)
    error: str = ""


class Readiness(BaseModel):
    patient_id: str
    encounter_id: str | None
    generated_at: str
    agents: list[AgentResult]
    blockers: list[str]               # things only a human can resolve, collected from every agent
    flags: list[str] = Field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(a.status in ("ok", "degraded", "nothing-to-do", "skipped") for a in self.agents)


class Context(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    chart: Chart
    principal: Principal
    gateway: object
    payer: object


# ---------------------------------------------------------------------------------------------- the agents (each owns one artifact type)
def summary_agent(ctx: Context) -> AgentResult:
    res = summary.summarize_cached(ctx.gateway, ctx.chart, ctx.principal)
    pending = [f.text for f in res.summary.follow_up]
    return AgentResult(agent="summary", status="ok" if res.mode == "llm" else "degraded", artifacts=[f"summary:{ctx.chart.patient_id}"],
                       detail=("cached " if res.cached else "") + res.mode + (f" ({res.note})" if res.note else "") + f"; {len(pending)} open item(s)")


def coding_agent(ctx: Context) -> AgentResult:
    stay = ctx.chart.current_encounter()
    if stay and coding_api.open_worksheet(ctx.chart.patient_id, stay.id):
        return AgentResult(agent="coding", status="nothing-to-do", detail="an open worksheet already exists")
    ws = coding_api.create_for(ctx.principal, ctx.chart.patient_id, chart=ctx.chart)
    return AgentResult(agent="coding", status="ok" if ws["mode"] == "llm" else "degraded", artifacts=[ws["id"]],
                       detail=f"{ws['mode']}: {len(ws['items'])} line(s), {len(ws['queries'])} documentation query(ies)")


def prior_auth_agent(ctx: Context) -> AgentResult:
    made, notes = [], []
    for sug in priorauth.suggest_services(ctx.chart, ctx.payer):
        if not sug.pa_required:
            continue
        if priorauth_api.open_request(ctx.chart.patient_id, sug.service_code):
            notes.append(f"{sug.service_code} already in progress")
            continue
        pa = priorauth_api.create_draft_for(ctx.principal, ctx.chart.patient_id, sug.service_code, chart=ctx.chart)
        made.append(pa["id"])
        notes.append(f"{sug.service_code}: {len(pa['gaps'])} open requirement(s)")
    if not made and not notes:
        return AgentResult(agent="prior-auth", status="nothing-to-do", detail="no service in the chart needs authorization under this plan")
    return AgentResult(agent="prior-auth", status="ok" if made else "nothing-to-do", artifacts=made, detail="; ".join(notes))


class Agent(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    name: str
    needs: str                        # the role-matrix action a principal must hold to run this agent
    run: Callable


AGENTS = [Agent(name="summary", needs="summary", run=summary_agent), Agent(name="coding", needs="coding.suggest", run=coding_agent),
          Agent(name="prior-auth", needs="priorauth.draft", run=prior_auth_agent)]


# ---------------------------------------------------------------------------------------------- the supervisor
def _run(agent: Agent, ctx: Context) -> AgentResult:
    t0 = time.perf_counter()
    try:
        r = agent.run(ctx)
    except HTTPException as e:                    # e.g. the payer is unreachable: a clean, local failure
        r = AgentResult(agent=agent.name, status="failed", error=f"{e.status_code}: {e.detail}")
    except Exception as e:   # noqa: BLE001 - isolation: one agent's bug is that agent's failure, not the packet's
        r = AgentResult(agent=agent.name, status="failed", error=f"{type(e).__name__}: {e}"[:300])
    r.seconds = round(time.perf_counter() - t0, 2)
    return r


def prepare(chart: Chart, gateway, principal: Principal, payer, agents: list[Agent] | None = None, timeout: float = AGENT_TIMEOUT_S) -> Readiness:
    """Run every agent this principal is allowed to run, in parallel, and assemble what is left for humans."""
    ctx = Context(chart=chart, principal=principal, gateway=gateway, payer=payer)
    allowed = ROLE_ACTIONS.get(principal.role, set())
    plan = agents or AGENTS
    results: dict[str, AgentResult] = {a.name: AgentResult(agent=a.name, status="skipped", detail=f"role {principal.role} may not perform {a.needs}") for a in plan if a.needs not in allowed}
    runnable = [a for a in plan if a.needs in allowed]
    with ThreadPoolExecutor(max_workers=max(1, len(runnable))) as pool:
        futures = {a.name: pool.submit(_run, a, ctx) for a in runnable}
        for name, fut in futures.items():
            try:
                results[name] = fut.result(timeout=timeout)
            except FutureTimeout:
                results[name] = AgentResult(agent=name, status="failed", error=f"timed out after {timeout:g}s")
    ordered = [results[a.name] for a in plan]
    stay = chart.current_encounter()
    blockers = []
    with db.system() as c:
        for r in c.execute("SELECT id, service_code, requirements FROM prior_auth WHERE patient_id=%s AND status IN ('DRAFT','PENDED')", (chart.patient_id,)).fetchall():
            gaps = [q["id"] for q in r["requirements"] if q["status"] not in priorauth.RESOLVED]
            if gaps:
                blockers.append(f"Prior authorization {r['service_code']} ({r['id']}): {len(gaps)} requirement(s) need a clinician: {', '.join(gaps)}")
        for w in c.execute("SELECT id, queries FROM coding_worksheet WHERE patient_id=%s AND status='OPEN'", (chart.patient_id,)).fetchall():
            if w["queries"]:
                blockers.append(f"Coding worksheet {w['id']}: {len(w['queries'])} documentation query(ies) for the clinician")
    flags = [f.title for f in dashboard.flags_for(chart) if f.level == "attention"]
    blockers += [f"Flag: {t}" for t in flags]
    return Readiness(patient_id=chart.patient_id, encounter_id=stay.id if stay else None, generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     agents=ordered, blockers=blockers, flags=flags)


def store(r: Readiness, chart: Chart, principal: Principal) -> None:
    with db.session(principal) as c:
        c.execute("INSERT INTO readiness (patient_id, unit, payload, created_by) VALUES (%s,%s,%s,%s)", (r.patient_id, chart.unit, r.model_dump_json(), principal.user))


def latest(patient_id: str, principal: Principal) -> dict | None:
    with db.session(principal) as c:
        row = c.execute("SELECT payload FROM readiness WHERE patient_id=%s ORDER BY id DESC LIMIT 1", (patient_id,)).fetchone()
    return row["payload"] if row else None
