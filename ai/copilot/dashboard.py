"""Data for the clinical dashboard (Build Session 6): a timeline of what happened and a few transparent, rule-based flags.

The flags are deliberately NOT predictions. Each one is a rule a clinician can read ("a lab was ordered and has no result"), fires on facts in the chart,
and shows its evidence. A flag that says "high readmission risk" would be a risk score, which would take the product out of the CDS exemption (see docs/compliance/fda-cds-memo.md).
These rules mirror the five reasons payers most often query a discharge (see the MIS report): they save the phone call, not the diagnosis.
"""
import re
from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import cds, db
from .access import authorize
from .auth import CurrentUser, Principal
from .chart import Chart
from .priorauth import SERVICE_SIGNALS

router = APIRouter(prefix="/api")


class Evidence(BaseModel):
    ref: str
    text: str


class Flag(BaseModel):
    id: str
    level: str                      # attention | info
    title: str
    rule: str                       # the rule, in words a clinician can check
    evidence: list[Evidence] = Field(default_factory=list)
    next_step: str = ""


class Event(BaseModel):
    when: str
    kind: str                       # encounter | procedure | lab | medication | note | document
    title: str
    detail: str = ""
    ref: str
    flag: str = ""                  # H | L | pending


def clip(text: str, n: int) -> str:
    """Shorten at a word boundary so a source quote never ends mid-number."""
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " …"


IMPLANT = re.compile(r"\b(implant|stent|prosthe|device log|pacemaker)\w*", re.I)
SURGICAL = re.compile(r"arthroplasty|percutaneous coronary|resection|appendectomy|bypass|replacement|fixation|repair of", re.I)
DISCHARGE_TALK = re.compile(r"discharge|home tomorrow|follow-?up in", re.I)


def _stay_docs(chart: Chart):
    stay = chart.current_encounter()
    return stay, [d for d in chart.documents if stay and d.encounter == stay.id and not d.generated]


def flags_for(chart: Chart) -> list[Flag]:
    stay, docs = _stay_docs(chart)
    if stay is None:
        return []
    out: list[Flag] = []
    planning = [d for d in docs if d.text and DISCHARGE_TALK.search(d.text)]
    for series in chart.test_series("laboratory").values():
        last = series[-1]
        if last.value is None and last.encounter == stay.id:
            f = Flag(id=f"pending-result:{last.ref}", level="attention", title=f"Result pending: {last.name}",
                     rule="A laboratory test was ordered during this stay and has no result yet.", evidence=[Evidence(ref=last.ref, text=f"{last.name} ordered {last.date[:10]}, no result")],
                     next_step="Document who will follow the result up after discharge.")
            if planning:
                f.title += " (discharge is being planned)"
                f.evidence.append(Evidence(ref=planning[-1].ref, text=clip(planning[-1].text, 300)))
            out.append(f)
    mentions = [d for d in docs if d.text and IMPLANT.search(d.text)]
    if mentions and not any(d.kind == "DEVICE_LOG" for d in docs):
        out.append(Flag(id="implant-record-missing", level="attention", title="Implant mentioned but no implant record filed",
                        rule="A note this stay mentions an implant or stent, and no DEVICE_LOG document (implant record) is filed for the stay.",
                        evidence=[Evidence(ref=mentions[0].ref, text=clip(mentions[0].text, 400))],
                        next_step="File the implant sticker/log: payers query discharges without it."))
    surgical = [p for p in chart.procedures if p.encounter == stay.id and SURGICAL.search(p.name)]
    if surgical and not any(d.kind in ("OP_NOTE", "CATH_RPT") for d in docs):
        out.append(Flag(id="operative-report-missing", level="attention", title="Procedure without an operative/procedure report",
                        rule="The stay has a surgical or interventional procedure and no OP_NOTE or CATH_RPT document is filed.",
                        evidence=[Evidence(ref=surgical[0].ref, text=surgical[0].name)], next_step="Ask the proceduralist to dictate or upload the report."))
    for c in chart.conditions:
        if c.encounter == stay.id and c.status == "provisional":
            out.append(Flag(id=f"provisional-dx:{c.ref}", level="info", title=f"Diagnosis still provisional: {c.display}",
                            rule="A diagnosis recorded for this stay is marked provisional in the EHR; coding and payers need it confirmed or removed.",
                            evidence=[Evidence(ref=c.ref, text=f"{c.display} [{c.icd10 or 'no code'}] provisional")], next_step="Confirm or revise before discharge summary and coding."))
    services = {code for d in docs if d.text for code, rx in SERVICE_SIGNALS.items() if rx.search(d.text)}
    if services:
        out.append(Flag(id="post-acute-service", level="info", title="Post-acute service mentioned: prior authorization may be needed",
                        rule="Notes this stay mention a skilled nursing facility, walker, home oxygen or home health, which many plans authorize in advance.",
                        evidence=[Evidence(ref=d.ref, text=clip(d.text, 300)) for d in docs if d.text and any(SERVICE_SIGNALS[s].search(d.text) for s in services)][:2],
                        next_step="Open the Prior authorization tab: drafts are prepared from the chart for your review."))
    out.sort(key=lambda f: (f.level != "attention", f.id))
    return out


def timeline_for(chart: Chart, limit: int = 120) -> list[Event]:
    ev: list[Event] = []
    for e in chart.encounters:
        ev.append(Event(when=e.start, kind="encounter", title=f"{e.type} ({e.cls})", detail=(e.reason or "") + (f" - {e.location}" if e.location else ""), ref=f"Encounter/{e.id}"))
    for p in chart.procedures:
        ev.append(Event(when=p.date or "", kind="procedure", title=p.name, ref=p.ref))
    for series in chart.test_series("laboratory").values():
        last = series[-1]
        if last.value is None:
            ev.append(Event(when=last.date, kind="lab", title=f"{last.name}: pending", detail="no result yet", ref=last.ref, flag="pending"))
        elif last.flag:
            trend = " → ".join(f"{o.value:g}" for o in series[-3:] if o.value is not None)
            ev.append(Event(when=last.date, kind="lab", title=f"{last.name} {last.value:g} {last.unit}", detail=trend if len(series) > 1 else "", ref=last.ref, flag=last.flag))
    for m in chart.medications:
        if m.category in ("inpatient", "discharge") and m.authored:
            ev.append(Event(when=m.authored, kind="medication", title=m.name, detail=m.sig, ref=m.ref))
    for d in chart.documents:
        if d.generated:
            ev.append(Event(when=d.date, kind="document", title=f"{d.kind} (filed by Copilot)", ref=d.ref))
        elif d.text and d.mime.startswith("text"):
            first = re.split(r"(?<=[.!?])\s", d.text.strip())[0]
            ev.append(Event(when=d.date, kind="note", title=f"{d.kind} by {d.author}", detail=first[:200], ref=d.ref))
        else:
            ev.append(Event(when=d.date, kind="document", title=d.kind, ref=d.ref))
    ev = [e for e in ev if e.when]
    ev.sort(key=lambda e: e.when)
    return ev[-limit:]


@router.get("/patients/{patient_id}/dashboard")
def dashboard(patient_id: str, p: Principal = CurrentUser):
    from .app import rt
    from .chart import load_chart
    from . import rag
    chart = load_chart(rt.get_fhir(), patient_id)
    authorize(p, "summary", patient_id, unit=chart.unit)
    rag.index_chart(chart, p)           # opening the dashboard keeps the index fresh
    stay = chart.current_encounter()
    los = (date.fromisoformat(chart.as_of) - date.fromisoformat(stay.start[:10])).days if stay else None
    with db.session(p) as c:
        pa = c.execute("SELECT id, service_code, service_name, status FROM prior_auth WHERE patient_id=%s ORDER BY created_at DESC", (patient_id,)).fetchall()
        cw = c.execute("SELECT id, status, jsonb_array_length(items) AS lines FROM coding_worksheet WHERE patient_id=%s ORDER BY created_at DESC", (patient_id,)).fetchall()
    flags = flags_for(chart)
    return {"patient": {"id": chart.patient_id, "name": chart.name, "age": chart.age, "sex": chart.sex, "payer": chart.payer, "unit": chart.unit,
                        "location": stay.location if stay else None, "encounter": stay.id if stay else None, "admitted": stay.start[:10] if stay else None, "los_days": los,
                        "status": stay.status if stay else None},
            "timeline": timeline_for(chart), "flags": flags, "prior_auth": pa, "coding": cw,
            "cds": cds.envelope("dashboard", [{"ref": e.ref, "text": e.text} for f in flags for e in f.evidence], generated=False)}
