import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MEDITRACK_DB_URL", "postgresql://meditrack:meditrack@localhost:5432/meditrack")
os.environ.setdefault("MEDITRACK_DOCS_DIR", str(Path(__file__).resolve().parent / ".data" / "documents"))
os.environ.setdefault("MEDITRACK_HOTFOLDER", str(Path(__file__).resolve().parent / ".data" / "hotfolder"))


@pytest.fixture(scope="session")
def seeded():
    """Reseed MediTrack once per test session (about 3 seconds)."""
    import psycopg
    try:
        psycopg.connect(os.environ["MEDITRACK_DB_URL"], connect_timeout=3).close()
    except psycopg.Error as e:   # pragma: no cover
        pytest.skip(f"PostgreSQL not reachable ({e}); run: docker compose up -d db && python -m meditrack.backend.bootstrap")
    from meditrack.backend.seed import seed
    return seed()


@pytest.fixture(scope="session")
def client(seeded):
    from fastapi.testclient import TestClient
    from meditrack.backend.app import app
    with TestClient(app) as c:
        yield c
