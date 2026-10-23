# The customer's world: CityForward Health

This folder is **not** what learners build. It is the environment an FDE walks into on day one: a hospital with a 15-year-old
EHR, a payer it must deal with, and (later) a platform team. The instructor explains it; learners study it the way they would
on site, then build the AI layer next to it (`poc/` and `ai/` in the later branches).

> Everything is fictional. Patients are synthetic (Synthea). SSNs start with 999, NPIs with 9999.

## Run it

```bash
docker compose up --build        # from the repo root; Postgres, MediTrack, mock payer, gateway
open http://localhost:8001       # MediTrack
open http://localhost:8003/rules # the payer's medical policy
```

Without Docker (Python 3.13 + a local PostgreSQL): `python -m meditrack.backend.bootstrap` (admin URL in `DB_ADMIN_URL`), then
`python -m meditrack.backend.seed`, then `uvicorn meditrack.backend.app:app --port 8001` with `PYTHONPATH=customer`.
Tests: `cd customer && PYTHONPATH=. pytest` (PostgreSQL required; they reseed the database).

## The pieces

| Piece | Where | Teaching point |
|---|---|---|
| **MediTrack HMS v3.4** | `meditrack/` | A 2011 EHR: cryptic tables (`IP_ADM_DTL`, `PT_MST`, `LAB_RSLT`), no foreign keys, dates as text, free-text notes, a 2018 UI reskin on top. `schema.sql` has the vendor's comments; read it first. |
| **The patient data** | `synthea/` | Synthea FHIR bundles (19 patients) + 3 hand-written "golden" patients, loaded through a deliberately lossy migration (`meditrack/etl`). See `synthea/README.md`. |
| **The three doors** | `meditrack/backend/{fhir,smart,events}.py` | How a vendor lets other systems in, from 2023 on: a **FHIR R4 API**, **SMART on FHIR** launch/auth, **signed event webhooks**. The old doors still exist too: the document hot-folder and System Admin > External Links. |
| **The payer** | `mock_payer/` | A fictional insurer's prior-authorization API (Da Vinci PAS shaped): policy catalogue, approve / deny / pend, asynchronous clinical review. |
| **The hospital's platform** | `platform/` | Local stack in `docker-compose.yml`; Kubernetes (kind / AKS) added with the deployment session. |

## The story in numbers (MIS report)

Insured patients wait **6.3 hours** after discharge is advised; self-pay patients **2.5 hours**; **~38%** of payer authorizations
come back with a documentation query. Top reasons: pending results not documented, operative report missing, principal diagnosis
inconsistent with the admitting diagnosis, attending NPI missing, implant record missing. `Reports > MIS: Discharge TAT`.

## Golden patients (the demo spine)

| Account | Patient | Story |
|---|---|---|
| `IP2610-0142` | Robert Hale, 62 M | Anterior STEMI, PCI. Discharge summary, ICD-10-CM coding, risk flags. A pending Lp(a) result and no implant record: the classic payer query. |
| `IP2610-0151` | Margaret Doyle, 79 F | Hip fracture, hemiarthroplasty. Needs a **skilled nursing facility** stay and a **rolling walker**: two prior authorizations, with the evidence scattered over PT, case-management and operative notes. |
| `IP2610-0138` | James Whitaker, 71 M | COPD exacerbation. Needs **home oxygen**: the qualifying SpO2 values are in a progress note and in respiratory-therapy flowsheet rows. |

## What MediTrack does NOT do (so the AI layer has to)

* no SSO: MediTrack signs in a hard-coded resident; the banner's "act as" switch only exists for demos
* the EHR's FHIR API does not enforce unit-level (row-level) access for user tokens: it is the source of truth, not the policy engine
* no vector search, no summarization, no coding assistance
* events are at-least-once with IDs only: consumers must be idempotent and fetch through FHIR
