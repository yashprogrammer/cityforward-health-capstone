# The vendor's AI service ("CityForward Copilot")

This is what learners build, session by session, next to the hospital's EHR. It never touches MediTrack's database: it reads through the
**FHIR API** with **SMART Backend Services** credentials, keeps its own Postgres (`copilot`, with pgvector), and sits behind the hospital's gateway at `/ai`.

```
MediTrack (EHR) --FHIR R4 / SMART--> copilot ──> Postgres+pgvector (chunks, audit, drafts)
      │  signed webhooks (IDs only) ─────┘   └─> Gateway ──> LLM (Groq gpt-oss-20b | Foundry | in-VPC) 
      └── SMART launch: the clinician opens the dashboard inside the chart
```

| Module | Job |
|---|---|
| `fhir_client.py` | `private_key_jwt` -> 5-minute `system/*.read` token -> FHIR reads and write-back |
| `chart.py` | FHIR resources -> typed chart -> citable **facts** (every fact keeps the FHIR reference it came from) |
| `rag.py`, `embeddings.py` | chunk index in Postgres; hybrid retrieval (pgvector cosine + full-text, fused with RRF), hard-scoped to one patient |
| `gateway.py`, `llm.py` | the only door to a model: JSON validation, token/latency/cost metering; redaction and audit hook in here |
| `ask.py`, `summary.py` | LangGraph graphs: retrieve/draft -> **ground** (code checks citations and numbers) -> one revision -> deterministic fallback |
| `priorauth.py`, `priorauth_api.py`, `payer_client.py` | **Build Session 3.** Reads the payer's policy, assembles evidence per requirement (code where it can, a model where it must, code verifies the model), a clinician reviews/attests/approves, then a Da Vinci PAS-style `Claim/$submit`; the packet is filed back into the chart |
| `auth.py`, `access.py` | verify the SMART user token (RS256 via MediTrack's JWKS); patient-launch scope and unit-level access |

Run the tests (needs PostgreSQL with pgvector; they seed MediTrack and the copilot database):

```bash
PYTHONPATH=ai:customer pytest ai/tests
```

Embeddings: the default `hash` embedder is dependency-free and lexical (fine for tests; it will not connect "antiplatelet" to "ticagrelor").
Build the image with `--build-arg FASTEMBED=1` and set `EMBEDDER=fastembed` for real semantic search (bge-small, local CPU, nothing leaves the VPC).

## Prior authorization: who does what

| Step | Who | Notes |
|---|---|---|
| Suggest services | assistant | pattern-match chart text (SNF, walker, home oxygen, home health), then ask the payer whether *this plan* requires PA |
| Draft | PA coordinator / clinician | diagnoses and "midnights" are computed from the chart; measurements and documents come from the model, **verified in code** (citation exists, every number is in the cited passage, value meets the policy, measurement is fresh) |
| Attest | treating clinician only | the assistant never attests |
| Approve | attending only | needs no open requirement, or a written reason to submit with gaps |
| Submit / poll | PA coordinator or clinician | pended requests (`A4`) reopen for edits and are resubmitted under the same identifier |

## Coding with the coder in the loop

`coding.py` proposes ICD-10-CM codes; `terminology.py` (public-domain code set, no network) decides what may be shown: the code must exist and be billable,
the model's quote must appear verbatim in the cited chart text, descriptions come from the official list, and Excludes1 pairs / unspecified / provisional
diagnoses become documentation queries for the clinician. A coder or attending accepts, rejects (with a reason) or modifies every line and picks the
principal diagnosis; finalizing files a worksheet PDF into the chart. SNOMED CT needs a UMLS licence, so only a demonstration subset of mappings is bundled.

## PHI redaction and audit (Build Session 4)

* `redact.py` de-identifies every prompt at the gateway: exact-match removal of the patient's own identifiers (we hold them from the FHIR Patient resource), patterns for what we cannot know
  (SSN, phone, email, URL, IP), dates turned into reversible offsets from admission (`[D+3]`), ages over 89 generalized, clinician names tokenized. The reply is restored locally, so the clinician sees real values.
  `tests/test_redact.py` runs the real summary pipeline for **every patient in the hospital** with a spy model and scans every byte it received for that patient's identifiers.
* `audit.py`: append-only, hash-chained `audit_log` (database triggers refuse UPDATE/DELETE/TRUNCATE; `GET /api/audit/verify` recomputes the chain). Every allowed and denied access, every AI call
  (purpose, model, tokens, number of redactions) and every human decision (attest, approve, submit, code) is recorded with IDs only, never clinical text or prompts.
