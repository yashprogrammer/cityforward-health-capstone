"""Legacy workflow endpoints: discharge, pharmacy reconciliation, documents, hot-folder import."""
import time

import pytest

from meditrack.backend.db import HOTFOLDER, connect_once


@pytest.fixture(autouse=True, scope="module")
def isolated_state(seeded):
    """These tests change patient state; start and finish from the pristine seed so other test modules are unaffected."""
    from meditrack.backend.seed import seed
    seed()
    yield
    seed()


def test_discharge_advise_reconciles_medications_and_blocks_repeat(client):
    before = client.get("/int/ip/IP2610-0142").json()
    assert not [m for m in before["meds"] if m["ISS_TYP"] == "DISCHARGE"]
    assert client.post("/int/ip/IP2610-0142/discharge-advise").status_code == 200
    after = client.get("/int/ip/IP2610-0142").json()
    assert after["adm"]["STS"] == "DA" and after["adm"]["DSCH_ADV_DT"]
    names = {m["DRUG_NM"] for m in after["meds"] if m["ISS_TYP"] == "DISCHARGE"}
    assert any("ticagrelor" in n for n in names) and any("aspirin" in n for n in names)
    assert not any("enoxaparin" in n for n in names)   # a completed in-patient order is not continued
    assert client.post("/int/ip/IP2610-0142/discharge-advise").status_code == 409


def test_final_discharge_records_disposition(client):
    assert client.post("/int/ip/IP2610-0142/discharge", json={"disposition": "HOME"}).status_code == 200
    d = client.get("/int/ip/IP2610-0142").json()["adm"]
    assert d["STS"] == "DSCH" and d["DSCH_DISP"] == "HOME"


def test_external_link_modes_and_smart_link_href(client):
    assert client.post("/int/admin/extlinks", json={"label": "AI Insights", "url": "http://ai.local/launch", "mode": "SMART"}).status_code == 200
    d = client.get("/int/ip/IP2610-0151").json()
    smart = next(l for l in d["links"] if l["DISP_MD"] == "SMART")
    assert smart["HREF"].startswith("/smart/ehr-launch?lnk_id=") and "ip_no=IP2610-0151" in smart["HREF"]
    pacs = next(l for l in d["links"] if l["DISP_MD"] == "LINK")
    assert "mrn=CF900703" in pacs["HREF"]


def test_hotfolder_import_files_a_document_and_rejects_bad_names(client):
    HOTFOLDER.mkdir(parents=True, exist_ok=True)
    good = HOTFOLDER / "IP2610-0151_DEVICE_LOG_20261023101500.pdf"
    bad = HOTFOLDER / "not_a_valid_name.pdf"
    good.write_bytes(b"%PDF-1.4 test")
    bad.write_bytes(b"%PDF-1.4 test")
    for _ in range(40):
        time.sleep(0.5)
        with connect_once() as c:
            row = c.execute("SELECT UPL_BY FROM PT_DOC WHERE FILE_NM=%s", (good.name,)).fetchone()
        if row:
            break
    assert row and row["UPL_BY"] == "BATCH_IMPORT"
    assert (HOTFOLDER / "rejected" / bad.name).exists()
