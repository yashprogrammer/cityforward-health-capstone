"""MediTrack's FHIR R4 API (v3.4): the supported way for other systems to read the chart.

MediTrack's tables are cryptic and internal (/int/* is off limits). This module turns them into FHIR R4 resources
(US Core style) for the read interactions an AI application needs, plus DocumentReference create for write-back.
Every call needs a SMART access token (Authorization: Bearer ...); see smart.py.

Deliberately NOT exposed: SSN (minimum necessary), the audit trail, user accounts.
"""
import base64
import json
import os
import re
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from ..etl.mappings import UNIT_NAMES, VITAL_LOINC
from . import smart
from .db import DOCS_DIR, audit, connect, now, row, rows

router = APIRouter(prefix="/fhir")
FHIR_JSON = "application/fhir+json"
AUTH_ENABLED = os.getenv("FHIR_AUTH", "on") != "off"
BASE = smart.FHIR_BASE
SYS = {"icd10": "http://hl7.org/fhir/sid/icd-10-cm", "sct": "http://snomed.info/sct", "loinc": "http://loinc.org",
       "rxnorm": "http://www.nlm.nih.gov/research/umls/rxnorm", "ucum": "http://unitsofmeasure.org",
       "mrn": "urn:oid:2.16.840.1.113883.19.5", "npi": "http://hl7.org/fhir/sid/us-npi",
       "meditrack": "https://meditrack.cityforward.example/fhir/CodeSystem"}
CLASS_CODE = {"IMP": "IMP", "ED": "EMER", "OP": "AMB", "HH": "HH", "VR": "VR"}
DISPOSITION = {"HOME": ("home", "Home"), "HOME_HEALTH": ("alt-home", "Alternative home"), "SNF": ("snf", "Skilled nursing facility"),
               "REHAB": ("rehab", "Rehabilitation"), "EXPIRED": ("exp", "Expired")}
RESOURCES = ["Patient", "Encounter", "Condition", "Procedure", "Observation", "MedicationRequest", "DocumentReference",
             "Coverage", "Practitioner", "Organization"]


class FhirError(Exception):
    def __init__(self, status: int, code: str, text: str):
        self.status, self.code, self.text = status, code, text


def install(app) -> None:
    @app.exception_handler(FhirError)
    async def _handler(request, exc: FhirError):
        return outcome(exc.status, exc.code, exc.text)


def outcome(status: int, code: str, text: str) -> JSONResponse:
    return JSONResponse({"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": code, "diagnostics": text}]},
                        status_code=status, media_type=FHIR_JSON)


def respond(resource: dict, status: int = 200, headers: dict | None = None) -> Response:
    return Response(json.dumps(resource), status_code=status, media_type=FHIR_JSON, headers=headers)


# ---------------------------------------------------------------- auth
def auth(request: Request) -> dict:
    if not AUTH_ENABLED:
        return {"scope": "system/*.read system/*.write", "sub": "anonymous", "client_id": "anonymous"}
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("bearer "):
        raise FhirError(401, "login", "a SMART access token is required (Authorization: Bearer ...)")
    try:
        return smart.verify_access_token(h[7:])
    except Exception as e:   # noqa: BLE001 - any token problem is a 401
        raise FhirError(401, "security", f"invalid access token: {type(e).__name__}")


def need(claims: dict, rtype: str, action: str = "read", patient_id=None) -> None:
    if not smart.scope_allows(claims, rtype, action, patient_id):
        raise FhirError(403, "forbidden", f"token scope does not allow {action} on {rtype}" + (f" for patient {patient_id}" if patient_id else ""))


def log_access(claims: dict, rtype: str, rid: str) -> None:
    with connect() as c:
        audit(c, claims.get("sub", "?"), f"FHIR_READ:{rtype}", rid)


# ---------------------------------------------------------------- helpers
def iso(s: str | None) -> str | None:
    if not s:
        return None
    return s.replace(" ", "T") + ":00Z" if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", s) else s


def pid(v: str | None) -> str | None:
    return v.split("/")[-1] if v else None


def ref(rtype: str, rid) -> dict:
    return {"reference": f"{rtype}/{rid}"}


def bundle(entries: list[dict], total: int | None = None, self_url: str = "") -> dict:
    return {"resourceType": "Bundle", "type": "searchset", "total": len(entries) if total is None else total,
            **({"link": [{"relation": "self", "url": self_url}]} if self_url else {}),
            "entry": [{"fullUrl": f"{BASE}/{e['resourceType']}/{e['id']}", "resource": e, "search": {"mode": "match"}} for e in entries]}


# ---------------------------------------------------------------- mappers: legacy rows -> FHIR resources
def m_patient(r: dict) -> dict:
    nm = r["PT_NM"].rsplit(" ", 1)
    return {"resourceType": "Patient", "id": str(r["PT_ID"]),
            "meta": {"profile": ["http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient"]},
            "identifier": [{"use": "usual", "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0203", "code": "MR"}]},
                            "system": SYS["mrn"], "value": r["MRN"]}],
            "active": True, "name": [{"use": "official", "family": nm[-1], "given": nm[0].split()}],
            "telecom": [{"system": "phone", "value": r["PT_PHN"], "use": "home"}] if r["PT_PHN"] else [],
            "gender": "male" if r["PT_SEX"] == "M" else "female", "birthDate": r["PT_DOB"],
            "address": [{"use": "home", "line": [r["PT_ADDR"]], "city": r["PT_CITY"], "state": r["PT_ST"], "postalCode": r["PT_ZIP"], "country": "US"}]}


def m_encounter(r: dict) -> dict:
    enc = {"resourceType": "Encounter", "id": r["IP_NO"], "meta": {"profile": ["http://hl7.org/fhir/us/core/StructureDefinition/us-core-encounter"]},
           "status": "in-progress" if r["STS"] in ("ADM", "DA") else "finished",
           "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": CLASS_CODE.get(r["ENC_CLS"], "AMB")},
           "type": [{"text": r["ENC_TYP"] or "Visit"}], "subject": ref("Patient", r["PT_ID"]),
           "period": {"start": iso(r["ADM_DT"]), **({"end": iso(r["DSCH_DT"])} if r["DSCH_DT"] else {})},
           "reasonCode": [{"text": r["ADM_RSN"]}] if r["ADM_RSN"] else [],
           "serviceProvider": ref("Organization", "cityforward"),
           "extension": [{"url": f"{SYS['meditrack']}/legacy-status", "valueCode": r["STS"]}]}
    if r["DR_ID"]:
        enc["participant"] = [{"type": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ParticipationType", "code": "PPRF"}]}],
                               "individual": ref("Practitioner", r["DR_ID"])}]
    if r["WRD_CD"]:
        enc["extension"].append({"url": f"{SYS['meditrack']}/ward-code", "valueCode": r["WRD_CD"]})
        enc["location"] = [{"location": {"display": f"{UNIT_NAMES.get(r['WRD_CD'], r['WRD_CD'])}" + (f", bed {r['BED_NO']}" if r["BED_NO"] else "")}}]
    if r["DSCH_ADV_DT"]:
        enc["extension"].append({"url": f"{SYS['meditrack']}/discharge-advised-at", "valueDateTime": iso(r["DSCH_ADV_DT"])})
    if r["DSCH_DISP"] in DISPOSITION:
        code, disp = DISPOSITION[r["DSCH_DISP"]]
        enc["hospitalization"] = {"dischargeDisposition": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/discharge-disposition", "code": code, "display": disp}]}}
    return enc


def m_condition(r: dict) -> dict:
    return {"resourceType": "Condition", "id": f"dx-{r['ID']}", "meta": {"profile": ["http://hl7.org/fhir/us/core/StructureDefinition/us-core-condition-encounter-diagnosis"]},
            "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]},
            "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status",
                                               "code": "provisional" if r["DIAG_TYP"] == "PROV" else "confirmed"}]},
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-category", "code": "encounter-diagnosis"}]}],
            "code": {"coding": [{"system": SYS["icd10"], "code": r["ICD_CD"], "display": r["DIAG_DESC"]}], "text": r["DIAG_DESC"]},
            "subject": ref("Patient", r["PT_ID"]), "encounter": ref("Encounter", r["IP_NO"]), "recordedDate": iso(r["ADM_DT"])}


def m_procedure(r: dict) -> dict:
    coding = [{"system": SYS["sct"], "code": r["PROC_CD"], "display": r["PROC_DESC"]}] if r["PROC_CD"] and r["PROC_SYS"] == "SCT" else []
    return {"resourceType": "Procedure", "id": f"px-{r['ID']}", "status": "completed",
            "code": {"coding": coding, "text": r["PROC_DESC"]}, "subject": ref("Patient", r["PT_ID"]), "encounter": ref("Encounter", r["IP_NO"]),
            "performedDateTime": iso(r["PROC_DT"])}


def m_observation(r: dict) -> dict:
    vital = r["LOINC_CD"] in VITAL_LOINC
    o = {"resourceType": "Observation", "id": f"obs-{r['ID']}",
         "status": "registered" if r["STS"] == "PENDING" else "final",
         "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "vital-signs" if vital else "laboratory"}]}],
         "code": {"coding": [{"system": SYS["loinc"], "code": r["LOINC_CD"]}] if r["LOINC_CD"] else [], "text": r["TST_NM"]},
         "subject": ref("Patient", r["PT_ID"]), "encounter": ref("Encounter", r["IP_NO"]), "effectiveDateTime": iso(r["RSLT_DT"])}
    if r["RSLT_VAL"] not in (None, ""):
        try:
            o["valueQuantity"] = {"value": float(r["RSLT_VAL"]), "unit": r["UOM"], "system": SYS["ucum"], "code": r["UOM"]}
        except ValueError:
            o["valueString"] = r["RSLT_VAL"]
    if r["FLG"]:
        o["interpretation"] = [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation", "code": r["FLG"]}]}]
    if r["REF_RNG"]:
        o["referenceRange"] = [{"text": r["REF_RNG"]}]
    return o


MED_CATEGORY = {"INPATIENT": "inpatient", "DISCHARGE": "discharge", "HOME": "community", "OUTPT": "outpatient"}


def m_medrequest(r: dict) -> dict:
    coding = [{"system": SYS["rxnorm"], "code": r["RXN_CD"], "display": r["DRUG_NM"]}] if r["RXN_CD"] else []
    sig = " ".join(x for x in (r["DOSE"], r["ROUTE"], r["FREQ"]) if x)
    return {"resourceType": "MedicationRequest", "id": f"rx-{r['ID']}", "status": "active" if r["MED_STS"] == "ACTIVE" else "completed", "intent": "order",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/medicationrequest-category", "code": MED_CATEGORY[r["ISS_TYP"]]}]}],
            "medicationCodeableConcept": {"coding": coding, "text": r["DRUG_NM"]}, "subject": ref("Patient", r["PT_ID"]), "encounter": ref("Encounter", r["IP_NO"]),
            "authoredOn": iso(r["ADM_DT"]), **({"dosageInstruction": [{"text": sig}]} if sig else {})}


def m_note(r: dict) -> dict:
    nursing = r["NOTE_TYP"] == "NRS"
    return {"resourceType": "DocumentReference", "id": f"note-{r['ID']}", "status": "current",
            "type": {"coding": [{"system": SYS["loinc"], "code": "34746-8" if nursing else "11506-3", "display": "Nurse Note" if nursing else "Progress note"}]},
            "category": [{"coding": [{"system": "http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category", "code": "clinical-note"}]}],
            "subject": ref("Patient", r["PT_ID"]), "date": iso(r["NOTE_DT"]), "author": [{"display": r["AUTH"]}],
            "context": {"encounter": [ref("Encounter", r["IP_NO"])]},
            "content": [{"attachment": {"contentType": "text/plain", "url": f"{BASE}/Binary/note-{r['ID']}"}}]}


def m_doc(r: dict) -> dict:
    return {"resourceType": "DocumentReference", "id": f"doc-{r['DOC_ID']}", "status": "current",
            "type": {"coding": [{"system": f"{SYS['meditrack']}/document-type", "code": r["DOC_TYP"]}], "text": r["DOC_TYP"]},
            "category": [{"coding": [{"system": f"{SYS['meditrack']}/document-category", "code": "scanned-document"}]}],
            "subject": ref("Patient", r["PT_ID"]), "date": iso(r["UPL_DT"]), "author": [{"display": r["UPL_BY"]}],
            "context": {"encounter": [ref("Encounter", r["IP_NO"])]},
            "content": [{"attachment": {"contentType": "application/pdf", "title": r["FILE_NM"], "url": f"{BASE}/Binary/doc-{r['DOC_ID']}"}}]}


def m_dsumm(r: dict) -> dict:
    return {"resourceType": "DocumentReference", "id": f"dsum-{r['IP_NO']}", "status": "current",
            "type": {"coding": [{"system": SYS["loinc"], "code": "18842-5", "display": "Discharge summary"}]},
            "category": [{"coding": [{"system": "http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category", "code": "clinical-note"}]}],
            "subject": ref("Patient", r["PT_ID"]), "date": iso(r["CRT_DT"]), "author": [{"display": r["CRT_BY"]}],
            "context": {"encounter": [ref("Encounter", r["IP_NO"])]},
            "content": [{"attachment": {"contentType": "text/plain", "url": f"{BASE}/Binary/dsum-{r['IP_NO']}"}}]}


def m_coverage(r: dict) -> dict:
    return {"resourceType": "Coverage", "id": f"cov-{r['PT_ID']}", "status": "active", "type": {"text": r["PYR_NM"]},
            "subscriberId": r["MBR_ID"], "beneficiary": ref("Patient", r["PT_ID"]), "payor": [{"display": r["PYR_NM"]}],
            "period": {"start": iso(r["ADM_DT"])}, "class": [{"type": {"coding": [{"code": "plan"}]}, "value": r["PYR_NM"]}]}


def m_practitioner(r: dict) -> dict:
    nm = r["DR_NM"].replace("Dr. ", "").rsplit(" ", 1)
    return {"resourceType": "Practitioner", "id": r["DR_ID"], "identifier": [{"system": SYS["npi"], "value": r["DR_NPI"]}], "active": True,
            "name": [{"family": nm[-1], "given": nm[0].split(), "prefix": ["Dr."]}],
            "qualification": [{"code": {"text": r["DR_SPCL"]}}] if r["DR_SPCL"] else []}


ORG = {"resourceType": "Organization", "id": "cityforward", "active": True, "name": "CityForward Health",
       "address": [{"line": ["1 Hospital Way"], "city": "Columbus", "state": "OH", "postalCode": "43215", "country": "US"}]}

# ---------------------------------------------------------------- queries (one place that knows the legacy joins)
ENC_JOIN = "JOIN IP_ADM_DTL a ON a.IP_NO = {t}.IP_NO"


def q_patients(c, where: str = "", args: tuple = (), limit: int = 100):
    return rows(c, f"SELECT * FROM PT_MST p {where} ORDER BY p.PT_ID LIMIT %s", *args, limit)


def date_clause(param: str | None, col: str) -> tuple[str, list]:
    """FHIR date search (eq/ge/le/gt/lt prefix) against a 'YYYY-MM-DD HH:MM' text column."""
    if not param:
        return "", []
    m = re.fullmatch(r"(eq|ge|le|gt|lt)?(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2}).*)?", param)
    if not m:
        raise FhirError(400, "invalid", f"bad date parameter {param}")
    op = {"eq": "=", "ge": ">=", "le": "<=", "gt": ">", "lt": "<", None: "="}[m[1]]
    val = f"{m[2]} {m[3] or '00:00'}"
    if op == "=" and not m[3]:
        return f"AND {col} >= %s AND {col} < %s", [val, f"{m[2]} 23:59:99"]
    return f"AND {col} {op} %s", [val]


def paging(request: Request) -> tuple[int, int]:
    count = min(int(request.query_params.get("_count", 100)), 500)
    page = max(int(request.query_params.get("_page", 1)), 1)
    return count, (page - 1) * count


def sort_dir(request: Request, default: str = "ASC") -> str:
    return "DESC" if request.query_params.get("_sort", "").startswith("-") else default


def unrestricted(claims: dict, rtype: str) -> bool:
    """Does the token hold a user/ or system/ scope for this type (i.e. not limited to one patient)?"""
    scopes = " ".join(sc for sc in claims.get("scope", "").split() if not sc.startswith("patient/"))
    return smart.scope_allows({**claims, "scope": scopes}, rtype)


def patient_filter(claims: dict, rtype: str, request: Request) -> str | None:
    """The patient a search is limited to (None = unrestricted). A patient/* token can only ever see its own patient."""
    p = pid(request.query_params.get("patient") or request.query_params.get("subject"))
    if unrestricted(claims, rtype):
        return p
    p = p or str(claims.get("patient"))
    need(claims, rtype, "read", p)
    return p


# ---------------------------------------------------------------- endpoints
@router.get("/metadata")
def capability_statement():
    return respond({
        "resourceType": "CapabilityStatement", "status": "active", "date": "2026-10-01", "kind": "instance", "fhirVersion": "4.0.1",
        "format": ["json"], "software": {"name": "MediTrack HMS", "version": "3.4.0"}, "implementation": {"description": "CityForward Health FHIR R4 API", "url": BASE},
        "rest": [{"mode": "server",
                  "security": {"service": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/restful-security-service", "code": "SMART-on-FHIR"}]}],
                               "extension": [{"url": "http://fhir-registry.smarthealthit.org/StructureDefinition/oauth-uris", "extension": [
                                   {"url": "authorize", "valueUri": smart.BASE_URL + "/smart/authorize"}, {"url": "token", "valueUri": smart.TOKEN_URL}]}]},
                  "resource": [{"type": t, "interaction": [{"code": "read"}, {"code": "search-type"}] + ([{"code": "create"}] if t == "DocumentReference" else [])}
                               for t in RESOURCES]}]})


@router.get("/Patient/{rid}")
def read_patient(rid: str, request: Request):
    claims = auth(request)
    need(claims, "Patient", "read", rid)
    with connect() as c:
        r = row(c, "SELECT * FROM PT_MST WHERE PT_ID=%s", int(rid) if rid.isdigit() else -1)
    if not r:
        raise FhirError(404, "not-found", f"Patient/{rid}")
    log_access(claims, "Patient", rid)
    return respond(m_patient(r))


@router.get("/Patient")
def search_patient(request: Request):
    claims = auth(request)
    qp, where, args = request.query_params, [], []
    if qp.get("_id"):
        where.append("p.PT_ID = %s"); args.append(int(pid(qp["_id"])) if pid(qp["_id"]).isdigit() else -1)
    if qp.get("identifier"):
        where.append("p.MRN = %s"); args.append(qp["identifier"].split("|")[-1])
    if qp.get("name"):
        where.append("p.PT_NM ILIKE %s"); args.append(f"%{qp['name']}%")
    if qp.get("birthdate"):
        where.append("p.PT_DOB = %s"); args.append(qp["birthdate"])
    count, offset = paging(request)
    if not unrestricted(claims, "Patient"):
        own = qp.get("_id") and pid(qp["_id"]) or str(claims.get("patient"))
        need(claims, "Patient", "read", own)
        where.append("p.PT_ID = %s"); args.append(int(claims.get("patient", -1)))
    with connect() as c:
        res = rows(c, f"SELECT * FROM PT_MST p {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY p.PT_ID LIMIT %s OFFSET %s", *args, count, offset)
    for r in res:
        log_access(claims, "Patient", str(r["PT_ID"]))
    return respond(bundle([m_patient(r) for r in res], self_url=str(request.url)))


@router.get("/Patient/{rid}/$everything")
def patient_everything(rid: str, request: Request):
    claims = auth(request)
    need(claims, "Patient", "read", rid)
    out = []
    with connect() as c:
        p = row(c, "SELECT * FROM PT_MST WHERE PT_ID=%s", int(rid) if rid.isdigit() else -1)
        if not p:
            raise FhirError(404, "not-found", f"Patient/{rid}")
        out.append(m_patient(p))
        for r in rows(c, "SELECT * FROM IP_ADM_DTL WHERE PT_ID=%s ORDER BY ADM_DT", p["PT_ID"]):
            out.append(m_encounter(r))
        out += [m_condition(r) for r in _children(c, "IP_DIAG_DTL", p["PT_ID"], "ID")]
        out += [m_procedure(r) for r in _children(c, "IP_PROC_DTL", p["PT_ID"], "PROC_DT")]
        out += [m_observation(r) for r in _children(c, "LAB_RSLT", p["PT_ID"], "RSLT_DT")]
        out += [m_medrequest(r) for r in _children(c, "PHR_ISS_DTL", p["PT_ID"], "ID")]
        out += [m_note(r) for r in _children(c, "CLN_NOTE", p["PT_ID"], "NOTE_DT")]
        out += [m_doc(r) for r in _children(c, "PT_DOC", p["PT_ID"], "DOC_ID")]
    log_access(claims, "Patient/$everything", rid)
    return respond(bundle(out))


def _children(c, table: str, patient_id: int, order: str) -> list[dict]:
    cols = "a.PT_ID, a.ADM_DT"
    return rows(c, f"SELECT t.*, {cols} FROM {table} t JOIN IP_ADM_DTL a ON a.IP_NO = t.IP_NO WHERE a.PT_ID=%s ORDER BY t.{order}", patient_id)


@router.get("/Encounter/{rid}")
def read_encounter(rid: str, request: Request):
    claims = auth(request)
    with connect() as c:
        r = row(c, "SELECT * FROM IP_ADM_DTL WHERE IP_NO=%s", rid)
    if not r:
        raise FhirError(404, "not-found", f"Encounter/{rid}")
    need(claims, "Encounter", "read", r["PT_ID"])
    log_access(claims, "Encounter", rid)
    return respond(m_encounter(r))


@router.get("/Encounter")
def search_encounter(request: Request):
    claims, qp = auth(request), request.query_params
    p = patient_filter(claims, "Encounter", request)
    where, args = ["TRUE"], []
    if p:
        where.append("a.PT_ID = %s"); args.append(int(p))
    if qp.get("_id"):
        where.append("a.IP_NO = %s"); args.append(qp["_id"])
    if qp.get("status"):
        where.append("a.STS = ANY(%s)"); args.append(list({"in-progress": ("ADM", "DA"), "finished": ("DSCH", "CLS")}.get(qp["status"], ("?",))))
    if qp.get("class"):
        where.append("a.ENC_CLS = %s"); args.append({v: k for k, v in CLASS_CODE.items()}.get(qp["class"], "?"))
    clause, dargs = date_clause(qp.get("date"), "a.ADM_DT")
    count, offset = paging(request)
    with connect() as c:
        res = rows(c, f"SELECT a.* FROM IP_ADM_DTL a WHERE {' AND '.join(where)} {clause} ORDER BY a.ADM_DT {sort_dir(request)} LIMIT %s OFFSET %s", *args, *dargs, count, offset)
    return respond(bundle([m_encounter(r) for r in res], self_url=str(request.url)))


def _search(request: Request, rtype: str, table: str, mapper, date_col: str, order: str, extra_where=None):
    claims, qp = auth(request), request.query_params
    p = patient_filter(claims, rtype, request)
    where, args = ["TRUE"], []
    if p:
        where.append("a.PT_ID = %s"); args.append(int(p))
    if qp.get("encounter"):
        where.append("t.IP_NO = %s"); args.append(pid(qp["encounter"]))
    if extra_where:
        extra_where(qp, where, args)
    clause, dargs = date_clause(qp.get("date"), f"t.{date_col}") if date_col else ("", [])
    count, offset = paging(request)
    with connect() as c:
        res = rows(c, f"SELECT t.*, a.PT_ID, a.ADM_DT FROM {table} t JOIN IP_ADM_DTL a ON a.IP_NO = t.IP_NO WHERE {' AND '.join(where)} {clause} "
                      f"ORDER BY t.{order} {sort_dir(request)}, t.IP_NO LIMIT %s OFFSET %s", *args, *dargs, count, offset)
    return respond(bundle([mapper(r) for r in res], self_url=str(request.url)))


def _read(request: Request, rtype: str, table: str, key: str, mapper, rid: str):
    claims = auth(request)
    with connect() as c:
        r = row(c, f"SELECT t.*, a.PT_ID, a.ADM_DT FROM {table} t JOIN IP_ADM_DTL a ON a.IP_NO = t.IP_NO WHERE t.{key}=%s", rid)
    if not r:
        raise FhirError(404, "not-found", f"{rtype}/{rid}")
    need(claims, rtype, "read", r["PT_ID"])
    log_access(claims, rtype, str(rid))
    return respond(mapper(r))


def _num(rid: str, prefix: str) -> int:
    if not rid.startswith(prefix) or not rid[len(prefix):].isdigit():
        raise FhirError(404, "not-found", rid)
    return int(rid[len(prefix):])


@router.get("/Condition")
def search_condition(request: Request):
    return _search(request, "Condition", "IP_DIAG_DTL", m_condition, "", "ID")


@router.get("/Condition/{rid}")
def read_condition(rid: str, request: Request):
    return _read(request, "Condition", "IP_DIAG_DTL", "ID", m_condition, _num(rid, "dx-"))


@router.get("/Procedure")
def search_procedure(request: Request):
    return _search(request, "Procedure", "IP_PROC_DTL", m_procedure, "PROC_DT", "PROC_DT")


@router.get("/Procedure/{rid}")
def read_procedure(rid: str, request: Request):
    return _read(request, "Procedure", "IP_PROC_DTL", "ID", m_procedure, _num(rid, "px-"))


def _obs_filters(qp, where, args):
    cat = qp.get("category")
    if cat:
        codes = sorted(VITAL_LOINC)
        where.append(("t.LOINC_CD = ANY(%s)" if cat.split("|")[-1] == "vital-signs" else "(t.LOINC_CD <> ALL(%s) OR t.LOINC_CD IS NULL)")); args.append(codes)
    if qp.get("code"):
        where.append("t.LOINC_CD = %s"); args.append(qp["code"].split("|")[-1])


@router.get("/Observation")
def search_observation(request: Request):
    return _search(request, "Observation", "LAB_RSLT", m_observation, "RSLT_DT", "RSLT_DT", _obs_filters)


@router.get("/Observation/{rid}")
def read_observation(rid: str, request: Request):
    return _read(request, "Observation", "LAB_RSLT", "ID", m_observation, _num(rid, "obs-"))


def _med_filters(qp, where, args):
    if qp.get("status"):
        where.append("t.MED_STS = %s"); args.append("ACTIVE" if qp["status"] == "active" else "DONE")
    if qp.get("category"):
        inv = {v: k for k, v in MED_CATEGORY.items()}
        where.append("t.ISS_TYP = %s"); args.append(inv.get(qp["category"].split("|")[-1], "?"))


@router.get("/MedicationRequest")
def search_medrequest(request: Request):
    return _search(request, "MedicationRequest", "PHR_ISS_DTL", m_medrequest, "", "ID", _med_filters)


@router.get("/MedicationRequest/{rid}")
def read_medrequest(rid: str, request: Request):
    return _read(request, "MedicationRequest", "PHR_ISS_DTL", "ID", m_medrequest, _num(rid, "rx-"))


@router.get("/DocumentReference")
def search_documentreference(request: Request):
    claims, qp = auth(request), request.query_params
    p = patient_filter(claims, "DocumentReference", request)
    out = []
    want_type = (qp.get("type") or "").split("|")[-1]
    with connect() as c:
        enc = qp.get("encounter") and pid(qp["encounter"])
        base_where, args = "TRUE", []
        if p:
            base_where += " AND a.PT_ID = %s"; args.append(int(p))
        if enc:
            base_where += " AND t.IP_NO = %s"; args.append(enc)
        if want_type in ("", "11506-3", "34746-8"):
            for r in rows(c, f"SELECT t.*, a.PT_ID FROM CLN_NOTE t JOIN IP_ADM_DTL a ON a.IP_NO=t.IP_NO WHERE {base_where} ORDER BY t.NOTE_DT", *args):
                if want_type in ("", "34746-8" if r["NOTE_TYP"] == "NRS" else "11506-3"):
                    out.append(m_note(r))
        if want_type in ("", "18842-5"):
            out += [m_dsumm(r) for r in rows(c, f"SELECT t.*, a.PT_ID FROM IP_DSCH_SUMM t JOIN IP_ADM_DTL a ON a.IP_NO=t.IP_NO WHERE {base_where}", *args)]
        if want_type == "" or want_type.isupper():
            for r in rows(c, f"SELECT t.*, a.PT_ID FROM PT_DOC t JOIN IP_ADM_DTL a ON a.IP_NO=t.IP_NO WHERE {base_where} ORDER BY t.DOC_ID", *args):
                if want_type in ("", r["DOC_TYP"]):
                    out.append(m_doc(r))
    return respond(bundle(out[: paging(request)[0]], self_url=str(request.url)))


def _doc_row(c, rid: str):
    if rid.startswith("note-"):
        return "note", row(c, "SELECT t.*, a.PT_ID FROM CLN_NOTE t JOIN IP_ADM_DTL a ON a.IP_NO=t.IP_NO WHERE t.ID=%s", _num(rid, "note-")), m_note
    if rid.startswith("doc-"):
        return "doc", row(c, "SELECT t.*, a.PT_ID FROM PT_DOC t JOIN IP_ADM_DTL a ON a.IP_NO=t.IP_NO WHERE t.DOC_ID=%s", _num(rid, "doc-")), m_doc
    if rid.startswith("dsum-"):
        return "dsum", row(c, "SELECT t.*, a.PT_ID FROM IP_DSCH_SUMM t JOIN IP_ADM_DTL a ON a.IP_NO=t.IP_NO WHERE t.IP_NO=%s", rid[5:]), m_dsumm
    raise FhirError(404, "not-found", rid)


@router.get("/DocumentReference/{rid}")
def read_documentreference(rid: str, request: Request):
    claims = auth(request)
    with connect() as c:
        kind, r, mapper = _doc_row(c, rid)
    if not r:
        raise FhirError(404, "not-found", f"DocumentReference/{rid}")
    need(claims, "DocumentReference", "read", r["PT_ID"])
    log_access(claims, "DocumentReference", rid)
    return respond(mapper(r))


@router.get("/Binary/{rid}")
def read_binary(rid: str, request: Request):
    claims = auth(request)
    with connect() as c:
        kind, r, _ = _doc_row(c, rid)
    if not r:
        raise FhirError(404, "not-found", f"Binary/{rid}")
    need(claims, "DocumentReference", "read", r["PT_ID"])
    log_access(claims, "Binary", rid)
    if kind == "note":
        return Response(r["NOTE_TXT"], media_type="text/plain; charset=utf-8")
    if kind == "dsum":
        return Response(r["SUMM_TXT"], media_type="text/plain; charset=utf-8")
    return FileResponse(DOCS_DIR / r["FILE_NM"], media_type="application/pdf")


@router.post("/DocumentReference")
async def create_documentreference(request: Request):
    """Write-back: another system files a PDF in the patient's chart (it shows in MediTrack's Documents tab)."""
    claims = auth(request)
    body: dict[str, Any] = await request.json()
    if body.get("resourceType") != "DocumentReference":
        raise FhirError(400, "invalid", "resourceType must be DocumentReference")
    patient_ref = pid((body.get("subject") or {}).get("reference"))
    enc_ref = pid(((body.get("context") or {}).get("encounter") or [{}])[0].get("reference"))
    att = ((body.get("content") or [{}])[0]).get("attachment") or {}
    doc_type = ((body.get("type") or {}).get("coding") or [{}])[0].get("code") or (body.get("type") or {}).get("text")
    if not (patient_ref and enc_ref and doc_type and att.get("data")):
        raise FhirError(400, "required", "subject, context.encounter, type and content[0].attachment.data are required")
    if att.get("contentType") != "application/pdf":
        raise FhirError(422, "not-supported", "only application/pdf attachments are accepted")
    doc_type = re.sub(r"[^A-Z_]", "", doc_type.upper().replace("-", "_"))
    with connect() as c:
        adm = row(c, "SELECT IP_NO, PT_ID FROM IP_ADM_DTL WHERE IP_NO=%s", enc_ref)
        if not adm or str(adm["PT_ID"]) != patient_ref:
            raise FhirError(422, "not-found", "encounter does not belong to that patient")
        need(claims, "DocumentReference", "write", adm["PT_ID"])
        try:
            data = base64.b64decode(att["data"], validate=True)
        except ValueError:
            raise FhirError(400, "invalid", "attachment.data is not valid base64")
        if not data.startswith(b"%PDF"):
            raise FhirError(422, "invalid", "attachment is not a PDF")
        fname = f"{enc_ref}_{doc_type}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        (DOCS_DIR / fname).write_bytes(data)
        by = claims.get("client_id") or claims.get("sub", "api")
        doc_id = c.execute("INSERT INTO PT_DOC (IP_NO, DOC_TYP, FILE_NM, UPL_DT, UPL_BY) VALUES (%s,%s,%s,%s,%s) RETURNING DOC_ID",
                           (enc_ref, doc_type, fname, now(), by)).fetchone()["DOC_ID"]
        audit(c, by, f"FHIR_CREATE:DocumentReference:{doc_type}", enc_ref)
        try:
            from .events import emit
            emit(c, "document.added", enc_ref, {"docType": doc_type, "source": "fhir-api"})
        except ImportError:
            pass
        created = row(c, "SELECT t.*, a.PT_ID FROM PT_DOC t JOIN IP_ADM_DTL a ON a.IP_NO=t.IP_NO WHERE t.DOC_ID=%s", doc_id)
    return respond(m_doc(created), 201, {"Location": f"{BASE}/DocumentReference/doc-{doc_id}"})


@router.get("/Coverage")
def search_coverage(request: Request):
    claims = auth(request)
    p = patient_filter(claims, "Coverage", request)
    with connect() as c:
        res = rows(c, f"""SELECT DISTINCT ON (a.PT_ID) a.PT_ID, a.PYR_NM, a.MBR_ID, a.ADM_DT FROM IP_ADM_DTL a
                          WHERE a.PYR_NM IS NOT NULL {'AND a.PT_ID = %s' if p else ''} ORDER BY a.PT_ID, a.ADM_DT DESC""", *([int(p)] if p else []))
    return respond(bundle([m_coverage(r) for r in res], self_url=str(request.url)))


@router.get("/Practitioner/{rid}")
def read_practitioner(rid: str, request: Request):
    claims = auth(request)
    need(claims, "Practitioner", "read")
    with connect() as c:
        r = row(c, "SELECT * FROM DR_MST WHERE DR_ID=%s", rid)
    if not r:
        raise FhirError(404, "not-found", f"Practitioner/{rid}")
    return respond(m_practitioner(r))


@router.get("/Practitioner")
def search_practitioner(request: Request):
    claims, qp = auth(request), request.query_params
    need(claims, "Practitioner", "read")
    with connect() as c:
        if qp.get("identifier"):
            res = rows(c, "SELECT * FROM DR_MST WHERE DR_NPI=%s", qp["identifier"].split("|")[-1])
        else:
            res = rows(c, "SELECT * FROM DR_MST ORDER BY DR_ID LIMIT %s", paging(request)[0])
    return respond(bundle([m_practitioner(r) for r in res], self_url=str(request.url)))


@router.get("/Organization/{rid}")
def read_organization(rid: str, request: Request):
    auth(request)
    if rid != "cityforward":
        raise FhirError(404, "not-found", f"Organization/{rid}")
    return respond(ORG)
