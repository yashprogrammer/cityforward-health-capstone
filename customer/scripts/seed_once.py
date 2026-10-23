"""Seed MediTrack unless it already has data (first start). `--force` reseeds (demo reset)."""
import sys
import time

import psycopg

from meditrack.backend.db import connect_once
from meditrack.backend.seed import seed


def has_data() -> bool:
    try:
        with connect_once() as c:
            return c.execute("SELECT COUNT(*) AS N FROM IP_ADM_DTL").fetchone()["N"] > 0
    except psycopg.Error:
        return False


def wait_for_db(seconds: int = 60) -> None:
    for _ in range(seconds):
        try:
            connect_once().close()
            return
        except psycopg.Error:
            time.sleep(1)
    sys.exit("database not reachable")


if __name__ == "__main__":
    wait_for_db()
    if "--force" in sys.argv or not has_data():
        seed()
    else:
        print("MediTrack already has data - keeping it (use --force to reseed)")
