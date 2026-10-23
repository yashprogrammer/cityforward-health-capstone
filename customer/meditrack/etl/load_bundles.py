"""Load the prepared FHIR bundles (Synthea + the hand-written golden patients) into MediTrack's legacy tables.

This is the "data migration" an EHR vendor would run: modern FHIR in, cryptic 2011 tables out. It is deliberately lossy
the way real migrations are: MediTrack keeps ICD-10-CM only (SNOMED conditions we cannot map are skipped), keeps lab values as text,
and has no vitals table, so vitals live in LAB_RSLT.
"""
import base64
import collections
import hashlib
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from ..backend.pharmacy import reconcile_discharge_meds
from . import mappings as M

IP_OVERRIDES = {"GLD-001": "IP2610-0142", "GLD-002": "IP2610-0151", "GLD-003": "IP2610-0138"}   # demo continuity with the webinar
PREFIX = {"IMP": "IP", "EMER": "ED", "AMB": "OP", "HH": "HH", "VR": "VR"}
SUFFIXES = (" (disorder)", " (procedure)", " (finding)", " (situation)", " (regime/therapy)", " (qualifier value)")
INSURED_QUERY_RATE = 0.38


def parse(s: str) -> datetime:
    """FHIR instant/date -> naive UTC datetime (all demo data is UTC)."""
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s if len(s) > 10 else s + "T00:00:00").replace(tzinfo=None)


def stable(s: str, mod: int) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest(), 16) % mod   # Python's hash() is randomized per process


def txt(d: datetime | None) -> str | None:
    return d.strftime("%Y-%m-%d %H:%M") if d else None


def clean(s: str) -> str:
    for suf in SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)]
    return s.strip()


def fmt_val(v: float) -> str:
    if v == int(v):
        return str(int(v))
    a = abs(v)
    return f"{v:.0f}" if a >= 100 else f"{v:.1f}" if a >= 10 else f"{v:.2f}".rstrip("0").rstrip(".")


def ref_id(ref: str | None) -> str | None:
    return ref.split(":")[-1] if ref and ref.startswith("urn:uuid:") else None


def timeline(rng: random.Random, insured: bool, p_query: float = INSURED_QUERY_RATE) -> dict:
    """Discharge-advised -> patient-out timeline. Insured patients wait on the payer's final approval; a documentation
    query from the payer adds hours. This is the pain the engagement is about."""
    if not insured:
        return dict(tat_h=rng.uniform(1.8, 3.2), status=None)
    submit = rng.uniform(1.8, 2.8)                       # writing the summary + final bill
    query = rng.random() < p_query
    resp = submit + (rng.uniform(4.0, 6.0) if query else rng.uniform(2.0, 3.4))
    return dict(tat_h=resp + rng.uniform(0.25, 0.65), submit_h=submit, resp_h=resp, status="QUERY" if query else "APPROVED",
                reason=rng.choice(M.AUTH_QUERY_REASONS) if query else None)


def ward_for(reason: str) -> str:
    r = reason.lower()
    for words, ward in ((("heart", "coronary", "infarction", "cardiac", "valve", "angina", "atrial"), "4W-CARD"),
                        (("appendic", "cholecyst", "colon", "hernia", "surgical", "transplant"), "2N-SURG"),
                        (("fracture", "skilled nursing", "knee", "hip", "tendon", "ligament", "joint"), "5S-ORTH")):
        if any(w in r for w in words):
            return ward
    return "3E-MED"


class Counter:
    def __init__(self):
        self.n = collections.defaultdict(int)

    def next(self, prefix: str, when: datetime, start: int = 200) -> str:
        key = (prefix, when.strftime("%y%m"))
        self.n[key] += 1
        return f"{prefix}{key[1]}-{start + self.n[key]:04d}"


def scanned_pdf(path: Path, title: str, ip_no: str, name: str, lines: list[str]) -> None:
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Courier", "B", 14)
    pdf.cell(0, 10, "CITYFORWARD HEALTH - COLUMBUS, OH", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Courier", "", 11)
    pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"Account: {ip_no}    Patient: {name}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    for line in lines or ["[scanned document]"]:
        pdf.multi_cell(0, 6, line.encode("latin-1", "replace").decode("latin-1"), new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))


DOC_TITLES = {"ID_PROOF": "Photo ID (scan)", "INS_CARD": "Insurance card (scan)", "CONSENT": "Informed consent form",
              "CATH_RPT": "Cardiac catheterization report", "DEVICE_LOG": "Implant record / device log", "OP_NOTE": "Operative report"}


def load_cases(conn, root: Path, docs_dir: Path) -> dict:
    manifest = json.load(open(root / "manifest.json"))
    counter = Counter()
    stats = collections.Counter()
    base = []   # (insured, tat_hours, query?) of the discharged cases, so the history can be calibrated around them
    for case in manifest:
        bundle = json.load(open(root / case["file"]))
        res = [e["resource"] for e in bundle["entry"]]
        by_type = collections.defaultdict(list)
        for r in res:
            by_type[r["resourceType"]].append(r)
        pat = by_type["Patient"][0]
        admit = parse(case["admit"])
        dob = date.fromisoformat(pat["birthDate"])
        addr = (pat.get("address") or [{}])[0]
        name = pat["name"][0]
        pt_name = " ".join(name["given"]) + " " + name["family"]
        pt_id = conn.execute(
            "INSERT INTO PT_MST (MRN, PT_NM, PT_DOB, PT_AGE, PT_SEX, PT_PHN, PT_ADDR, PT_CITY, PT_ST, PT_ZIP, PT_SSN) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING PT_ID",
            (case["mrn"], pt_name, pat["birthDate"], int((admit.date() - dob).days // 365.25), "M" if pat["gender"] == "male" else "F",
             next((t["value"] for t in pat.get("telecom", []) if t["system"] == "phone"), ""), " ".join(addr.get("line", [])),
             addr.get("city", ""), addr.get("state", ""), addr.get("postalCode", ""),
             next((i["value"] for i in pat.get("identifier", []) if "ssn" in i.get("system", "")), None))).fetchone()["PT_ID"]

        # practitioners -> DR_MST (demo attendings already exist)
        known = {d[3]: d[0] for d in M.DOCTORS}
        for pr in by_type["Practitioner"]:
            npi = pr["identifier"][0]["value"]
            if npi not in known:
                known[npi] = "D" + npi
                nm = pr["name"][0]
                conn.execute("INSERT INTO DR_MST VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                             ("D" + npi, f"Dr. {' '.join(nm['given'])} {nm['family']}", "General Practice", npi))
        attending = M.ATTENDING_BY_UNIT[case["unit"]]
        doc_name = {d[0]: d[1] for d in M.DOCTORS}

        encs = sorted(by_type["Encounter"], key=lambda e: e["period"]["start"])
        target = next(e for e in encs if e["id"] == case["encounter_id"])
        t_start = parse(target["period"]["start"])
        companion = next((e for e in encs if e["class"]["code"] == "EMER" and e["id"] != target["id"]
                          and t_start - timedelta(hours=3) <= parse(e["period"].get("end", e["period"]["start"])) <= t_start), None)
        enc_ip: dict[str, str] = {}
        enc_row: dict[str, dict] = {}
        insured = bool(case["payer"])
        tl = None
        if case["status"] == "DSCH":
            tl = timeline(random.Random(f"{case['case']}-tl"), insured)
            base.append((insured, tl["tat_h"], tl["status"]))
        for e in encs:
            if companion is not None and e["id"] == companion["id"]:
                enc_ip["urn:uuid:" + e["id"]] = None   # resolved to the admission below
                continue
            cls = e["class"]["code"]
            is_target = e["id"] == target["id"]
            prefix = PREFIX.get(cls, "OP")
            start = parse(e["period"]["start"])
            end = parse(e["period"]["end"]) if e["period"].get("end") else None
            ip_no = IP_OVERRIDES.get(case["case"]) if is_target and case["case"] in IP_OVERRIDES else counter.next(prefix, start)
            enc_ip["urn:uuid:" + e["id"]] = ip_no
            reason = clean(((e.get("reasonCode") or [{}])[0].get("coding") or [{}])[0].get("display") or e["type"][0].get("text", "Visit"))
            if is_target:
                adv = txt(parse(case["advise"])) if case["status"] == "DA" else None
                dsch = case["discharge"]
                if tl:
                    d_dt = parse(case["discharge"])
                    adv = txt(d_dt - timedelta(hours=tl["tat_h"]))
                row = (ip_no, pt_id, txt(start), case["unit"], f"{int(case['bed']):02d}", attending, "INSURED" if insured else "SELF_PAY",
                       case["payer"], case["member_id"], reason, case["status"], adv, dsch, case["disposition"], "IMP", e["type"][0].get("text", "Hospital admission"))
            else:
                doc = known.get(next((p["individual"]["reference"].split("|")[-1] for p in e.get("participant", [])), ""), attending)
                if cls == "IMP":
                    row = (ip_no, pt_id, txt(start), ward_for(reason), f"{1 + stable(ip_no, 14):02d}", doc, "INSURED" if insured else "SELF_PAY",
                           case["payer"], case["member_id"], reason, "DSCH", None, txt(end), "HOME", "IMP", e["type"][0].get("text", "")[:80])
                else:
                    row = (ip_no, pt_id, txt(start), "ED" if cls == "EMER" else "OPD", None, doc, "INSURED" if insured else "SELF_PAY",
                           case["payer"], case["member_id"], reason, "CLS", None, txt(end), None, {"EMER": "ED"}.get(cls, "OP" if cls not in ("HH", "VR") else cls),
                           e["type"][0].get("text", "")[:80])
            conn.execute("INSERT INTO IP_ADM_DTL (IP_NO, PT_ID, ADM_DT, WRD_CD, BED_NO, DR_ID, PAY_MODE, PYR_NM, MBR_ID, ADM_RSN, STS, "
                         "DSCH_ADV_DT, DSCH_DT, DSCH_DISP, ENC_CLS, ENC_TYP) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", row)
            enc_row[ip_no] = dict(start=start, doc=row[5])
            stats["visits"] += 1
        target_ip = enc_ip["urn:uuid:" + target["id"]]
        if companion is not None:
            enc_ip["urn:uuid:" + companion["id"]] = target_ip
        first_ip = min(enc_row, key=lambda k: enc_row[k]["start"])

        def ip_of(ref):
            return enc_ip.get(ref) if ref in enc_ip else first_ip

        # diagnoses
        seen_dx = set()
        for c in by_type["Condition"]:
            icd = next((x["code"] for x in c["code"]["coding"] if x["system"].endswith("icd-10-cm")), None)
            sct = next((x["code"] for x in c["code"]["coding"] if x["system"].endswith("sct")), None)
            icd = icd or M.SNOMED_TO_ICD10CM.get(sct or "")
            if not icd:
                stats["conditions_unmapped"] += 1
                continue
            ip_no = ip_of((c.get("encounter") or {}).get("reference"))
            if (ip_no, icd) in seen_dx:
                continue
            seen_dx.add((ip_no, icd))
            desc = clean(next((x.get("display") for x in c["code"]["coding"] if x["system"].endswith("icd-10-cm")), None) or c["code"].get("text", ""))
            typ = "PROV" if ip_no == target_ip and case["status"] == "ADM" else "FINAL"
            conn.execute("INSERT INTO IP_DIAG_DTL (IP_NO, ICD_CD, DIAG_DESC, DIAG_TYP) VALUES (%s,%s,%s,%s)", (ip_no, icd, desc, typ))
            stats["diagnoses"] += 1

        # procedures
        for p in by_type["Procedure"]:
            ip_no = ip_of((p.get("encounter") or {}).get("reference"))
            code = (p["code"].get("coding") or [{}])[0]
            when = parse(p["performedPeriod"]["start"]) if p.get("performedPeriod") else parse(p["performedDateTime"])
            conn.execute("INSERT INTO IP_PROC_DTL (IP_NO, PROC_CD, PROC_SYS, PROC_DESC, PROC_DT, SURGEON) VALUES (%s,%s,%s,%s,%s,%s)",
                         (ip_no, code.get("code", ""), "SCT" if code.get("code") else "", clean(p["code"].get("text", code.get("display", "")))[:120],
                          txt(when), doc_name.get(enc_row[ip_no]["doc"], enc_row[ip_no]["doc"])))
            stats["procedures"] += 1

        # labs + vitals
        for o in by_type["Observation"]:
            ip_no = ip_of((o.get("encounter") or {}).get("reference"))
            when = txt(parse(o["effectiveDateTime"]))
            parts = []
            if o.get("component"):
                parts = [(c["code"]["coding"][0], c.get("valueQuantity")) for c in o["component"]]
            else:
                parts = [(o["code"]["coding"][0], o.get("valueQuantity"))]
            for coding, q in parts:
                loinc = coding["code"]
                name_ = (o["code"].get("text") if not o.get("component") else coding.get("display")) or coding.get("display", "")
                name_ = name_.split(" [")[0][:70]
                pending = o["status"] in ("registered", "preliminary")
                if q is None and not pending:
                    continue
                v = q["value"] if q else None
                lo, hi = M.LAB_RANGES.get(loinc, (None, None))
                rng = ("%s-%s" % (lo, hi) if lo is not None and hi is not None else f"<{hi}" if hi is not None else f">{lo}" if lo is not None else "")
                flag = "" if v is None else ("L" if lo is not None and v < lo else "H" if hi is not None and v > hi else "")
                conn.execute("INSERT INTO LAB_RSLT (IP_NO, TST_NM, LOINC_CD, RSLT_VAL, UOM, REF_RNG, FLG, RSLT_DT, STS) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                             (ip_no, name_, loinc, "" if v is None else fmt_val(v), (q or {}).get("unit", ""), rng, flag, when, "PENDING" if pending else "FINAL"))
                stats["results"] += 1

        # medications
        home_seen = set()
        for m in by_type["MedicationRequest"]:
            mc = m.get("medicationCodeableConcept") or {}
            if not mc.get("text"):
                continue
            ip_no = ip_of((m.get("encounter") or {}).get("reference"))
            code = next((x["code"] for x in mc.get("coding", []) if x["system"].endswith("rxnorm")), None)
            di = (m.get("dosageInstruction") or [{}])[0]
            dose, freq = _dosage(di)
            route = "Oral" if "Oral" in mc["text"] else "Inhaled" if "Inhal" in mc["text"] else "Injection" if "Inject" in mc["text"] else "Topical" if "Topical" in mc["text"] else ""
            active = m["status"] == "active"
            if ip_no == target_ip:
                typ = "INPATIENT"
            elif active:
                typ = "HOME"
            else:
                typ = "OUTPT"
            if typ == "HOME":
                key = (code or mc["text"])
                if key in home_seen:
                    continue
                home_seen.add(key)
                if len(home_seen) > 12:
                    continue
                ip_no = target_ip   # the home medication list is taken at admission
            conn.execute("INSERT INTO PHR_ISS_DTL (IP_NO, DRUG_NM, RXN_CD, DOSE, FREQ, ROUTE, DUR, ISS_TYP, MED_STS) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                         (ip_no, mc["text"][:120], code, dose, freq, route, "", typ, "ACTIVE" if active else "DONE"))
            stats["medications"] += 1

        # notes
        for d in by_type["DocumentReference"]:
            ip_no = ip_of(((d.get("context", {}).get("encounter") or [{}])[0]).get("reference"))
            text = base64.b64decode(d["content"][0]["attachment"]["data"]).decode().strip()
            nursing = any(c.get("code") == "34746-8" for c in d["type"].get("coding", []))
            author = (d.get("author") or [{}])[0].get("display") or "Unknown"
            if not case.get("golden") and ip_no == target_ip:
                author = doc_name[attending]
            conn.execute("INSERT INTO CLN_NOTE (IP_NO, NOTE_DT, NOTE_TYP, AUTH, NOTE_TXT) VALUES (%s,%s,%s,%s,%s)",
                         (ip_no, txt(parse(d["date"])), "NRS" if nursing else "DR", author, text))
            stats["notes"] += 1

        # scanned documents for the admission
        docs = case.get("docs")
        if docs is None:
            docs = ["ID_PROOF", "CONSENT"] + (["INS_CARD"] if insured else [])
            pcodes = {(p["code"].get("coding") or [{}])[0].get("code") for p in by_type["Procedure"]
                      if ip_of((p.get("encounter") or {}).get("reference")) == target_ip}
            if pcodes & {"33367005", "415070008"}:
                docs.append("CATH_RPT")
            if "appendic" in case["reason"].lower():
                docs.append("OP_NOTE")
        notes_for_cath = [n for n in by_type["DocumentReference"]]
        for dt in docs:
            fn = f"{target_ip}_{dt}.pdf"
            lines = [f"[scanned document] {DOC_TITLES.get(dt, dt)}"]
            if dt == "CATH_RPT":
                lines = [base64.b64decode(n["content"][0]["attachment"]["data"]).decode().strip()[:900] for n in notes_for_cath
                         if "angiograph" in base64.b64decode(n["content"][0]["attachment"]["data"]).decode().lower()][:1] or lines
            scanned_pdf(docs_dir / fn, DOC_TITLES.get(dt, dt), target_ip, pt_name, lines)
            conn.execute("INSERT INTO PT_DOC (IP_NO, DOC_TYP, FILE_NM, UPL_DT, UPL_BY) VALUES (%s,%s,%s,%s,%s)", (target_ip, dt, fn, txt(admit), "HIM"))

        # discharge medication reconciliation (pharmacy does this when discharge is advised)
        if case["status"] in ("DA", "DSCH"):
            reconcile_discharge_meds(conn, target_ip)
        # payer authorization history for finished insured stays
        if tl and insured:
            d_dt = parse(case["discharge"])
            adv = d_dt - timedelta(hours=tl["tat_h"])
            conn.execute("INSERT INTO PYR_AUTH_DTL (IP_NO, SUBM_DT, AUTH_STS, QRY_RSN, RESP_DT) VALUES (%s,%s,%s,%s,%s)",
                         (target_ip, txt(adv + timedelta(hours=tl["submit_h"])), tl["status"], tl["reason"], txt(adv + timedelta(hours=tl["resp_h"]))))
        stats["cases"] += 1
    stats["base"] = base
    return stats


def _dosage(di: dict) -> tuple[str, str]:
    if di.get("text"):
        parts = di["text"].split(" ", 2)
        # "650 mg every 6 hours" -> dose "650 mg", frequency "every 6 hours"
        words = di["text"].split()
        dose = " ".join(words[:2]) if len(words) > 2 and words[0].replace(".", "").isdigit() else ""
        return dose, di["text"][len(dose):].strip() if dose else di["text"]
    rep = (di.get("timing") or {}).get("repeat") or {}
    f, p, u = rep.get("frequency"), rep.get("period"), rep.get("periodUnit")
    freq = ""
    if f and p and u == "d" and p == 1:
        freq = {1: "once daily", 2: "twice daily", 3: "three times daily", 4: "four times daily"}.get(f, f"{f} times daily")
    elif f and p and u:
        freq = f"{f} time(s) every {p:g} {u}"
    q = ((di.get("doseAndRate") or [{}])[0].get("doseQuantity") or {}).get("value")
    return (f"{q:g}" if q else ""), freq


def history_rows(rows: list[dict], seed: int, base: list) -> tuple[list[dict], dict]:
    """Admission-only history (older stays migrated from the previous system) + the stats the MIS report will show."""
    rng = random.Random(seed)
    start = datetime(2026, 6, 25, 8, 0)
    out = []
    tats = {True: [t for i, t, s in base if i], False: [t for i, t, s in base if not i]}
    queries = [s for i, t, s in base if s]
    for r in rows:
        insured = bool(r["payer"])
        adm = start + timedelta(hours=rng.uniform(0, 24 * 107))
        tl = timeline(rng, insured)
        adv = adm + timedelta(hours=max(12.0, r["los_hours"]))
        tats[insured].append(tl["tat_h"])
        if insured:
            queries.append(tl["status"])
        out.append(dict(r, adm=adm, adv=adv, tl=tl))
    stats = dict(self_pay=sum(tats[False]) / len(tats[False]), insured=sum(tats[True]) / len(tats[True]),
                 query_pct=100 * sum(1 for q in queries if q == "QUERY") / len(queries))
    return out, stats


def calibrate_history(rows: list[dict], base: list) -> tuple[int, list[dict], dict]:
    """Pick the first seed whose history reproduces the story's baseline: self-pay 2.5 h, insured 6.3 h, ~38% payer queries."""
    for seed in range(1, 20000):
        out, s = history_rows(rows, seed, base)
        if round(s["self_pay"], 1) == 2.5 and round(s["insured"], 1) == 6.3 and abs(s["query_pct"] - 38.0) <= 0.5:
            return seed, out, s
    raise RuntimeError("no seed reproduces the baseline; adjust the generator")


def load_history(conn, rows: list[dict], counter: Counter | None = None) -> None:
    counter = counter or Counter()
    for r in rows:
        pt_id = conn.execute(
            "INSERT INTO PT_MST (MRN, PT_NM, PT_DOB, PT_AGE, PT_SEX, PT_PHN, PT_ADDR, PT_CITY, PT_ST, PT_ZIP, PT_SSN) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING PT_ID",
            (r["mrn"], r["name"], r["dob"], int((r["adm"].date() - date.fromisoformat(r["dob"])).days // 365.25), r["sex"], r["phone"],
             r["addr"], r["city"], r["state"], r["zip"], r["ssn"])).fetchone()["PT_ID"]
        tl, adm, adv = r["tl"], r["adm"], r["adv"]
        insured = bool(r["payer"])
        ip_no = counter.next("IP", adm, start=500)
        reason = clean(r["reason"])
        conn.execute("INSERT INTO IP_ADM_DTL (IP_NO, PT_ID, ADM_DT, WRD_CD, BED_NO, DR_ID, PAY_MODE, PYR_NM, MBR_ID, ADM_RSN, STS, DSCH_ADV_DT, "
                     "DSCH_DT, DSCH_DISP, ENC_CLS, ENC_TYP) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'DSCH',%s,%s,%s,'IMP','Hospital admission')",
                     (ip_no, pt_id, txt(adm), ward_for(reason), f"{1 + stable(ip_no, 14):02d}", M.ATTENDING_BY_UNIT[ward_for(reason)],
                      "INSURED" if insured else "SELF_PAY", r["payer"], None, reason, txt(adv), txt(adv + timedelta(hours=tl["tat_h"])),
                      "SNF" if "skilled" in reason.lower() else "HOME"))
        if insured:
            conn.execute("INSERT INTO PYR_AUTH_DTL (IP_NO, SUBM_DT, AUTH_STS, QRY_RSN, RESP_DT) VALUES (%s,%s,%s,%s,%s)",
                         (ip_no, txt(adv + timedelta(hours=tl["submit_h"])), tl["status"], tl["reason"], txt(adv + timedelta(hours=tl["resp_h"]))))
