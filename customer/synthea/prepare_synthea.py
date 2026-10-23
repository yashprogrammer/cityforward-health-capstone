"""Turn a raw Synthea cohort into the small, committed demo data set (run once by the course author).

    python prepare_synthea.py /path/to/synthea/output  [--out prepared]

Input : the 'fhir' folder Synthea wrote (one FHIR R4 transaction bundle per patient + practitionerInformation*.json).
Output: prepared/bundles/<case>.json  one trimmed, date-shifted bundle per demo patient
        prepared/mis_history.json     admission-only rows (older stays migrated from the previous system)
        prepared/manifest.json        per-case facts MediTrack needs (unit, bed, status, dates, disposition)

What it does, and why:
  * picks ~20 clinically meaningful inpatient stays (heart attack, heart failure, appendicitis, COVID, ...)
  * keeps 24 months of history before the stay (labs, vitals, meds, procedures, notes) and drops billing noise
    (Claim, ExplanationOfBenefit, ...), which dominate Synthea's file size
  * shifts every date by whole days so the stay falls inside the demo window (age at the stay is preserved)
  * replaces real insurer names with fictional ones and adds a Coverage resource
Everything is deterministic: same Synthea output in, same files out.
"""
import argparse
import base64
import collections
import glob
import hashlib
import json
import os
import random
import re
from datetime import date, datetime, timedelta, timezone

ANCHOR = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc)   # "now" inside the demo data

# (reason shown on the Synthea inpatient encounter, how many cases, unit, discharge disposition, how many are current inpatients)
QUOTAS = [
    ("Acute ST segment elevation myocardial infarction (disorder)", 3, "4W-CARD", "HOME", 0),
    ("Acute non-ST segment elevation myocardial infarction (disorder)", 2, "4W-CARD", "HOME", 1),
    ("Chronic congestive heart failure (disorder)", 4, "4W-CARD", "HOME_HEALTH", 2),
    ("Appendicitis (disorder)", 2, "2N-SURG", "HOME", 1),
    ("Disease caused by severe acute respiratory syndrome coronavirus 2 (disorder)", 2, "3E-MED", "HOME", 1),
    ("Patient transfer to skilled nursing facility (procedure)", 3, "5S-ORTH", "SNF", 1),
    ("Non-small cell carcinoma of lung, TNM stage 1 (disorder)", 2, "3E-MED", "HOME", 1),
    ("Aortic valve stenosis (disorder)", 1, "4W-CARD", "HOME", 1),
]

PAYER_MAP = {   # real insurer names in Synthea -> fictional plans (Medicare / Medicaid are public programs)
    "Aetna": "Keystone Health Plan", "Anthem": "Lakeshore Mutual", "Cigna Health": "Pinnacle Care",
    "Humana": "Evergreen Health Plan", "UnitedHealthcare": "Summit Health Plan",
    "Blue Cross Blue Shield": "Northwind Health", "Dual Eligible": "Medicare", "Medicare": "Medicare",
    "Medicaid": "Medicaid",
}
KEEP_TYPES = {"Patient", "Encounter", "Condition", "Observation", "Procedure", "MedicationRequest", "DocumentReference",
              "AllergyIntolerance", "Immunization", "Device"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?)?$")
TEXT_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
HISTORY_MONTHS = 24
# (min linked resources, max linked resources, min age, max length of stay in days, earliest year)
LIMITS = {"default": (6, 320, 40, 8, 2016),
          "Appendicitis (disorder)": (2, 320, 18, 8, 2016),
          "Disease caused by severe acute respiratory syndrome coronavirus 2 (disorder)": (6, 3000, 40, 14, 2020)}
OBS_PER_CODE_STAY, OBS_PER_CODE_HISTORY = 8, 5   # keep the latest few values of each test: trends, not thousands of rows


def parse_dt(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    if len(s) == 10:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(s)


def res_time(r: dict) -> datetime | None:
    for k in ("effectiveDateTime", "authoredOn", "date", "recordedDate", "onsetDateTime", "occurrenceDateTime", "issued"):
        if r.get(k):
            return parse_dt(r[k])
    for k in ("period", "performedPeriod"):
        if r.get(k, {}).get("start"):
            return parse_dt(r[k]["start"])
    return None


def enc_ref(r: dict) -> str | None:
    ref = r.get("encounter")
    if r["resourceType"] == "DocumentReference":
        ref = (r.get("context", {}).get("encounter") or [None])[0]
    return ref.get("reference") if isinstance(ref, dict) else None


def shift_value(v, delta: timedelta):
    if isinstance(v, dict):
        return {k: shift_value(x, delta) for k, x in v.items()}
    if isinstance(v, list):
        return [shift_value(x, delta) for x in v]
    if isinstance(v, str) and DATE_RE.match(v):
        if len(v) == 10:
            return (date.fromisoformat(v) + delta).isoformat()
        d = parse_dt(v) + delta
        out = d.isoformat()
        return out if "." not in v else d.isoformat(timespec="milliseconds")
    return v


def shift_text(text: str, delta: timedelta) -> str:
    def sub(m):
        try:
            return (date(int(m[1]), int(m[2]), int(m[3])) + delta).isoformat()
        except ValueError:
            return m[0]
    return TEXT_DATE_RE.sub(sub, text)


def downsample_observations(resources: list[dict], stay_from: datetime) -> list[dict]:
    """Keep only the most recent few values of each test (separately for the stay and for the history before it)."""
    groups: dict[tuple, list[dict]] = collections.defaultdict(list)
    for r in resources:
        if r["resourceType"] == "Observation":
            code = r["code"]["coding"][0]["code"]
            groups[(code, (res_time(r) or stay_from) >= stay_from)].append(r)
    drop = set()
    for (code, in_stay), group in groups.items():
        group.sort(key=lambda r: res_time(r) or stay_from, reverse=True)
        drop.update(id(r) for r in group[(OBS_PER_CODE_STAY if in_stay else OBS_PER_CODE_HISTORY):])
    return [r for r in resources if id(r) not in drop]


def stable_int(s: str, mod: int) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest(), 16) % mod


def payer_for(resources: list[dict], bound: datetime) -> str | None:
    c = collections.Counter()
    for r in resources:
        if r["resourceType"] == "Claim":
            t = res_time(r) or parse_dt(r.get("created", "1900-01-01"))
            if bound - timedelta(days=370) <= t <= bound:
                c[r.get("insurance", [{}])[0].get("coverage", {}).get("display")] += 1
    if not c:
        for r in resources:
            if r["resourceType"] == "Claim":
                c[r.get("insurance", [{}])[0].get("coverage", {}).get("display")] += 1
    name = c.most_common(1)[0][0] if c else None
    if name in (None, "NO_INSURANCE"):
        return None
    return PAYER_MAP.get(name, "Pinnacle Care")


MA_PLANS = ["Summit Medicare Advantage", "Lakeshore Medicare Advantage", "Evergreen Medicare Advantage"]


def advantage(payer: str | None, key: str) -> str | None:
    """About half of the Medicare patients are on a (fictional) Medicare Advantage plan, which is where prior authorization lives."""
    if payer == "Medicare" and stable_int(key + "ma", 2) == 0:
        return MA_PLANS[stable_int(key, len(MA_PLANS))]
    return payer


def anonymize_note(text: str, payer: str | None) -> str:
    for real, fake in PAYER_MAP.items():
        text = text.replace(real, fake)
    if payer and "Advantage" in payer:
        text = text.replace("currently has Medicare.", f"currently has {payer}.")
    return text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("synthea_out")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "prepared"))
    args = ap.parse_args()
    fhir_dir = os.path.join(args.synthea_out, "fhir")
    files = sorted(f for f in glob.glob(fhir_dir + "/*.json") if not os.path.basename(f).startswith(("hospital", "practitioner")))
    files.sort(key=lambda f: hashlib.sha256(os.path.basename(f).encode()).hexdigest())   # deterministic shuffle

    practitioners = {}
    for pf in glob.glob(fhir_dir + "/practitionerInformation*.json"):
        for e in json.load(open(pf))["entry"]:
            r = e["resource"]
            if r["resourceType"] == "Practitioner":
                practitioners[r["identifier"][0]["value"]] = r

    # ---- pass 1: find candidate stays -------------------------------------------------
    # A Synthea hospital episode = an ED encounter (EMER) minutes before an inpatient encounter (IMP). Recent episodes
    # (2016+) carry the clinical content (procedures, labs, vitals, orders); older ones are nearly empty.
    need = {q[0]: q[1] for q in QUOTAS}
    cands: dict[str, list[dict]] = collections.defaultdict(list)
    stays_other: list[dict] = []
    seen_other = set()
    for f in files:
        b = json.load(open(f))
        res = [e["resource"] for e in b["entry"]]
        pat = next(r for r in res if r["resourceType"] == "Patient")
        died = parse_dt(pat["deceasedDateTime"]) if pat.get("deceasedDateTime") else None   # later death is cut off with the history
        linked = collections.Counter(enc_ref(r) for r in res if enc_ref(r) and r["resourceType"] != "DocumentReference")
        birth = date.fromisoformat(pat["birthDate"])
        encs = [r for r in res if r["resourceType"] == "Encounter"]
        for r in encs:
            if r["class"]["code"] != "IMP" or not r["period"].get("end"):
                continue
            start, end = parse_dt(r["period"]["start"]), parse_dt(r["period"]["end"])
            if died and end > died - timedelta(days=10):   # the stay that ended in death is not a discharge
                continue
            los = (end - start).total_seconds() / 86400
            rc = r.get("reasonCode") or [{}]
            reason = (rc[0].get("coding") or [{}])[0].get("display")
            age = (start.date() - birth).days / 365.25
            payer = advantage(payer_for(res, start), pat["id"])
            score = linked["urn:uuid:" + r["id"]]
            for o in encs:   # the ED visit that led to this admission
                if o["class"]["code"] == "EMER" and start - timedelta(hours=3) <= parse_dt(o["period"].get("end", o["period"]["start"])) <= start:
                    score += linked["urn:uuid:" + o["id"]]
            lo, hi, min_age, max_los, min_year = LIMITS.get(reason, LIMITS["default"])
            if reason in need and start.year >= min_year and 0.5 <= los <= max_los and age >= min_age and lo <= score <= hi:
                cands[reason].append(dict(file=f, enc=r["id"], reason=reason, start=start, end=end, payer=payer, score=score))
            elif pat["id"] not in seen_other and 0.5 <= los <= 9 and age >= 18 and len(stays_other) < 400:
                seen_other.add(pat["id"])
                stays_other.append(dict(file=f, enc=r["id"], reason=reason or "Hospital admission", start=start, end=end, payer=payer))
    for reason, n in need.items():
        print(f"  candidates for {reason[:50]:50}: {len(cands[reason]):3} (need {n})")
    picked: list[dict] = []
    used = set()
    for reason, n in need.items():
        for c in sorted(cands[reason], key=lambda c: (-c["score"], c["file"])):
            if n and c["file"] not in used:
                picked.append(c); used.add(c["file"]); n -= 1
        assert n == 0, f"not enough candidate stays for {reason}; generate a bigger Synthea cohort"

    # ---- plan statuses / dates ----------------------------------------------------------
    rng = random.Random(2610)
    plan = []
    current_left = {q[0]: q[4] for q in QUOTAS}
    unit_of = {q[0]: (q[2], q[3]) for q in QUOTAS}
    discharged_slots = sorted(rng.sample(range(10, 80), sum(q[1] for q in QUOTAS)))   # days before ANCHOR for admissions
    for i, p in enumerate(picked):
        unit, disp = unit_of[p["reason"]]
        los = p["end"] - p["start"]
        if current_left[p["reason"]] > 0:
            current_left[p["reason"]] -= 1
            status = "DA" if (p["reason"].startswith("Acute non-ST") or p["reason"].startswith("Patient transfer")) else "ADM"
            back = 4 if status == "DA" else rng.choice([1, 2, 3])
            admit = (ANCHOR - timedelta(days=back)).replace(hour=p["start"].hour, minute=p["start"].minute, second=0, microsecond=0)
        else:
            status = "DSCH"
            admit = (ANCHOR - timedelta(days=discharged_slots.pop())).replace(hour=p["start"].hour, minute=p["start"].minute, second=0, microsecond=0)
        plan.append(dict(p, unit=unit, disp=disp, status=status, admit=admit, bed=f"{1 + stable_int(p['enc'], 14):02d}",
                         delta=timedelta(days=(admit.date() - p["start"].date()).days)))
    # stable case ids: current patients first
    plan.sort(key=lambda x: (x["status"] == "DSCH", x["admit"]))

    # ---- pass 2: build trimmed + shifted bundles -----------------------------------------
    outdir = args.out
    os.makedirs(outdir + "/bundles", exist_ok=True)
    manifest = []
    for n, p in enumerate(plan, 1):
        case = f"SYN-{n:03d}"
        b = json.load(open(p["file"]))
        entries = {e["resource"]["id"]: e for e in b["entry"]}
        res = [e["resource"] for e in b["entry"]]
        pat = next(r for r in res if r["resourceType"] == "Patient")
        medications = {"urn:uuid:" + r["id"]: r["code"] for r in res if r["resourceType"] == "Medication"}
        for r in res:   # fold Medication resources into the request: one self-contained MedicationRequest
            ref = (r.get("medicationReference") or {}).get("reference")
            if r["resourceType"] == "MedicationRequest" and ref in medications:
                r["medicationCodeableConcept"] = medications[ref]
                r.pop("medicationReference")
        stay_start, stay_end = p["start"], p["end"]
        if p["status"] == "DSCH":
            bound = stay_end + timedelta(hours=2)
        else:   # still in hospital "now": nothing after the anchor exists yet
            bound = ANCHOR - p["delta"]
        window_start = stay_start - timedelta(days=30 * HISTORY_MONTHS)
        kept = []
        for r in res:
            t = r["resourceType"]
            if t not in KEEP_TYPES:
                continue
            when = res_time(r)
            if t == "Patient":
                kept.append(r); continue
            if t == "Condition":
                if "(disorder)" not in r["code"].get("text", ""):
                    continue
                if when and when > bound:
                    continue
                kept.append(r); continue
            if t == "AllergyIntolerance":
                kept.append(r); continue
            if t == "Device":
                if when is None or when <= bound:
                    kept.append(r)
                continue
            if t == "MedicationRequest" and r.get("status") == "active" and when and when <= bound:
                kept.append(r); continue   # long-running prescriptions are the home medication list
            if when is None or when > bound or when < window_start:
                continue
            if t == "Observation":
                cats = {c["code"] for cat in r.get("category", []) for c in cat.get("coding", [])}
                if not cats & {"laboratory", "vital-signs"}:
                    continue
            if t == "Encounter" and r["id"] == p["enc"] and p["status"] != "DSCH":
                r = json.loads(json.dumps(r)); r["period"].pop("end", None); r["status"] = "in-progress"
            kept.append(r)
        kept = downsample_observations(kept, stay_start - timedelta(hours=3))
        pat_id = pat["id"]
        mrn = f"CF{100000 + stable_int(pat_id, 899999)}"
        member = None
        shifted = []
        for r in kept:
            r = shift_value(json.loads(json.dumps(r)), p["delta"])
            for k in ("serviceProvider", "custodian", "location", "text", "provenance"):
                r.pop(k, None) if r["resourceType"] != "Patient" or k != "text" else r.pop("text", None)
            if r["resourceType"] == "Patient":
                ssn = [i for i in r.get("identifier", []) if "ssn" in i.get("system", "")]
                r["identifier"] = [{"use": "usual", "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0203", "code": "MR"}]},
                                    "system": "urn:oid:2.16.840.1.113883.19.5", "value": mrn}] + ssn
                r["extension"] = [e for e in r.get("extension", []) if e["url"].endswith(("us-core-race", "us-core-ethnicity", "us-core-birthsex"))]
                for k in ("deceasedDateTime", "multipleBirthBoolean", "multipleBirthInteger", "maritalStatus", "communication"):
                    r.pop(k, None)
            if r["resourceType"] == "DocumentReference":
                att = r["content"][0]["attachment"]
                txt = base64.b64decode(att["data"]).decode()
                txt = anonymize_note(shift_text(txt, p["delta"]), p["payer"])
                att["data"] = base64.b64encode(txt.encode()).decode()
            shifted.append(r)
        admit_end = stay_end + p["delta"]
        practitioner_npis = set()
        for r in shifted:
            for ref in json.dumps(r).split('"reference": "Practitioner?identifier=http://hl7.org/fhir/sid/us-npi|')[1:]:
                practitioner_npis.add(ref.split('"')[0])
        coverage = None
        if p["payer"]:
            member = f"{'M' if p['payer'] in ('Medicare', 'Medicaid') else 'W'}{stable_int(pat_id + 'mbr', 10**9):09d}"
            coverage = {"resourceType": "Coverage", "id": f"cov-{case.lower()}", "status": "active",
                        "type": {"text": p["payer"]}, "subscriberId": member,
                        "beneficiary": {"reference": "urn:uuid:" + pat_id},
                        "payor": [{"display": p["payer"]}], "class": [{"type": {"coding": [{"code": "plan"}]}, "value": p["payer"]}]}
        entries_out = [{"fullUrl": "urn:uuid:" + r["id"], "resource": r, "request": {"method": "POST", "url": r["resourceType"]}} for r in shifted]
        for npi in sorted(practitioner_npis):
            if npi in practitioners:
                pr = practitioners[npi]
                pr = {k: v for k, v in pr.items() if k not in ("extension", "telecom", "address")}
                entries_out.append({"fullUrl": "urn:uuid:" + pr["id"], "resource": pr, "request": {"method": "POST", "url": "Practitioner"}})
        if coverage:
            entries_out.append({"fullUrl": "urn:uuid:" + coverage["id"], "resource": coverage, "request": {"method": "POST", "url": "Coverage"}})
        bundle = {"resourceType": "Bundle", "type": "transaction", "entry": entries_out}
        json.dump(bundle, open(f"{outdir}/bundles/{case}.json", "w"), separators=(",", ":"))
        enc_res = next(r for r in shifted if r["resourceType"] == "Encounter" and r["id"] == p["enc"])
        admit = p["admit"]
        manifest.append(dict(
            case=case, file=f"bundles/{case}.json", patient_id=pat_id, encounter_id=p["enc"], mrn=mrn, reason=p["reason"],
            unit=p["unit"], bed=p["bed"], status=p["status"], admit=admit.strftime("%Y-%m-%d %H:%M"),
            discharge=None if p["status"] != "DSCH" else (admit + (stay_end - stay_start)).strftime("%Y-%m-%d %H:%M"),
            advise=None if p["status"] == "ADM" else ((ANCHOR.replace(hour=8, minute=30)) if p["status"] == "DA"
                                                      else (admit + (stay_end - stay_start) - timedelta(hours=3 if not p["payer"] else 6)))
                          .strftime("%Y-%m-%d %H:%M"),
            disposition=p["disp"] if p["status"] == "DSCH" else None, payer=p["payer"], member_id=member,
            attending_npi=next((pt.get("individual", {}).get("reference", "").split("|")[-1] for pt in enc_res.get("participant", [])), None),
            size_kb=round(os.path.getsize(f"{outdir}/bundles/{case}.json") / 1024)))
        print(f"{case} {p['status']:4} {p['unit']:8} {p['reason'][:55]:55} {manifest[-1]['size_kb']:5} KB  payer={p['payer']}")

    json.dump(manifest, open(f"{outdir}/manifest.json", "w"), indent=1)

    # ---- admission-only history (older stays migrated from the previous system) -------------
    history = []
    for s in stays_other:
        if s["file"] in used:
            continue
        b = json.load(open(s["file"]))
        pat = next(e["resource"] for e in b["entry"] if e["resource"]["resourceType"] == "Patient")
        addr = (pat.get("address") or [{}])[0]
        name = pat["name"][0]
        ssn = next((i["value"] for i in pat.get("identifier", []) if "ssn" in i.get("system", "")), None)
        history.append(dict(
            name=" ".join(name["given"]) + " " + name["family"], dob=pat["birthDate"], sex="M" if pat["gender"] == "male" else "F",
            phone=next((t["value"] for t in pat.get("telecom", []) if t["system"] == "phone"), ""),
            addr=" ".join(addr.get("line", [])), city=addr.get("city", ""), state=addr.get("state", ""), zip=addr.get("postalCode", ""),
            ssn=ssn, mrn=f"CF{100000 + stable_int(pat['id'], 899999)}", payer=s["payer"],
            reason=s["reason"], los_hours=round((s["end"] - s["start"]).total_seconds() / 3600, 1)))
    history = history[:150]
    json.dump(history, open(f"{outdir}/mis_history.json", "w"), indent=0)
    print(f"{len(manifest)} full cases, {len(history)} admission-only history rows -> {outdir}")


if __name__ == "__main__":
    main()
