"""Give current admissions the diagnoses a fresh seed would give them, without reseeding. Shows the plan unless --apply is given.

    python -m scripts.backfill_diagnoses            # dry run
    python -m scripts.backfill_diagnoses --apply

Only admissions with no diagnosis rows are changed, so it is safe to run again. Statuses default to ADM and DA (current admissions).
"""
import argparse
from pathlib import Path

from meditrack.backend.db import connect_once
from meditrack.etl.load_bundles import backfill_current_admission_diagnoses

PREPARED = Path(__file__).resolve().parents[1] / "synthea" / "prepared"

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="write the diagnoses (default is a dry run)")
    parser.add_argument("--statuses", default="ADM,DA", help="admission statuses to repair (default ADM,DA)")
    args = parser.parse_args()
    with connect_once() as conn:
        report = backfill_current_admission_diagnoses(conn, PREPARED, tuple(args.statuses.split(",")), apply=args.apply)
    for r in report:
        print(f"{r['case']:8} {r['mrn']:9} {r['ip_no'] or '-':12} {r['result']}")
        for icd, desc, typ in r["diagnoses"] if r["result"].endswith("added") or r["result"].startswith(("would", "added")) else []:
            print(f"           {typ:5} {icd:8} {desc}")
    print("applied" if args.apply else "dry run: nothing was written (use --apply)")
