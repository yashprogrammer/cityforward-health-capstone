"""(Re)create MediTrack's database with synthetic CityForward Health data.

Usage (from the customer/ folder):  python -m meditrack.backend.seed

Patients come from FHIR bundles in customer/synthea/prepared (Synthea + three hand-written golden patients), loaded
through the same kind of migration an EHR vendor would run (meditrack/etl/load_bundles.py).
"""
import shutil
from pathlib import Path

from ..etl import mappings as M
from ..etl.load_bundles import Counter, calibrate_history, load_cases, load_history
from . import seed_data as S
from .db import DB_URL, DOCS_DIR, HOTFOLDER, SCHEMA, connect_once

PREPARED = Path(__file__).resolve().parents[2] / "synthea" / "prepared"


def empty_dir(d: Path) -> None:
    """Clear a folder's contents (the folder itself may be a mounted file share, so it is kept)."""
    d.mkdir(parents=True, exist_ok=True)
    for p in d.iterdir():
        shutil.rmtree(p) if p.is_dir() else p.unlink()


def seed(prepared: Path = PREPARED) -> dict:
    import json
    empty_dir(DOCS_DIR)
    empty_dir(HOTFOLDER)
    conn = connect_once()
    conn.execute(SCHEMA.read_text())
    conn.cursor().executemany("INSERT INTO DR_MST VALUES (%s,%s,%s,%s)", M.DOCTORS)
    conn.cursor().executemany("INSERT INTO USR_MST VALUES (%s,%s,%s,%s,%s,1)", M.USERS)
    stats = load_cases(conn, prepared, DOCS_DIR)
    history = json.load(open(prepared / "mis_history.json"))
    seed_no, rows, baseline = calibrate_history(history, stats["base"])
    load_history(conn, rows)
    conn.execute("INSERT INTO SYS_EXT_LNK (LNK_LBL, LNK_URL, DISP_MD, IS_ACTV, CRT_BY, CRT_DT) VALUES (%s,%s,%s,%s,%s,%s)", S.LEGACY_LINK)
    conn.commit()
    conn.close()
    stats.pop("base")
    stats["history_seed"] = seed_no
    stats["baseline"] = {k: round(v, 1) for k, v in baseline.items()}
    print(f"MediTrack DB seeded -> {DB_URL.rsplit('@', 1)[-1]}, documents -> {DOCS_DIR}")
    print("  ", dict(stats))
    return stats


if __name__ == "__main__":
    seed()
