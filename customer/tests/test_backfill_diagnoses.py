"""Repairing a database seeded before admissions carried their diagnoses. Run against a disposable, seeded database (demo mode is fine)."""
from collections import defaultdict

from meditrack.backend.db import connect_once
from meditrack.backend.seed import PREPARED
from meditrack.etl.load_bundles import backfill_current_admission_diagnoses as backfill


def snapshot(c, ip_nos):
    out = defaultdict(list)
    for r in c.execute("SELECT IP_NO, ICD_CD, DIAG_DESC, DIAG_TYP FROM IP_DIAG_DTL WHERE IP_NO = ANY(%s) ORDER BY ID", (ip_nos,)).fetchall():
        out[r["IP_NO"]].append((r["ICD_CD"], r["DIAG_DESC"], r["DIAG_TYP"]))
    return dict(out)


def test_backfill_reproduces_a_fresh_seed_and_is_safe_to_repeat(seeded):
    c = connect_once()
    try:
        current = [r["IP_NO"] for r in c.execute("SELECT IP_NO FROM IP_ADM_DTL WHERE ENC_CLS='IMP' AND STS IN ('ADM','DA')").fetchall()]
        original = snapshot(c, current)
        assert len(original) >= 9

        # a database that is already right is left alone
        report = backfill(c, PREPARED, apply=True)
        assert report and all(r["ip_no"] for r in report)
        assert all(r["result"].startswith(("already has", "nothing to add")) for r in report)
        assert snapshot(c, current) == original

        # a database seeded before the fix: the current admissions have no diagnoses at all
        c.execute("DELETE FROM IP_DIAG_DTL WHERE IP_NO = ANY(%s)", (current,))
        assert snapshot(c, current) == {}
        dry = backfill(c, PREPARED)
        assert any(r["result"].startswith("would add") for r in dry) and snapshot(c, current) == {}          # a dry run writes nothing
        applied = backfill(c, PREPARED, apply=True)
        assert all(r["ip_no"] for r in applied)
        assert snapshot(c, current) == original                                                                 # same codes, descriptions, types and order as the seed
        provisional = {ip for ip, rows in original.items() if any(t == "PROV" for _, _, t in rows)}
        assert provisional and all(r["STS"] == "ADM" for r in c.execute("SELECT STS FROM IP_ADM_DTL WHERE IP_NO = ANY(%s)", (list(provisional),)).fetchall())

        # running it again changes nothing, and a diagnosis someone added by hand is never touched
        again = backfill(c, PREPARED, apply=True)
        assert all(r["result"].startswith(("already has", "nothing to add")) for r in again)
        assert snapshot(c, current) == original
    finally:
        c.rollback()      # leave the shared test database exactly as it was
        c.close()
