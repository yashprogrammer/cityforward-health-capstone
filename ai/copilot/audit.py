"""Who did what to whose record, and proof nobody edited the history afterwards (HIPAA 164.312(b) audit controls; HITRUST access/change recording).

Each row stores the hash of the previous row, so deleting or editing any row breaks every hash after it. The database also refuses UPDATE, DELETE and
TRUNCATE on the table (triggers), so a bug or a curious developer cannot rewrite history; only a superuser disabling the trigger can, and the chain
check then catches it. Rows carry identifiers and outcomes only. Write the chain head somewhere the application cannot write (a WORM bucket) to make
tampering by a database administrator detectable too.
"""
import contextvars
import hashlib
import json
from datetime import datetime, timezone

from . import db

_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("audit_ctx", default={})
GENESIS = "0" * 64


def set_context(request_id: str | None, client_ip: str | None) -> None:
    _ctx.set({"request_id": request_id, "client_ip": client_ip})


def _digest(prev: str, row: dict) -> str:
    body = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((prev + body).encode()).hexdigest()


def _canon(actor, role, action, patient_id, outcome, detail, request_id, client_ip, ts) -> dict:
    return {"actor": actor, "role": role, "action": action, "patient_id": patient_id, "outcome": outcome, "detail": detail,
            "request_id": request_id, "client_ip": client_ip, "ts": ts.astimezone(timezone.utc).isoformat(timespec="microseconds")}


def log(principal, action: str, patient_id: str | None = None, outcome: str = "allowed", detail: dict | None = None) -> None:
    """Append one record. Never raises into the request path: a failure to audit is reported loudly but must not become a clinical outage."""
    actor = getattr(principal, "user", None) or (principal if isinstance(principal, str) else "system")
    role = getattr(principal, "role", None)
    ctx = _ctx.get()
    try:
        with db.session() as c:
            c.execute("SELECT pg_advisory_xact_lock(7771)")          # serialize appends so the chain has one order
            prev = c.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
            prev_hash = prev["hash"] if prev else GENESIS
            ts = datetime.now(timezone.utc)
            row = _canon(actor, role, action, patient_id, outcome, detail or {}, ctx.get("request_id"), ctx.get("client_ip"), ts)
            c.execute("""INSERT INTO audit_log (ts, actor, role, action, patient_id, outcome, detail, request_id, client_ip, prev_hash, hash)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                      (ts, actor, role, action, patient_id, outcome, json.dumps(detail or {}), ctx.get("request_id"), ctx.get("client_ip"), prev_hash, _digest(prev_hash, row)))
    except Exception as e:   # noqa: BLE001
        print(f"[AUDIT FAILURE] {action} {actor}: {e}")


def verify_chain() -> dict:
    """Recompute every hash. Returns {ok, rows, first_bad_id}."""
    with db.session() as c:
        rows = c.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
    prev, n = GENESIS, 0
    for r in rows:
        row = _canon(r["actor"], r["role"], r["action"], r["patient_id"], r["outcome"], r["detail"], r["request_id"], r["client_ip"], r["ts"])
        if r["prev_hash"] != prev or r["hash"] != _digest(prev, row):
            return {"ok": False, "rows": len(rows), "first_bad_id": r["id"]}
        prev, n = r["hash"], n + 1
    return {"ok": True, "rows": n, "head": prev}


def search(patient_id: str | None = None, actor: str | None = None, action: str | None = None, limit: int = 200) -> list[dict]:
    q, args = "SELECT id, ts, actor, role, action, patient_id, outcome, detail, request_id FROM audit_log WHERE TRUE", []
    for col, v in (("patient_id", patient_id), ("actor", actor), ("action", action)):
        if v:
            q += f" AND {col}=%s"
            args.append(v)
    with db.session() as c:
        out = c.execute(q + " ORDER BY id DESC LIMIT %s", (*args, min(limit, 1000))).fetchall()
    return [{**r, "ts": r["ts"].isoformat()} for r in out]
