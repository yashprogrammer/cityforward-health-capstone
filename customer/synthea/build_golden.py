"""Hand-author the three 'golden' demo patients as Synthea-style FHIR bundles (run once by the course author).

    python build_golden.py            # writes prepared/bundles/GLD-00N.json and prepends them to prepared/manifest.json

Synthea's inpatient stays are clinically thin, so the demo and the evals rely on three stays written by a person:
  GLD-001  Robert Hale      62 M  anterior STEMI, PCI                -> discharge summary, coding, risk flags, payer documentation gap
  GLD-002  Margaret Doyle   79 F  hip fracture, hemiarthroplasty     -> prior authorization: skilled nursing facility + walker (DME)
  GLD-003  James Whitaker   71 M  COPD exacerbation, hypoxemia       -> prior authorization: home oxygen (DME), risk flags
Codes are real (SNOMED CT / LOINC / RxNorm / ICD-10-CM) where we verified them against Synthea's own output; otherwise the
code is left out rather than guessed. All people are fictional.
"""
import base64
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
NS = uuid.UUID("5d1b7d2e-0c63-4f51-9a43-2610c17f0a01")
ANCHOR = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc)
SCT, LNC, RXN, ICD = "http://snomed.info/sct", "http://loinc.org", "http://www.nlm.nih.gov/research/umls/rxnorm", "http://hl7.org/fhir/sid/icd-10-cm"

ATTENDINGS = {  # synthetic NPIs start with 9999
    "raman": ("9999900001", "Priya", "Raman"), "okafor": ("9999900002", "Michael", "Okafor"),
    "whitfield": ("9999900003", "Laura", "Whitfield"), "reyes": ("9999900004", "Daniel", "Reyes"),
    "tran": ("9999900005", "Emily", "Tran"),
}


def uid(*parts) -> str:
    return str(uuid.uuid5(NS, "|".join(str(p) for p in parts)))


def iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def at(days_ago: float, hh: int, mm: int = 0) -> datetime:
    return (ANCHOR - timedelta(days=days_ago)).replace(hour=hh, minute=mm, second=0, microsecond=0)


class Builder:
    def __init__(self, key: str, name: tuple, dob: str, sex: str, ssn: str, phone: str, addr: tuple, payer: str | None):
        self.key, self.res = key, []
        self.pid = uid(key, "patient")
        self.mrn = "CF" + str(100000 + int(uuid.uuid5(NS, key + "mrn").int % 899999))
        self.payer = payer
        self.res.append({
            "resourceType": "Patient", "id": self.pid,
            "meta": {"profile": ["http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient"]},
            "identifier": [{"use": "usual", "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0203", "code": "MR"}]},
                            "system": "urn:oid:2.16.840.1.113883.19.5", "value": self.mrn},
                           {"system": "http://hl7.org/fhir/sid/us-ssn", "value": ssn}],
            "name": [{"use": "official", "family": name[1], "given": [name[0]]}],
            "telecom": [{"system": "phone", "value": phone, "use": "home"}],
            "gender": "male" if sex == "M" else "female", "birthDate": dob,
            "address": [{"line": [addr[0]], "city": addr[1], "state": "OH", "postalCode": addr[2], "country": "US"}]})
        for who in ATTENDINGS.values():
            self._practitioner(*who)
        if payer:
            self.member = ("M" if payer == "Medicare" else "W") + f"{int(uuid.uuid5(NS, key + 'mbr').int % 10**9):09d}"
            self.res.append({"resourceType": "Coverage", "id": uid(key, "cov"), "status": "active", "type": {"text": payer},
                             "subscriberId": self.member, "beneficiary": {"reference": "urn:uuid:" + self.pid},
                             "payor": [{"display": payer}], "class": [{"type": {"coding": [{"code": "plan"}]}, "value": payer}]})
        else:
            self.member = None

    def _practitioner(self, npi, given, family):
        self.res.append({"resourceType": "Practitioner", "id": uid("pr", npi), "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": npi}],
                         "active": True, "name": [{"family": family, "given": [given], "prefix": ["Dr."]}]})

    def ref(self, rid):
        return {"reference": "urn:uuid:" + rid}

    def encounter(self, key, cls, start, end, text, reason=None, who="raman"):
        rid = uid(self.key, "enc", key)
        npi, given, family = ATTENDINGS[who]
        r = {"resourceType": "Encounter", "id": rid, "status": "finished" if end else "in-progress",
             "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": cls},
             "type": [{"coding": [{"system": SCT, "code": "0", "display": text}], "text": text}],
             "subject": self.ref(self.pid), "period": {"start": iso(start)},
             "participant": [{"type": [{"text": "primary performer"}],
                              "individual": {"reference": f"Practitioner?identifier=http://hl7.org/fhir/sid/us-npi|{npi}", "display": f"Dr. {given} {family}"}}]}
        r["type"][0]["coding"] = []
        if end:
            r["period"]["end"] = iso(end)
        if reason:
            r["reasonCode"] = [{"coding": [{"system": SCT, "code": reason[0], "display": reason[1]}]}]
        self.res.append(r)
        return rid

    def condition(self, enc, key, snomed, text, icd, when, icd_text=None):
        coding = ([{"system": SCT, "code": snomed[0], "display": snomed[1]}] if snomed else [])
        if icd:
            coding.append({"system": ICD, "code": icd, "display": icd_text or text})
        self.res.append({"resourceType": "Condition", "id": uid(self.key, "cond", key),
                         "clinicalStatus": {"coding": [{"code": "active"}]}, "verificationStatus": {"coding": [{"code": "confirmed"}]},
                         "category": [{"coding": [{"code": "encounter-diagnosis"}]}], "code": {"coding": coding, "text": (snomed[1] if snomed else text)},
                         "subject": self.ref(self.pid), "encounter": self.ref(enc), "onsetDateTime": iso(when), "recordedDate": iso(when)})

    def obs(self, enc, loinc, text, value, unit, when, vital=False, status="final", comp=None):
        r = {"resourceType": "Observation", "id": uid(self.key, "obs", enc, loinc, iso(when), unit, value),
             "status": status, "category": [{"coding": [{"code": "vital-signs" if vital else "laboratory"}]}],
             "code": {"coding": [{"system": LNC, "code": loinc, "display": text}], "text": text},
             "subject": self.ref(self.pid), "encounter": self.ref(enc), "effectiveDateTime": iso(when), "issued": iso(when)}
        if value is not None:
            r["valueQuantity"] = {"value": value, "unit": unit, "system": "http://unitsofmeasure.org", "code": unit}
        self.res.append(r)

    def bp(self, enc, sys_, dia, when):
        self.res.append({"resourceType": "Observation", "id": uid(self.key, "bp", enc, iso(when)), "status": "final",
                         "category": [{"coding": [{"code": "vital-signs"}]}],
                         "code": {"coding": [{"system": LNC, "code": "85354-9", "display": "Blood pressure panel"}], "text": "Blood Pressure"},
                         "subject": self.ref(self.pid), "encounter": self.ref(enc), "effectiveDateTime": iso(when),
                         "component": [{"code": {"coding": [{"system": LNC, "code": "8480-6", "display": "Systolic Blood Pressure"}]},
                                        "valueQuantity": {"value": sys_, "unit": "mm[Hg]"}},
                                       {"code": {"coding": [{"system": LNC, "code": "8462-4", "display": "Diastolic Blood Pressure"}]},
                                        "valueQuantity": {"value": dia, "unit": "mm[Hg]"}}]})

    def vitals(self, enc, when, hr=None, sbp=None, dbp=None, rr=None, temp=None, spo2=None):
        if hr: self.obs(enc, "8867-4", "Heart rate", hr, "/min", when, vital=True)
        if sbp: self.bp(enc, sbp, dbp, when)
        if rr: self.obs(enc, "9279-1", "Respiratory rate", rr, "/min", when, vital=True)
        if temp: self.obs(enc, "8310-5", "Body temperature", temp, "Cel", when, vital=True)
        if spo2: self.obs(enc, "59408-5", "Oxygen saturation in Arterial blood by Pulse oximetry", spo2, "%", when, vital=True)

    def procedure(self, enc, key, code, text, when, minutes=30):
        coding = [{"system": SCT, "code": code, "display": text}] if code else []
        self.res.append({"resourceType": "Procedure", "id": uid(self.key, "proc", key), "status": "completed",
                         "code": {"coding": coding, "text": text}, "subject": self.ref(self.pid), "encounter": self.ref(enc),
                         "performedPeriod": {"start": iso(when), "end": iso(when + timedelta(minutes=minutes))}})

    def med(self, enc, key, text, when, rx=None, status="active", dose=None, freq=None):
        r = {"resourceType": "MedicationRequest", "id": uid(self.key, "med", key), "status": status, "intent": "order",
             "medicationCodeableConcept": {"coding": ([{"system": RXN, "code": rx, "display": text}] if rx else []), "text": text},
             "subject": self.ref(self.pid), "encounter": self.ref(enc), "authoredOn": iso(when)}
        if dose or freq:
            r["dosageInstruction"] = [{"text": " ".join(x for x in (dose, freq) if x)}]
        self.res.append(r)

    def note(self, enc, key, when, who_text, text, nursing=False):
        loinc = ("34746-8", "Nurse Note") if nursing else ("11506-3", "Progress note")
        self.res.append({"resourceType": "DocumentReference", "id": uid(self.key, "note", key), "status": "current",
                         "type": {"coding": [{"system": LNC, "code": loinc[0], "display": loinc[1]}]},
                         "category": [{"coding": [{"code": "clinical-note"}]}], "subject": self.ref(self.pid), "date": iso(when),
                         "author": [{"display": who_text}], "context": {"encounter": [self.ref(enc)]},
                         "content": [{"attachment": {"contentType": "text/plain; charset=utf-8",
                                                     "data": base64.b64encode(text.strip().encode()).decode()}}]})

    def bundle(self):
        entries = [{"fullUrl": "urn:uuid:" + r["id"], "resource": r, "request": {"method": "POST", "url": r["resourceType"]}} for r in self.res]
        return {"resourceType": "Bundle", "type": "transaction", "entry": entries}


# ============================================================================ GLD-001  Robert Hale, anterior STEMI
def robert_hale():
    b = Builder("gld-001", ("Robert", "Hale"), "1964-03-09", "M", "999-41-7702", "614-555-0142", ("2218 Maplewood Drive", "Columbus", "43214"), "Summit Health Plan")
    # history: two PCP visits
    pcp1 = b.encounter("pcp1", "AMB", at(251, 14, 0), at(251, 14, 30), "Office visit - hypertension follow-up", who="okafor")
    b.condition(pcp1, "htn", (("59621000", "Essential hypertension (disorder)")), None, "I10", at(2900, 9), "Essential (primary) hypertension")
    b.condition(pcp1, "hld", (("55822004", "Hyperlipidemia (disorder)")), None, "E78.5", at(1800, 9), "Hyperlipidemia, unspecified")
    b.condition(pcp1, "tob", None, "Tobacco use (smokes about 1 pack/day)", "F17.210", at(9000, 9), "Nicotine dependence, cigarettes, uncomplicated")
    b.bp(pcp1, 148, 92, at(251, 14, 5)); b.vitals(pcp1, at(251, 14, 5), hr=84)
    b.obs(pcp1, "18262-6", "Cholesterol in LDL [Mass/volume] in Serum or Plasma", 162, "mg/dL", at(251, 14, 0))
    b.med(pcp1, "amlo", "amLODIPine 5 MG Oral Tablet", at(1500, 9), rx="197361", dose="5 mg", freq="once daily")
    b.med(pcp1, "ator10", "atorvastatin 10 MG Oral Tablet", at(1700, 9), rx="617312", dose="10 mg", freq="nightly", status="stopped")
    b.note(pcp1, "pcp1n", at(251, 14, 30), "Dr. Michael Okafor",
           "62M with hypertension and hyperlipidemia, smokes about a pack a day. BP 148/92 on amlodipine 5 mg, taking it irregularly. "
           "LDL 162 on atorvastatin 10 mg, adherence poor. Counselled on smoking cessation; declines pharmacotherapy. "
           "Plan: restart daily amlodipine, increase atorvastatin to 40 mg, recheck lipids in 3 months.")
    pcp2 = b.encounter("pcp2", "AMB", at(120, 10, 0), at(120, 10, 25), "Office visit - follow-up", who="okafor")
    b.bp(pcp2, 142, 88, at(120, 10, 5)); b.vitals(pcp2, at(120, 10, 5), hr=80)
    b.note(pcp2, "pcp2n", at(120, 10, 25), "Dr. Michael Okafor",
           "Follow-up. Missed lipid recheck. Still smoking. BP 142/88. Reinforced adherence; patient reports occasional exertional chest tightness "
           "on stairs over the past 2 weeks, resolves with rest. Stress test ordered, patient to schedule.")
    # ED visit + admission
    ed = b.encounter("ed", "EMER", at(5, 6, 5), at(5, 6, 40), "Emergency room admission", reason=("401303003", "Acute ST segment elevation myocardial infarction (disorder)"))
    ip = b.encounter("ip", "IMP", at(5, 6, 40), None, "Hospital admission", reason=("401303003", "Acute ST segment elevation myocardial infarction (disorder)"))
    b.condition(ed, "stemi", ("401303003", "Acute ST segment elevation myocardial infarction (disorder)"), None, "I21.02",
                at(5, 6, 5), "ST elevation (STEMI) myocardial infarction involving left anterior descending coronary artery")
    b.vitals(ed, at(5, 6, 8), hr=96, sbp=152, dbp=94, rr=20, spo2=96)
    b.procedure(ed, "ecg", "29303009", "Electrocardiographic procedure (procedure)", at(5, 6, 12), 10)
    b.obs(ed, "89579-7", "Troponin I.cardiac [Mass/volume] in Serum or Plasma by High sensitivity method", 1840, "ng/L", at(5, 6, 20))
    b.obs(ed, "718-7", "Hemoglobin [Mass/volume] in Blood", 13.2, "g/dL", at(5, 6, 20))
    b.obs(ed, "2160-0", "Creatinine [Mass/volume] in Serum or Plasma", 1.1, "mg/dL", at(5, 6, 20))
    b.obs(ed, "2823-3", "Potassium [Moles/volume] in Serum or Plasma", 4.2, "mmol/L", at(5, 6, 20))
    b.obs(ed, "2345-7", "Glucose [Mass/volume] in Serum or Plasma", 118, "mg/dL", at(5, 6, 20))
    b.note(ed, "ed_hp", at(5, 6, 30), "Dr. Emily Tran",
           "ED H&P. 62M, hypertension, hyperlipidemia, active smoker (20 pack-years). Retrosternal chest pain since 04:30 radiating to the left arm "
           "with diaphoresis. ECG: ST elevation V1-V4 with reciprocal inferior changes. hs-troponin I 1,840 ng/L. Aspirin 325 mg chewed, ticagrelor "
           "180 mg load, heparin bolus given. Cardiology (Dr. Raman) activated the cath lab. Consent for emergent angiography/PCI obtained from patient and wife.")
    b.procedure(ip, "cag", "33367005", "Angiography of coronary artery (procedure)", at(5, 7, 55), 25)
    b.procedure(ip, "pci", "415070008", "Percutaneous coronary intervention", at(5, 8, 15), 40)
    b.procedure(ip, "echo", "40701008", "Echocardiography (procedure)", at(4, 11, 0), 30)
    b.note(ip, "cath", at(5, 9, 30), "Dr. Priya Raman",
           "Coronary angiography via right radial: proximal LAD 99% thrombotic occlusion, left circumflex 40% non-critical, RCA normal. Primary PCI with one "
           "drug-eluting stent (3.0 x 28 mm) to the proximal LAD, TIMI III flow restored. Door-to-balloon 98 minutes. Uncomplicated. Transferred to CCU. "
           "Implant: Xience 3.0 x 28 mm, lot recorded on the implant sticker (see device log).")
    b.obs(ip, "89579-7", "Troponin I.cardiac [Mass/volume] in Serum or Plasma by High sensitivity method", 14250, "ng/L", at(5, 14, 30))
    b.obs(ip, "13969-1", "Creatine kinase.MB [Mass/volume] in Serum or Plasma", 86, "ng/mL", at(5, 14, 30))
    b.obs(ip, "18262-6", "Cholesterol in LDL [Mass/volume] in Serum or Plasma", 162, "mg/dL", at(4, 6, 30))
    b.obs(ip, "4548-4", "Hemoglobin A1c/Hemoglobin.total in Blood", 6.1, "%", at(4, 6, 30))
    b.obs(ip, "33762-6", "Natriuretic peptide.B prohormone N-Terminal [Mass/volume] in Serum or Plasma", 880, "pg/mL", at(4, 6, 30))
    b.obs(ip, "89579-7", "Troponin I.cardiac [Mass/volume] in Serum or Plasma by High sensitivity method", 3100, "ng/L", at(2, 6, 30))
    b.obs(ip, "2160-0", "Creatinine [Mass/volume] in Serum or Plasma", 1.0, "mg/dL", at(2, 6, 30))
    b.obs(ip, "10835-7", "Lipoprotein (a) [Mass/volume] in Serum or Plasma", None, "mg/dL", at(2, 6, 30), status="registered")
    for d, (hr, s, dd, sp) in {4: (78, 126, 80, 97), 3: (74, 124, 78, 97), 2: (72, 128, 80, 98), 1: (70, 122, 76, 98)}.items():
        b.vitals(ip, at(d, 8, 0), hr=hr, sbp=s, dbp=dd, rr=16, spo2=sp)
    for key, text, rx, dose, freq in [("asa", "aspirin 81 MG Oral Tablet", "243670", "81 mg", "once daily"),
                                      ("tica", "ticagrelor 90 MG Oral Tablet", None, "90 mg", "twice daily"),
                                      ("ator", "atorvastatin 80 MG Oral Tablet", "259255", "80 mg", "nightly"),
                                      ("meto", "24 HR metoprolol succinate 25 MG Extended Release Oral Tablet", "866427", "25 mg", "once daily"),
                                      ("lisin", "lisinopril 2.5 MG Oral Tablet", "311353", "2.5 mg", "once daily"),
                                      ("panto", "pantoprazole 40 MG Delayed Release Oral Tablet", None, "40 mg", "once daily before breakfast"),
                                      ("ntg", "Nitroglycerin 0.4 MG/ACTUAT Mucosal Spray", "705129", "0.4 mg", "as needed for chest pain")]:
        b.med(ip, key, text, at(5, 9, 0), rx=rx, dose=dose, freq=freq)
    b.med(ip, "enox", "enoxaparin 60 MG/0.6ML Injectable Solution", at(5, 9, 0), dose="60 mg", freq="subcutaneous every 12 hours for 2 days", status="completed")
    b.note(ip, "pn2", at(4, 10, 0), "Dr. Priya Raman",
           "POD1 after primary PCI. Pain free, haemodynamically stable. BP 126/80, HR 78. No arrhythmia on telemetry. Echo: LVEF 40%, anterior wall hypokinesis, "
           "no mitral regurgitation, no LV thrombus. Amlodipine stopped; lisinopril 2.5 mg started for LV dysfunction. Smoking cessation counselling done; "
           "patient motivated to quit.")
    b.note(ip, "pn3", at(3, 10, 15), "Dr. Emily Tran",
           "Transferred from CCU to 4 West. Ambulating with support. Repeat troponin trending down. Lipoprotein(a) sent to the reference lab, result awaited.")
    b.note(ip, "pn5", at(1, 10, 30), "Dr. Priya Raman",
           "Stable, pain free, ambulating independently. Radial access site healthy. Plan discharge tomorrow if stable. Cardiac rehabilitation referral. "
           "Cardiology follow-up in 1 week with the pending Lp(a) result. Dual antiplatelet therapy for 12 months, high-intensity statin, beta-blocker, ACE inhibitor.")
    b.note(ip, "rn1", at(5, 14, 10), "Maria Lopez, RN", "Post-PCI right radial site: no hematoma, distal pulses intact. TR band removed at 14:00. Vitals stable.", nursing=True)
    b.note(ip, "rn2", at(4, 20, 0), "Maria Lopez, RN", "Vitals stable. Tolerating cardiac diet. Patient anxious about returning to work (bank teller); reassured.", nursing=True)
    b.note(ip, "rn3", at(1, 16, 0), "James Park, RN", "Ambulating in corridor, no complaints. Dietitian counselling completed: low sodium, low saturated fat, smoking cessation.", nursing=True)
    return b, dict(case="GLD-001", unit="4W-CARD", bed="12", status="ADM", admit=at(5, 6, 40), who="raman", reason="Acute ST segment elevation myocardial infarction (disorder)",
                   disposition=None, docs=["ID_PROOF", "INS_CARD", "CONSENT", "CATH_RPT"], advise=None, discharge=None)


# ============================================================================ GLD-002  Margaret Doyle, hip fracture
def margaret_doyle():
    b = Builder("gld-002", ("Margaret", "Doyle"), "1947-06-21", "F", "999-52-1186", "614-555-0177", ("77 Alder Court", "Worthington", "43085"), "Lakeshore Medicare Advantage")
    pcp = b.encounter("pcp", "AMB", at(190, 9, 30), at(190, 10, 0), "Office visit - annual wellness", who="okafor")
    b.condition(pcp, "htn", ("59621000", "Essential hypertension (disorder)"), None, "I10", at(4000, 9), "Essential (primary) hypertension")
    b.condition(pcp, "osteo", ("64859006", "Osteoporosis (disorder)"), None, "M81.0", at(900, 9), "Age-related osteoporosis without current pathological fracture")
    b.condition(pcp, "hypoth", None, "Hypothyroidism", "E03.9", at(3200, 9), "Hypothyroidism, unspecified")
    b.bp(pcp, 138, 80, at(190, 9, 40)); b.vitals(pcp, at(190, 9, 40), hr=72)
    b.obs(pcp, "1989-3", "25-hydroxyvitamin D [Mass/volume] in Serum or Plasma", 14, "ng/mL", at(190, 9, 30))
    b.med(pcp, "levo", "levothyroxine sodium 75 MCG Oral Tablet", at(3000, 9), dose="75 mcg", freq="once daily")
    b.med(pcp, "amlo", "amLODIPine 2.5 MG Oral Tablet", at(2000, 9), rx="308136", dose="2.5 mg", freq="once daily")
    b.note(pcp, "pcpn", at(190, 10, 0), "Dr. Michael Okafor",
           "79F, lives alone in a two-story home. Osteoporosis, vitamin D 14 (low). Fall risk discussed. Advised vitamin D and calcium supplementation; "
           "declined bisphosphonate for now. Uses a cane outdoors.")
    ed = b.encounter("ed", "EMER", at(6, 17, 20), at(6, 17, 55), "Emergency room admission", reason=("359817006", "Closed fracture of hip (disorder)"), who="whitfield")
    ip = b.encounter("ip", "IMP", at(6, 18, 10), None, "Hospital admission", reason=("359817006", "Closed fracture of hip (disorder)"), who="whitfield")
    b.condition(ed, "fx", ("359817006", "Closed fracture of hip (disorder)"), None, "S72.001A", at(6, 17, 20),
                "Fracture of unspecified part of neck of right femur, initial encounter for closed fracture")
    b.vitals(ed, at(6, 17, 25), hr=88, sbp=146, dbp=84, rr=18, spo2=96)
    b.procedure(ed, "xr", "713021002", "Plain X-ray of pelvis (procedure)", at(6, 17, 40), 15)
    b.obs(ed, "718-7", "Hemoglobin [Mass/volume] in Blood", 11.8, "g/dL", at(6, 17, 45))
    b.obs(ed, "2160-0", "Creatinine [Mass/volume] in Serum or Plasma", 1.0, "mg/dL", at(6, 17, 45))
    b.obs(ed, "6301-6", "INR in Platelet poor plasma by Coagulation assay", 1.0, "{INR}", at(6, 17, 45))
    b.note(ed, "ed_hp", at(6, 17, 50), "Dr. Emily Tran",
           "ED H&P. 79F, mechanical fall at home about 3 hours ago, unable to bear weight on the right leg. Right leg shortened and externally rotated. "
           "X-ray pelvis: displaced right femoral neck fracture. Neurovascularly intact. Hypertension, osteoporosis, hypothyroidism. Orthopaedics consulted.")
    b.procedure(ip, "hemi", None, "Right hip hemiarthroplasty (bipolar)", at(5, 11, 0), 95)
    b.procedure(ip, "pt", "91251008", "Physical therapy procedure (regime/therapy)", at(4, 10, 0), 40)
    b.procedure(ip, "pt2", "91251008", "Physical therapy procedure (regime/therapy)", at(2, 10, 0), 40)
    b.obs(ip, "718-7", "Hemoglobin [Mass/volume] in Blood", 9.4, "g/dL", at(5, 16, 0))
    b.obs(ip, "718-7", "Hemoglobin [Mass/volume] in Blood", 9.6, "g/dL", at(3, 6, 0))
    b.obs(ip, "2160-0", "Creatinine [Mass/volume] in Serum or Plasma", 0.9, "mg/dL", at(3, 6, 0))
    b.obs(ip, "2951-2", "Sodium [Moles/volume] in Serum or Plasma", 138, "mmol/L", at(3, 6, 0))
    b.obs(ip, "2823-3", "Potassium [Moles/volume] in Serum or Plasma", 4.0, "mmol/L", at(3, 6, 0))
    for d, (hr, s, dd, sp) in {5: (92, 118, 70, 95), 4: (84, 124, 74, 96), 3: (80, 128, 76, 96), 2: (78, 130, 78, 96), 1: (76, 132, 78, 97)}.items():
        b.vitals(ip, at(d, 8, 0), hr=hr, sbp=s, dbp=dd, rr=16, spo2=sp, temp=36.8)
    for key, text, rx, dose, freq, st in [("apap", "Acetaminophen 325 MG Oral Tablet", "313782", "650 mg", "every 6 hours", "active"),
                                          ("tram", "tramadol hydrochloride 50 MG Oral Tablet", "835603", "50 mg", "every 6 hours as needed, 5 day supply", "active"),
                                          ("enox", "enoxaparin 40 MG/0.4ML Injectable Solution", None, "40 mg", "subcutaneous once daily for 28 days", "active"),
                                          ("vitd", "cholecalciferol 2000 UNT Oral Tablet", None, "2000 units", "once daily", "active"),
                                          ("ca", "calcium carbonate 500 MG Oral Tablet", None, "500 mg", "twice daily", "active"),
                                          ("levo", "levothyroxine sodium 75 MCG Oral Tablet", None, "75 mcg", "once daily", "active"),
                                          ("amlo", "amLODIPine 2.5 MG Oral Tablet", "308136", "2.5 mg", "once daily", "active"),
                                          ("cefaz", "cefazolin 1 GM Injection", None, "2 g", "IV before surgery and every 8 hours x 3 doses", "completed")]:
        b.med(ip, key, text, at(5, 9, 0), rx=rx, dose=dose, freq=freq, status=st)
    b.note(ip, "op", at(5, 12, 45), "Dr. Laura Whitfield",
           "Operative note. Right hip bipolar hemiarthroplasty, posterior approach, spinal anaesthesia. Estimated blood loss 400 mL. No intra-operative "
           "complications. Weight-bearing as tolerated with hip precautions. Post-op Hb 9.4; one unit packed red cells transfused.")
    b.note(ip, "pn3", at(3, 10, 0), "Dr. Emily Tran",
           "POD2. Pain controlled on scheduled acetaminophen with tramadol as needed. Hb 9.6 after transfusion. Wound clean and dry. "
           "PT evaluation: ambulates 20 feet with a rolling walker and moderate assistance of one; needs help with transfers. Lives alone.")
    b.note(ip, "pt", at(2, 11, 0), "Anna Cho, PT",
           "Physical therapy re-evaluation. Patient ambulated 40 feet with a front-wheeled (rolling) walker and minimal-to-moderate assistance of one; "
           "unable to safely manage stairs. Bedroom is on the second floor of her home. Requires skilled physical and occupational therapy daily to reach "
           "a safe level of independence. Patient needs a rolling walker for mobility in the home once discharged.")
    b.note(ip, "cm", at(1, 14, 0), "Rachel Kim, RN Case Manager",
           "Discharge planning. Patient cannot return home alone: needs daily skilled therapy and hands-on help with transfers and stairs. Family (daughter) "
           "agrees to skilled nursing facility placement; Maple Grove Rehabilitation has a bed. Qualifying inpatient stay is 3 or more midnights. "
           "Payer requires prior authorization for the facility stay and for the walker.")
    b.note(ip, "rn1", at(4, 21, 0), "Maria Lopez, RN", "Post-op day 1. Incision dressing dry. Neurovascularly intact. Up to chair with 1 assist.", nursing=True)
    b.note(ip, "rn2", at(2, 20, 0), "James Park, RN", "Slept well. Pain 3/10 with medication. Family visited; discussed rehab placement.", nursing=True)
    return b, dict(case="GLD-002", unit="5S-ORTH", bed="03", status="ADM", admit=at(6, 18, 10), who="whitfield", reason="Closed fracture of hip (disorder)",
                   disposition=None, docs=["ID_PROOF", "INS_CARD", "CONSENT", "OP_NOTE"], advise=None, discharge=None)


# ============================================================================ GLD-003  James Whitaker, COPD exacerbation
def james_whitaker():
    b = Builder("gld-003", ("James", "Whitaker"), "1955-11-02", "M", "999-63-4421", "614-555-0123", ("418 Briar Lane", "Dublin", "43017"), "Summit Medicare Advantage")
    pcp = b.encounter("pcp", "AMB", at(75, 11, 0), at(75, 11, 30), "Office visit - COPD follow-up", who="okafor")
    b.condition(pcp, "copd", ("185086009", "Chronic obstructive bronchitis (disorder)"), None, "J44.9", at(2600, 9), "Chronic obstructive pulmonary disease, unspecified")
    b.condition(pcp, "htn", ("59621000", "Essential hypertension (disorder)"), None, "I10", at(3500, 9), "Essential (primary) hypertension")
    b.condition(pcp, "tob", ("449868002", "Smokes tobacco daily (finding)"), None, "F17.210", at(12000, 9), "Nicotine dependence, cigarettes, uncomplicated")
    b.vitals(pcp, at(75, 11, 5), hr=88, sbp=134, dbp=82, rr=20, spo2=91)
    b.procedure(pcp, "spiro", "127783003", "Spirometry (procedure)", at(75, 11, 10), 20)
    b.med(pcp, "alb", "NDA021457 200 ACTUAT albuterol 0.09 MG/ACTUAT Metered Dose Inhaler [ProAir]", at(1000, 9), rx="745752", dose="2 puffs", freq="every 4 to 6 hours as needed")
    b.med(pcp, "tio", "tiotropium bromide 18 MCG Inhalation Powder", at(900, 9), dose="1 capsule inhaled", freq="once daily")
    b.note(pcp, "pcpn", at(75, 11, 30), "Dr. Michael Okafor",
           "71M, COPD (spirometry: FEV1 48% predicted), current smoker, 40 pack-years. Two exacerbations treated with steroids this year. Resting SpO2 91% "
           "on room air in clinic. Continues tiotropium and albuterol. Smoking cessation advised again.")
    ed = b.encounter("ed", "EMER", at(4, 2, 50), at(4, 3, 25), "Emergency room admission", reason=("65710008", "Acute respiratory failure (disorder)"), who="okafor")
    ip = b.encounter("ip", "IMP", at(4, 3, 30), None, "Hospital admission", reason=("65710008", "Acute respiratory failure (disorder)"), who="okafor")
    b.condition(ed, "arf", ("65710008", "Acute respiratory failure (disorder)"), None, "J96.01", at(4, 2, 50), "Acute respiratory failure with hypoxia")
    b.condition(ed, "copdx", ("185086009", "Chronic obstructive bronchitis (disorder)"), None, "J44.1", at(4, 2, 50), "Chronic obstructive pulmonary disease with (acute) exacerbation")
    b.condition(ed, "hypox", ("389087006", "Hypoxemia (disorder)"), None, "R09.02", at(4, 2, 50), "Hypoxemia")
    b.vitals(ed, at(4, 2, 55), hr=112, sbp=148, dbp=88, rr=28, temp=37.4, spo2=84)
    b.procedure(ed, "cxr", "399208008", "Plain X-ray of chest (procedure)", at(4, 3, 5), 10)
    b.procedure(ed, "o2", "371908008", "Oxygen administration by mask (procedure)", at(4, 3, 0), 60)
    b.procedure(ed, "ecg", "29303009", "Electrocardiographic procedure (procedure)", at(4, 3, 8), 10)
    b.obs(ed, "2744-1", "pH of Arterial blood", 7.31, "[pH]", at(4, 3, 10))
    b.obs(ed, "2019-8", "Carbon dioxide [Partial pressure] in Arterial blood", 62, "mm[Hg]", at(4, 3, 10))
    b.obs(ed, "2703-7", "Oxygen [Partial pressure] in Arterial blood", 54, "mm[Hg]", at(4, 3, 10))
    b.obs(ed, "1960-4", "Bicarbonate [Moles/volume] in Arterial blood", 31, "mmol/L", at(4, 3, 10))
    b.obs(ed, "6690-2", "Leukocytes [#/volume] in Blood by Automated count", 13.2, "10*3/uL", at(4, 3, 10))
    b.obs(ed, "1988-5", "C reactive protein [Mass/volume] in Serum or Plasma", 48, "mg/L", at(4, 3, 10))
    b.obs(ed, "33959-8", "Procalcitonin [Mass/volume] in Serum or Plasma", 0.12, "ng/mL", at(4, 3, 10))
    b.obs(ed, "32693-4", "Lactate [Moles/volume] in Blood", 1.4, "mmol/L", at(4, 3, 10))
    b.obs(ed, "89579-7", "Troponin I.cardiac [Mass/volume] in Serum or Plasma by High sensitivity method", 12, "ng/L", at(4, 3, 10))
    b.obs(ed, "33762-6", "Natriuretic peptide.B prohormone N-Terminal [Mass/volume] in Serum or Plasma", 410, "pg/mL", at(4, 3, 10))
    b.note(ed, "ed_hp", at(4, 3, 20), "Dr. Emily Tran",
           "ED H&P. 71M with COPD (FEV1 48%), current smoker, 3 days of worsening dyspnea, productive cough with increased sputum, wheeze. Speaking in short "
           "sentences. SpO2 84% on room air, RR 28. ABG 7.31 / pCO2 62 / pO2 54. CXR: hyperinflation, no focal consolidation. Treated with continuous "
           "albuterol-ipratropium nebulisers, methylprednisolone 125 mg IV, azithromycin, controlled oxygen via mask. Admitted for acute exacerbation with hypoxic respiratory failure.")
    for d, (hr, s, dd, rr, sp, t) in {3: (98, 140, 84, 24, 90, 37.0), 2: (90, 136, 80, 22, 92, 36.9), 1: (86, 132, 80, 20, 93, 36.8)}.items():
        b.vitals(ip, at(d, 8, 0), hr=hr, sbp=s, dbp=dd, rr=rr, spo2=sp, temp=t)
    b.obs(ip, "59408-5", "Oxygen saturation in Arterial blood by Pulse oximetry (room air, at rest)", 87, "%", at(1, 9, 30), vital=True)
    b.obs(ip, "59408-5", "Oxygen saturation in Arterial blood by Pulse oximetry (room air, ambulating)", 84, "%", at(1, 9, 45), vital=True)
    b.obs(ip, "59408-5", "Oxygen saturation in Arterial blood by Pulse oximetry (2 L/min nasal cannula, ambulating)", 93, "%", at(1, 10, 0), vital=True)
    b.obs(ip, "6690-2", "Leukocytes [#/volume] in Blood by Automated count", 11.0, "10*3/uL", at(2, 6, 0))
    b.obs(ip, "2160-0", "Creatinine [Mass/volume] in Serum or Plasma", 0.9, "mg/dL", at(2, 6, 0))
    b.obs(ip, "2744-1", "pH of Arterial blood", 7.38, "[pH]", at(2, 6, 0))
    b.obs(ip, "2019-8", "Carbon dioxide [Partial pressure] in Arterial blood", 54, "mm[Hg]", at(2, 6, 0))
    for key, text, rx, dose, freq, st in [("duo", "albuterol 0.083 % / ipratropium bromide 0.017 % Inhalation Solution", None, "3 mL", "nebulised every 4 hours", "active"),
                                          ("pred", "predniSONE 20 MG Oral Tablet", None, "40 mg", "once daily for 5 days", "active"),
                                          ("azi", "azithromycin 250 MG Oral Tablet", None, "500 mg then 250 mg", "once daily for 5 days", "active"),
                                          ("tio", "tiotropium bromide 18 MCG Inhalation Powder", None, "1 capsule inhaled", "once daily", "active"),
                                          ("alb", "NDA021457 200 ACTUAT albuterol 0.09 MG/ACTUAT Metered Dose Inhaler [ProAir]", "745752", "2 puffs", "every 4 to 6 hours as needed", "active"),
                                          ("lisin", "lisinopril 10 MG Oral Tablet", "314076", "10 mg", "once daily", "active"),
                                          ("nic", "nicotine 21 MG/24HR Transdermal Patch", None, "21 mg", "once daily", "active")]:
        b.med(ip, key, text, at(4, 4, 0), rx=rx, dose=dose, freq=freq, status=st)
    b.note(ip, "pn2", at(3, 10, 0), "Dr. Michael Okafor",
           "Hospital day 2. Dyspnea improving on nebulisers and steroids. Off controlled mask, now 3 L/min nasal cannula, SpO2 90%. Afebrile, no infiltrate on CXR, "
           "procalcitonin low: azithromycin for 5 days only. Nicotine replacement started; patient wants to quit.")
    b.note(ip, "pn4", at(1, 10, 30), "Dr. Michael Okafor",
           "Hospital day 4. Clinically improved but still hypoxemic off oxygen. Tested today on room air: resting SpO2 87%, with ambulation 84%; "
           "on 2 L/min nasal cannula with ambulation 93%. Meets criteria for home oxygen (resting SpO2 at or below 88%). Medically optimised on tiotropium, "
           "albuterol, steroid course. Plan home with oxygen, pulmonary rehabilitation referral and outpatient follow-up in 1 week. "
           "Face-to-face evaluation done today by me.")
    b.note(ip, "rt", at(1, 11, 15), "Tom Reed, RRT",
           "Respiratory therapy: qualifying saturations documented on room air today (rest 87%, exertion 84%) and on 2 L/min (93%). Patient taught on "
           "inhaler technique and oxygen safety (no smoking near oxygen).", nursing=True)
    b.note(ip, "rn1", at(3, 21, 0), "James Park, RN", "Wheeze improving. Sleeping with head elevated. SpO2 92% on 3 L/min.", nursing=True)
    return b, dict(case="GLD-003", unit="3E-MED", bed="09", status="ADM", admit=at(4, 3, 30), who="okafor", reason="Acute respiratory failure (disorder)",
                   disposition=None, docs=["ID_PROOF", "INS_CARD", "CONSENT"], advise=None, discharge=None)


def main() -> None:
    outdir = os.path.join(HERE, "prepared")
    os.makedirs(outdir + "/bundles", exist_ok=True)
    mf_path = outdir + "/manifest.json"
    manifest = [m for m in (json.load(open(mf_path)) if os.path.exists(mf_path) else []) if not m["case"].startswith("GLD-")]
    golden = []
    for build in (robert_hale, margaret_doyle, james_whitaker):
        b, m = build()
        path = f"{outdir}/bundles/{m['case']}.json"
        json.dump(b.bundle(), open(path, "w"), separators=(",", ":"))
        npi = ATTENDINGS[m.pop("who")][0]
        ip = next(r for r in b.res if r["resourceType"] == "Encounter" and r["class"]["code"] == "IMP")
        golden.append(dict(m, file=f"bundles/{m['case']}.json", patient_id=b.pid, encounter_id=ip["id"], mrn=b.mrn, payer=b.payer,
                           member_id=b.member, attending_npi=npi, admit=m["admit"].strftime("%Y-%m-%d %H:%M"),
                           size_kb=round(os.path.getsize(path) / 1024), golden=True))
        print(m["case"], f"{golden[-1]['size_kb']} KB", len(b.res), "resources")
    json.dump(golden + manifest, open(mf_path, "w"), indent=1)


if __name__ == "__main__":
    main()
