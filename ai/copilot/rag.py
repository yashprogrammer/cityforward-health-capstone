"""Retrieval over a patient's chart: chunks in Postgres, searched by meaning (pgvector) AND by words (full-text), fused with RRF.

Why hybrid: embeddings find "heart attack" when the note says "STEMI"; full-text finds "Lp(a)" or a drug name that embeddings blur.
Reciprocal Rank Fusion needs no score calibration: score = sum over rankers of 1 / (60 + rank).
"""
import hashlib
from dataclasses import dataclass

from .chart import Chart
from .db import session, system, vec
from .embeddings import get_embedder

RRF_K = 60


@dataclass
class Hit:
    id: int
    source_ref: str
    kind: str
    text: str
    effective: str | None
    encounter_id: str | None
    score: float


def index_chart(chart: Chart, principal=None) -> dict:
    """(Re)index one patient. Idempotent: unchanged text keeps its row, changed or removed text is replaced.
    With no principal this is the platform indexing (webhook worker, CLI); row-level security still applies to a user principal."""
    facts = chart.facts()
    emb = get_embedder()
    keep: set[tuple[str, str]] = set()
    rows = []
    for f in facts:
        h = hashlib.sha256(f.text.encode()).hexdigest()[:32]
        if (f.ref, h) in keep:
            continue
        keep.add((f.ref, h))
        rows.append((f, h))
    with (session(principal) if principal is not None else system()) as c:
        have = {(r["source_ref"], r["content_hash"]) for r in c.execute("SELECT source_ref, content_hash FROM chunk WHERE patient_id=%s", (chart.patient_id,))}
        new = [(f, h) for f, h in rows if (f.ref, h) not in have]
        vectors = emb.embed([f.text for f, _ in new]) if new else []
        for (f, h), v in zip(new, vectors):
            c.execute("""INSERT INTO chunk (patient_id, encounter_id, unit, source_ref, kind, effective, text, content_hash, embedding)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::vector) ON CONFLICT DO NOTHING""",
                      (chart.patient_id, f.encounter, chart.unit, f.ref, f.kind, f.when, f.text, h, vec(v)))
        stale = have - keep
        for ref, h in stale:
            c.execute("DELETE FROM chunk WHERE patient_id=%s AND source_ref=%s AND content_hash=%s", (chart.patient_id, ref, h))
        c.execute("UPDATE chunk SET unit=%s WHERE patient_id=%s AND unit IS DISTINCT FROM %s", (chart.unit, chart.patient_id, chart.unit))
        total = c.execute("SELECT count(*) n FROM chunk WHERE patient_id=%s", (chart.patient_id,)).fetchone()["n"]
        c.execute("""INSERT INTO patient_index (patient_id, unit, chunks, identity, indexed_at) VALUES (%s,%s,%s,%s, now())
                     ON CONFLICT (patient_id) DO UPDATE SET unit=EXCLUDED.unit, chunks=EXCLUDED.chunks, identity=EXCLUDED.identity, indexed_at=now()""",
                  (chart.patient_id, chart.unit, total, chart.identity.model_dump_json()))
    return {"patient_id": chart.patient_id, "chunks": total, "added": len(new), "removed": len(stale)}


# Clinicians write "SpO2", "LVEF", "ABG", "DAPT"; people ask for "oxygen saturation", "ejection fraction", "blood gas", "antiplatelet therapy".
# Expanding the query with the other spelling is cheap, explainable, and beats hoping an embedding model knows every abbreviation.
SYNONYMS = [
    ("oxygen saturation", "SpO2 sat"), ("o2 sat", "SpO2 saturation"), ("room air", "RA"), ("ejection fraction", "LVEF EF echo"), ("blood gas", "ABG pH pCO2 pO2"),
    ("blood pressure", "BP systolic diastolic"), ("heart rate", "HR pulse"), ("heart attack", "STEMI NSTEMI myocardial infarction MI"), ("myocardial infarction", "STEMI NSTEMI MI"),
    ("antiplatelet", "aspirin ticagrelor clopidogrel prasugrel DAPT dual antiplatelet"), ("blood thinner", "anticoagulant heparin warfarin apixaban enoxaparin"),
    ("anticoagulant", "heparin warfarin apixaban rivaroxaban enoxaparin"), ("shortness of breath", "dyspnea SOB"), ("dyspnea", "shortness of breath SOB"),
    ("high blood pressure", "hypertension HTN"), ("diabetes", "DM A1c glucose"), ("kidney", "renal creatinine eGFR"), ("antibiotic", "azithromycin ceftriaxone vancomycin"),
    ("allerg", "allergy allergies reaction"), ("walk", "ambulate ambulation gait"), ("stairs", "stair steps floor"), ("lung function", "FEV1 spirometry"),
    ("cholesterol", "LDL lipid statin"), ("smok", "tobacco nicotine pack-years"), ("vaccin", "immunization pneumococcal influenza"), ("transfus", "packed red cells blood unit"),
]


def expand_query(q: str) -> str:
    low = q.lower()
    extra = [terms for key, terms in SYNONYMS if key in low]
    return q + (" " + " ".join(extra) if extra else "")


def search(patient_id: str, query: str, k: int = 8, principal=None, kinds: list[str] | None = None) -> list[Hit]:
    """Top-k chunks of ONE patient's chart for a question. Patient scoping is a hard filter, never a prompt instruction."""
    query = expand_query(query)
    qv = get_embedder().embed([query])[0]
    kind_sql, kind_args = ("AND kind = ANY(%s)", [kinds]) if kinds else ("", [])
    pool_n = max(k * 4, 20)
    with session(principal) as c:
        by_vec = c.execute(f"SELECT id FROM chunk WHERE patient_id=%s {kind_sql} ORDER BY embedding <=> %s::vector LIMIT %s", (patient_id, *kind_args, vec(qv), pool_n)).fetchall()
        by_txt = c.execute(f"""SELECT id FROM chunk WHERE patient_id=%s {kind_sql} AND tsv @@ websearch_to_tsquery('english', %s)
                               ORDER BY ts_rank_cd(tsv, websearch_to_tsquery('english', %s)) DESC LIMIT %s""",
                           (patient_id, *kind_args, _or_query(query), _or_query(query), pool_n)).fetchall()
        scores: dict[int, float] = {}
        for ranking in (by_vec, by_txt):
            for rank, r in enumerate(ranking):
                scores[r["id"]] = scores.get(r["id"], 0.0) + 1.0 / (RRF_K + rank + 1)
        top = sorted(scores, key=scores.get, reverse=True)[:k]
        if not top:
            return []
        got = {r["id"]: r for r in c.execute("SELECT id, source_ref, kind, text, effective, encounter_id FROM chunk WHERE id = ANY(%s)", (top,))}
    return [Hit(id=i, source_ref=got[i]["source_ref"], kind=got[i]["kind"], text=got[i]["text"],
                effective=got[i]["effective"].isoformat() if got[i]["effective"] else None, encounter_id=got[i]["encounter_id"], score=scores[i]) for i in top]


def _or_query(q: str) -> str:
    """websearch_to_tsquery ANDs words; a natural-language question should match if *most* words do, so OR the content words."""
    words = [w for w in __import__("re").findall(r"[A-Za-z0-9]{3,}", q.lower()) if w not in _QUESTION_WORDS]
    return " or ".join(words) if words else q


_QUESTION_WORDS = frozenset("what when where which who why how does did was were the and for with this that has have had been are any there their from about patient".split())


def identity_of(patient_id: str):
    """Identity record stored at indexing time (used by the redactor when a call has no chart in hand)."""
    from .chart import Identity
    with system() as c:
        r = c.execute("SELECT identity FROM patient_index WHERE patient_id=%s", (patient_id,)).fetchone()
    return Identity(**r["identity"]) if r and r["identity"] else None
