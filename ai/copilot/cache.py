"""Result cache for expensive, repeatable model work (the stay summary). A clinician reopening the dashboard five times a shift should cost one model call, not five.

The key covers everything that changes the answer: prompt version, model, and the exact evidence text. Change any of them and the cache misses by construction,
so there is no invalidation logic to get wrong. Only successful model answers are cached (never the no-AI fallbacks). Authorization is checked before the cache is read, and the table has row-level security.
"""
import hashlib
import json

from . import db, telemetry

TTL_HOURS = 24


def make_key(feature: str, prompt_version: str, model: str, patient_id: str, evidence: list[str]) -> str:
    h = hashlib.sha256()
    for part in (feature, prompt_version, model, patient_id, json.dumps(evidence)):
        h.update(part.encode())
        h.update(b"\0")
    return h.hexdigest()


def get(feature: str, key: str, principal) -> dict | None:
    with db.session(principal) as c:
        r = c.execute("UPDATE result_cache SET hits = hits + 1 WHERE key=%s AND created_at > now() - make_interval(hours => %s) RETURNING payload", (key, TTL_HOURS)).fetchone()
    telemetry.CACHE.labels(feature, "hit" if r else "miss").inc()
    return r["payload"] if r else None


def put(feature: str, key: str, patient_id: str, unit: str | None, payload: dict, principal) -> None:
    with db.session(principal) as c:
        c.execute("""INSERT INTO result_cache (key, feature, patient_id, unit, payload) VALUES (%s,%s,%s,%s,%s)
                     ON CONFLICT (key) DO UPDATE SET payload=EXCLUDED.payload, created_at=now()""", (key, feature, patient_id, unit, json.dumps(payload, default=str)))
