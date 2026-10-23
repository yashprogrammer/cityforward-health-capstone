"""The data the whole engagement stands on: baseline numbers, golden patients, code validity."""
import pytest
import simple_icd_10_cm as icd

from meditrack.backend.db import connect_once


@pytest.fixture(scope="module")
def db(seeded):
    with connect_once() as c:
        yield c


def test_cases_loaded_without_unmapped_conditions(seeded):
    assert seeded["cases"] == 22
    assert seeded["conditions_unmapped"] == 0


def test_census_has_three_golden_patients_and_synthea_inpatients(client):
    census = client.get("/int/census").json()
    assert len(census) == 11
    assert {"IP2610-0142", "IP2610-0151", "IP2610-0138"} <= {r["IP_NO"] for r in census}
    assert {r["STS"] for r in census} == {"ADM", "DA"}


def test_discharge_baseline_matches_the_story(client):
    mis = client.get("/int/mis/discharge-tat").json()
    tat = {m["PAY_MODE"]: float(m["AVG_TAT_HRS"]) for m in mis["by_mode"]}
    assert tat == {"INSURED": 6.3, "SELF_PAY": 2.5}
    assert 37.5 <= float(mis["auths"]["QUERY_PCT"]) <= 38.5
    assert mis["reasons"][0]["QRY_RSN"]


def test_all_stored_icd10cm_codes_are_valid(db):
    codes = {r["ICD_CD"] for r in db.execute("SELECT DISTINCT ICD_CD FROM IP_DIAG_DTL").fetchall()}
    assert codes
    bad = sorted(c for c in codes if not icd.is_valid_item(c))
    assert not bad, f"not in ICD-10-CM: {bad}"


def test_golden_patient_hale(client):
    d = client.get("/int/ip/IP2610-0142").json()
    assert d["adm"]["PT_NM"] == "Robert Hale" and d["adm"]["STS"] == "ADM"
    assert any(x["ICD_CD"] == "I21.02" for x in d["diags"])
    assert {p["PROC_CD"] for p in d["procs"]} >= {"33367005", "415070008"}   # angiography, PCI
    assert any(l["TST_NM"].startswith("Lipoprotein") and l["STS"] == "PENDING" for l in d["labs"])   # a pending result
    assert "CATH_RPT" in {x["DOC_TYP"] for x in d["docs"]} and "DEVICE_LOG" not in {x["DOC_TYP"] for x in d["docs"]}   # the payer documentation gap
    assert len([n for n in d["notes"] if n["NOTE_TYP"] == "NRS"]) == 3
    assert d["visits"], "history visits are part of the patient file"


def test_golden_hip_fracture_has_the_evidence_a_payer_asks_for(client):
    d = client.get("/int/ip/IP2610-0151").json()
    text = " ".join(n["NOTE_TXT"] for n in d["notes"]).lower()
    assert "skilled nursing facility" in text and "rolling walker" in text and "lives alone" in text


def test_golden_copd_documents_the_qualifying_oxygen_saturation(client):
    d = client.get("/int/ip/IP2610-0138").json()
    sat = {l["TST_NM"]: l["RSLT_VAL"] for l in d["labs"] if "room air" in l["TST_NM"]}
    assert any(v == "87" for v in sat.values()) and any(v == "84" for v in sat.values())


def test_synthea_case_has_history_and_a_stay(client):
    d = client.get("/int/ip/IP2610-0204").json()   # heart failure
    assert d["adm"]["ENC_CLS"] == "IMP" and d["notes"] and d["labs"]
    assert len(d["visits"]) >= 5


def test_ids_are_stable_between_seeds(seeded):
    # beds and numbering must not depend on Python's per-process hash seed
    with connect_once() as c:
        assert c.execute("SELECT BED_NO FROM IP_ADM_DTL WHERE IP_NO='IP2610-0204'").fetchone()["BED_NO"] == "09"
