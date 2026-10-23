"""A patient's chart as the AI layer sees it: MediTrack's FHIR resources flattened into typed lists, then into citable facts.

Everything downstream (retrieval, summaries, prior auth, coding, the dashboard's "Why" panel) cites a fact by `ref`, the FHIR
resource it came from, so a clinician can always click through to the source.
"""
import io
import re
from collections import defaultdict
from datetime import date

from pydantic import BaseModel, Field

from .fhir_client import FhirClient

EXT_WARD = "/ward-code"


class Encounter(BaseModel):
    id: str
    cls: str
    type: str
    start: str
    end: str | None = None
    status: str = ""
    reason: str | None = None
    unit: str | None = None
    location: str | None = None
    practitioner: str | None = None
    disposition: str | None = None


class Condition(BaseModel):
    ref: str
    display: str
    icd10: str | None = None
    status: str = "confirmed"
    onset: str | None = None
    encounter: str | None = None


class Observation(BaseModel):
    ref: str
    name: str
    loinc: str | None = None
    value: float | None = None
    unit: str = ""
    date: str
    category: str
    flag: str = ""
    encounter: str | None = None


class Medication(BaseModel):
    ref: str
    name: str
    rxnorm: str | None = None
    status: str
    category: str = ""
    authored: str | None = None
    sig: str = ""
    encounter: str | None = None


class Procedure(BaseModel):
    ref: str
    name: str
    snomed: str | None = None
    date: str | None = None
    encounter: str | None = None


class Document(BaseModel):
    ref: str
    kind: str                    # progress note | nurse note | discharge summary | scanned document type
    date: str
    author: str = ""
    text: str = ""
    encounter: str | None = None
    mime: str = "text/plain"


class Fact(BaseModel):
    ref: str
    kind: str
    when: str | None
    text: str
    encounter: str | None = None


class Chart(BaseModel):
    patient_id: str
    name: str
    birth_date: str
    sex: str
    payer: str | None = None
    member_id: str | None = None
    unit: str | None = None                      # ward of the current (or latest) inpatient stay
    encounters: list[Encounter] = Field(default_factory=list)
    conditions: list[Condition] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    medications: list[Medication] = Field(default_factory=list)
    procedures: list[Procedure] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)

    @property
    def as_of(self) -> str:
        ds = [e.start for e in self.encounters] + [d.date for d in self.documents] + [o.date for o in self.observations]
        return max(ds)[:10]

    @property
    def age(self) -> int:
        b, a = date.fromisoformat(self.birth_date), date.fromisoformat(self.as_of)
        return a.year - b.year - ((a.month, a.day) < (b.month, b.day))

    def current_encounter(self) -> Encounter | None:
        imp = [e for e in self.encounters if e.cls == "IMP"]
        return (imp or self.encounters or [None])[-1]

    def encounter(self, eid: str) -> Encounter | None:
        return next((e for e in self.encounters if e.id == eid), None)

    def active_medications(self) -> list[Medication]:
        seen: dict[str, Medication] = {}
        for m in sorted(self.medications, key=lambda m: m.authored or ""):
            if m.status == "active":
                seen[m.rxnorm or m.name] = m
        return list(seen.values())

    def problem_list(self) -> list[Condition]:
        seen: dict[str, Condition] = {}
        for c in sorted(self.conditions, key=lambda c: c.onset or ""):
            seen.setdefault(c.icd10 or c.display, c)
        return sorted(seen.values(), key=lambda c: c.onset or "", reverse=True)

    def test_series(self, category: str = "laboratory") -> dict[str, list[Observation]]:
        by: dict[str, list[Observation]] = defaultdict(list)
        for o in self.observations:
            if o.category == category:
                by[o.loinc or o.name].append(o)
        return {k: sorted(v, key=lambda o: o.date) for k, v in by.items()}

    # ------------------------------------------------------------------ facts
    def facts(self, note_chars: int = 700) -> list[Fact]:
        """The citable pieces of this chart: one short, self-contained sentence or paragraph each."""
        f: list[Fact] = [Fact(ref=f"Patient/{self.patient_id}", kind="patient", when=None,
                              text=f"Patient {self.name}, {self.age} year old {'male' if self.sex == 'M' else 'female'}; payer {self.payer or 'self-pay'}"
                                   + (f"; member id {self.member_id}" if self.member_id else ""))]
        for e in self.encounters:
            f.append(Fact(ref=f"Encounter/{e.id}", kind="encounter", when=e.start, encounter=e.id,
                          text=f"{e.type} ({e.cls}) {e.start[:10]} to {(e.end or 'ongoing')[:10]}" + (f", {e.location}" if e.location else "")
                               + (f"; reason: {e.reason}" if e.reason else "") + (f"; attending {e.practitioner}" if e.practitioner else "")
                               + (f"; discharge disposition: {e.disposition}" if e.disposition else "")))
        for c in self.conditions:
            f.append(Fact(ref=c.ref, kind="diagnosis", when=c.onset, encounter=c.encounter,
                          text=f"Diagnosis: {c.display}" + (f" [ICD-10-CM {c.icd10}]" if c.icd10 else "") + (" (provisional)" if c.status == "provisional" else "")))
        for p in self.procedures:
            f.append(Fact(ref=p.ref, kind="procedure", when=p.date, encounter=p.encounter,
                          text=f"Procedure {(p.date or '')[:10]}: {p.name}" + (f" [SNOMED CT {p.snomed}]" if p.snomed else "")))
        for m in self.medications:
            f.append(Fact(ref=m.ref, kind="medication", when=m.authored, encounter=m.encounter,
                          text=f"Medication ({m.category or 'order'}, {m.status}): {m.name}" + (f" - {m.sig}" if m.sig else "")))
        for cat, label in (("laboratory", "Lab"), ("vital-signs", "Vital")):
            for series in self.test_series(cat).values():
                last = series[-1]
                if last.value is None:
                    text = f"{label} {last.name}: NO RESULT YET (ordered {last.date[:10]}, pending)"
                else:
                    trend = ", ".join(f"{o.value:g} on {o.date[:10]}" for o in series[-4:] if o.value is not None)
                    text = f"{label} {last.name} ({last.unit}): {trend}" + (f"; flagged {last.flag}" if last.flag else "")
                f.append(Fact(ref=last.ref, kind="lab" if cat == "laboratory" else "vital", when=last.date, encounter=last.encounter, text=text))
        for d in self.documents:
            if not d.text:
                continue
            for i, part in enumerate(split_text(d.text, note_chars)):
                f.append(Fact(ref=d.ref, kind="note" if d.mime.startswith("text") else "document", when=d.date, encounter=d.encounter,
                              text=f"{d.kind} {d.date[:10]} by {d.author or 'unknown'}" + (f" (part {i + 1})" if i else "") + f": {part}"))
        return f


def split_text(text: str, limit: int) -> list[str]:
    """Break on paragraph boundaries into pieces of about `limit` characters, never mid-sentence if avoidable."""
    paras = [re.sub(r"[ \t]+", " ", p).strip() for p in re.split(r"\n\s*\n|\n(?=[A-Z][A-Za-z /]{2,30}:)", text) if p.strip()]
    out, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) > limit:
            out.append(cur)
            cur = ""
        while len(p) > limit * 1.5:
            cut = max(p.rfind(". ", 0, limit), limit // 2)
            out.append((cur + " " + p[:cut + 1]).strip() if cur else p[:cut + 1])
            cur, p = "", p[cut + 1:].strip()
        cur = f"{cur} {p}".strip() if cur else p
    if cur:
        out.append(cur)
    return out or [text.strip()]


# ---------------------------------------------------------------------------------------------- FHIR -> Chart
def _code(cc: dict | None, suffix: str) -> str | None:
    return next((c["code"] for c in (cc or {}).get("coding", []) if c.get("system", "").endswith(suffix)), None)


def _text(cc: dict | None) -> str:
    cc = cc or {}
    return cc.get("text") or next((c.get("display") for c in cc.get("coding", []) if c.get("display")), "") or ""


def _rid(ref: str | None) -> str | None:
    return ref.split("/")[-1] if ref else None


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    try:
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages).strip()
    except Exception:   # noqa: BLE001 - a corrupt scan must not break indexing of the rest of the chart
        return ""


def chart_from_fhir(resources: list[dict], fetch=None) -> Chart:
    """`fetch(url) -> (bytes, content_type)` retrieves attachments; leave None to skip document bodies."""
    by: dict[str, list[dict]] = defaultdict(list)
    for r in resources:
        by[r["resourceType"]].append(r)
    p = by["Patient"][0]
    nm = p["name"][0]
    cov = (by.get("Coverage") or [{}])[0]
    chart = Chart(patient_id=p["id"], name=f"{' '.join(nm.get('given', []))} {nm.get('family', '')}".strip(), birth_date=p["birthDate"],
                  sex={"male": "M", "female": "F"}.get(p.get("gender"), "?"),
                  payer=(cov.get("payor") or [{}])[0].get("display"), member_id=cov.get("subscriberId"))
    for e in by["Encounter"]:
        ext = {x["url"].rsplit("/", 1)[-1]: x for x in e.get("extension", [])}
        chart.encounters.append(Encounter(
            id=e["id"], cls=e["class"]["code"], type=_text((e.get("type") or [{}])[0]) or "Visit", start=e["period"]["start"], end=e["period"].get("end"),
            status=e.get("status", ""), reason=_text((e.get("reasonCode") or [{}])[0]) or None,
            unit=ext.get("ward-code", {}).get("valueCode"), location=((e.get("location") or [{}])[0].get("location") or {}).get("display"),
            practitioner=((e.get("participant") or [{}])[0].get("individual") or {}).get("display"),
            disposition=_text((e.get("hospitalization") or {}).get("dischargeDisposition"))))
    chart.encounters.sort(key=lambda e: e.start)
    stay = chart.current_encounter()
    chart.unit = stay.unit if stay else None
    for c in by["Condition"]:
        chart.conditions.append(Condition(
            ref=f"Condition/{c['id']}", display=_text(c["code"]), icd10=_code(c["code"], "icd-10-cm"),
            status=_code(c.get("verificationStatus"), "condition-ver-status") or "confirmed", onset=c.get("onsetDateTime") or c.get("recordedDate"),
            encounter=_rid((c.get("encounter") or {}).get("reference"))))
    for o in by["Observation"]:
        cats = {cd["code"] for cat in o.get("category", []) for cd in cat.get("coding", [])}
        category = "vital-signs" if "vital-signs" in cats else "laboratory" if "laboratory" in cats else None
        when = o.get("effectiveDateTime")
        if category is None or not when:
            continue
        flag = next((cd["code"] for i in o.get("interpretation", []) for cd in i.get("coding", []) if cd["code"] in ("H", "L")), "")
        parts = [(c["code"], c.get("valueQuantity")) for c in o["component"]] if o.get("component") else [(o["code"], o.get("valueQuantity"))]
        for n, (code, q) in enumerate(parts):
            name = _text(code).split(" [")[0]
            chart.observations.append(Observation(
                ref=f"Observation/{o['id']}", name=name, loinc=_code(code, "loinc.org"), value=(q or {}).get("value"), unit=(q or {}).get("unit", ""),
                date=when, category=category, flag=flag, encounter=_rid((o.get("encounter") or {}).get("reference"))))
    for m in by["MedicationRequest"]:
        cc = m.get("medicationCodeableConcept") or {}
        chart.medications.append(Medication(
            ref=f"MedicationRequest/{m['id']}", name=_text(cc), rxnorm=_code(cc, "rxnorm"), status=m.get("status", "unknown"),
            category=_code((m.get("category") or [{}])[0], "medicationrequest-category") or "", authored=m.get("authoredOn"),
            sig=((m.get("dosageInstruction") or [{}])[0]).get("text", ""), encounter=_rid((m.get("encounter") or {}).get("reference"))))
    for pr in by["Procedure"]:
        chart.procedures.append(Procedure(ref=f"Procedure/{pr['id']}", name=_text(pr["code"]), snomed=_code(pr["code"], "sct"),
                                          date=pr.get("performedDateTime") or (pr.get("performedPeriod") or {}).get("start"),
                                          encounter=_rid((pr.get("encounter") or {}).get("reference"))))
    for d in by["DocumentReference"]:
        att = d["content"][0]["attachment"]
        mime = att.get("contentType", "")
        kind = _text(d.get("type")) or "document"
        body = ""
        if fetch and att.get("url"):
            raw, _ = fetch(att["url"])
            body = raw.decode("utf-8", "replace").strip() if mime.startswith("text/") else _pdf_text(raw) if mime == "application/pdf" else ""
        chart.documents.append(Document(
            ref=f"DocumentReference/{d['id']}", kind=kind, date=d.get("date", ""), author=(d.get("author") or [{}])[0].get("display", ""), text=body,
            encounter=_rid(((d.get("context") or {}).get("encounter") or [{}])[0].get("reference")), mime=mime))
    chart.documents.sort(key=lambda d: d.date)
    return chart


def load_chart(fhir: FhirClient, patient_id: str) -> Chart:
    resources = fhir.everything(patient_id)
    resources += fhir.search("Coverage", patient=patient_id)
    return chart_from_fhir(resources, fetch=fhir.binary)
