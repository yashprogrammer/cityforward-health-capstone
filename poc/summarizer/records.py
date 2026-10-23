"""Load a Synthea / FHIR R4 patient bundle into a plain, typed patient record.

A Synthea file is a FHIR *transaction bundle*: one JSON file, thousands of resources, linked by `urn:uuid:` references
(and by `Practitioner?identifier=...` search-style references for people and places that live in other files).
Loading it takes three steps: (1) index the resources, (2) resolve references, (3) flatten into the few things a
summarizer actually needs. Everything here is deterministic Python: no AI yet.
"""
import base64
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, Field

SYS_ICD10 = "http://hl7.org/fhir/sid/icd-10-cm"
SYS_SCT = "http://snomed.info/sct"


class Encounter(BaseModel):
    id: str
    cls: str                     # IMP (inpatient), EMER (emergency), AMB (ambulatory), HH, VR
    type: str
    start: str
    end: str | None = None
    reason: str | None = None
    practitioner: str | None = None


class Condition(BaseModel):
    id: str
    display: str
    icd10: str | None = None
    snomed: str | None = None
    onset: str | None = None
    encounter: str | None = None


class Observation(BaseModel):
    id: str
    name: str
    loinc: str | None = None
    value: float | None = None
    unit: str = ""
    date: str
    category: str                # laboratory | vital-signs
    status: str = "final"
    flag: str = ""               # H / L when the source says so
    encounter: str | None = None


class Medication(BaseModel):
    id: str
    name: str
    rxnorm: str | None = None
    status: str
    authored: str | None = None
    sig: str = ""
    encounter: str | None = None


class Procedure(BaseModel):
    id: str
    name: str
    snomed: str | None = None
    date: str | None = None
    encounter: str | None = None


class Note(BaseModel):
    id: str
    date: str
    author: str = ""
    kind: str = ""
    text: str
    encounter: str | None = None


class PatientRecord(BaseModel):
    patient_id: str
    name: str
    given: str
    birth_date: str
    sex: str
    mrn: str | None = None
    city: str = ""
    payer: str | None = None
    encounters: list[Encounter] = Field(default_factory=list)
    conditions: list[Condition] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    medications: list[Medication] = Field(default_factory=list)
    procedures: list[Procedure] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)

    # ---- derived views -------------------------------------------------------------------------------
    @property
    def as_of(self) -> str:
        """The date of the most recent thing in the chart ("now" for this patient)."""
        dates = [e.start for e in self.encounters] + [n.date for n in self.notes] + [o.date for o in self.observations]
        return max(dates)[:10]

    @property
    def age(self) -> int:
        b, a = date.fromisoformat(self.birth_date), date.fromisoformat(self.as_of)
        return a.year - b.year - ((a.month, a.day) < (b.month, b.day))

    def problem_list(self) -> list[Condition]:
        """Distinct diagnoses, earliest onset kept, most recent first."""
        seen: dict[str, Condition] = {}
        for c in sorted(self.conditions, key=lambda c: c.onset or ""):
            seen.setdefault(c.icd10 or c.snomed or c.display, c)
        return sorted(seen.values(), key=lambda c: c.onset or "", reverse=True)

    def active_medications(self) -> list[Medication]:
        seen: dict[str, Medication] = {}
        for m in sorted(self.medications, key=lambda m: m.authored or ""):
            if m.status == "active":
                seen[m.rxnorm or m.name] = m
        return list(seen.values())

    def latest_by_test(self, category: str = "laboratory") -> list[tuple[Observation, Observation | None]]:
        """For each test: (latest result, the one before it)."""
        by_test: dict[str, list[Observation]] = defaultdict(list)
        for o in self.observations:
            if o.category == category and (o.loinc or o.name):
                by_test[o.loinc or o.name].append(o)
        out = []
        for obs in by_test.values():
            obs.sort(key=lambda o: o.date)
            out.append((obs[-1], obs[-2] if len(obs) > 1 else None))
        return sorted(out, key=lambda t: t[0].date, reverse=True)


def _key(ref: str | None) -> str | None:
    """`urn:uuid:abc` -> abc ; `Practitioner?identifier=system|123` -> npi:123 ; `Patient/9` -> 9."""
    if not ref:
        return None
    if ref.startswith("urn:uuid:"):
        return ref[9:]
    if ref.startswith("Practitioner?identifier="):
        return "npi:" + ref.split("|")[-1]
    return ref.split("/")[-1]


def _code(cc: dict | None, system_suffix: str) -> str | None:
    return next((c["code"] for c in (cc or {}).get("coding", []) if c.get("system", "").endswith(system_suffix)), None)


def _display(cc: dict | None) -> str:
    cc = cc or {}
    return cc.get("text") or next((c.get("display") for c in cc.get("coding", []) if c.get("display")), "") or ""


def load_bundle(source: str | Path | dict) -> PatientRecord:
    bundle = source if isinstance(source, dict) else json.loads(Path(source).read_text())
    res = [e["resource"] for e in bundle["entry"]]
    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in res:
        by_type[r["resourceType"]].append(r)

    people = {}
    for pr in by_type["Practitioner"]:
        npi = next((i["value"] for i in pr.get("identifier", [])), None)
        nm = pr["name"][0]
        people["npi:" + str(npi)] = f"Dr. {' '.join(nm.get('given', []))} {nm.get('family', '')}".strip()

    p = by_type["Patient"][0]
    nm = p["name"][0]
    addr = (p.get("address") or [{}])[0]
    mrn = next((i["value"] for i in p.get("identifier", []) if any(c.get("code") == "MR" for c in i.get("type", {}).get("coding", []))), None)
    cov = by_type["Coverage"]
    rec = PatientRecord(
        patient_id=p["id"], name=f"{' '.join(nm.get('given', []))} {nm.get('family', '')}".strip(), given=" ".join(nm.get("given", [])),
        birth_date=p["birthDate"], sex={"male": "M", "female": "F"}.get(p.get("gender"), "?"), mrn=mrn, city=addr.get("city", ""),
        payer=(cov[0].get("payor") or [{}])[0].get("display") if cov else None)

    for e in by_type["Encounter"]:
        prov = next((people.get(_key(pt.get("individual", {}).get("reference"))) for pt in e.get("participant", [])), None)
        rec.encounters.append(Encounter(
            id=e["id"], cls=e["class"]["code"], type=_display((e.get("type") or [{}])[0]) or "Visit", start=e["period"]["start"], end=e["period"].get("end"),
            reason=_display((e.get("reasonCode") or [{}])[0]) or None, practitioner=prov))
    for c in by_type["Condition"]:
        rec.conditions.append(Condition(
            id=c["id"], display=_display(c["code"]).replace(" (disorder)", ""), icd10=_code(c["code"], "icd-10-cm"), snomed=_code(c["code"], "sct"),
            onset=c.get("onsetDateTime") or c.get("recordedDate"), encounter=_key((c.get("encounter") or {}).get("reference"))))
    for o in by_type["Observation"]:
        cats = {cd["code"] for cat in o.get("category", []) for cd in cat.get("coding", [])}
        category = "vital-signs" if "vital-signs" in cats else "laboratory" if "laboratory" in cats else None
        if category is None or not o.get("effectiveDateTime"):
            continue
        parts = [(c["code"]["coding"][0], c.get("valueQuantity")) for c in o["component"]] if o.get("component") else [(o["code"]["coding"][0], o.get("valueQuantity"))]
        flag = next((cd["code"] for i in o.get("interpretation", []) for cd in i.get("coding", []) if cd["code"] in ("H", "L")), "")
        for n, (coding, q) in enumerate(parts):
            name = (coding.get("display") if o.get("component") else _display(o["code"])) or ""
            rec.observations.append(Observation(
                id=f"{o['id']}.{n}" if o.get("component") else o["id"], name=name.split(" [")[0], loinc=coding.get("code"),
                value=q.get("value") if q else None, unit=(q or {}).get("unit", ""), date=o["effectiveDateTime"], category=category,
                status=o.get("status", "final"), flag=flag, encounter=_key((o.get("encounter") or {}).get("reference"))))
    for m in by_type["MedicationRequest"]:
        cc = m.get("medicationCodeableConcept") or {}
        di = (m.get("dosageInstruction") or [{}])[0]
        rec.medications.append(Medication(
            id=m["id"], name=_display(cc), rxnorm=_code(cc, "rxnorm"), status=m.get("status", "unknown"), authored=m.get("authoredOn"),
            sig=di.get("text", ""), encounter=_key((m.get("encounter") or {}).get("reference"))))
    for pr in by_type["Procedure"]:
        when = (pr.get("performedPeriod") or {}).get("start") or pr.get("performedDateTime")
        rec.procedures.append(Procedure(id=pr["id"], name=_display(pr["code"]).replace(" (procedure)", ""), snomed=_code(pr["code"], "sct"), date=when,
                                        encounter=_key((pr.get("encounter") or {}).get("reference"))))
    for d in by_type["DocumentReference"]:
        att = d["content"][0]["attachment"]
        if "data" not in att or not att.get("contentType", "").startswith("text/"):
            continue   # scanned PDFs are not text; the integrated service handles them differently
        kind = (d.get("type", {}).get("coding") or [{}])[0].get("display", "")
        rec.notes.append(Note(id=d["id"], date=d["date"], author=(d.get("author") or [{}])[0].get("display", ""), kind=kind,
                              text=base64.b64decode(att["data"]).decode().strip(), encounter=_key(((d.get("context") or {}).get("encounter") or [{}])[0].get("reference"))))
    rec.encounters.sort(key=lambda e: e.start)
    rec.notes.sort(key=lambda n: n.date)
    return rec


def list_bundles(folder: str | Path) -> list[tuple[str, Path]]:
    """[(label, path)] for every patient bundle in a folder, golden patients first."""
    out = []
    for f in sorted(Path(folder).glob("*.json")):
        try:
            r = load_bundle(f)
        except (KeyError, IndexError, json.JSONDecodeError):
            continue
        out.append((f"{f.stem}  {r.name}, {r.age}{r.sex}", f))
    return out
