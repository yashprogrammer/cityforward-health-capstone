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
| `auth.py`, `access.py` | verify the SMART user token (RS256 via MediTrack's JWKS); patient-launch scope and unit-level access |

Run the tests (needs PostgreSQL with pgvector; they seed MediTrack and the copilot database):

```bash
PYTHONPATH=ai:customer pytest ai/tests
```

Embeddings: the default `hash` embedder is dependency-free and lexical (fine for tests; it will not connect "antiplatelet" to "ticagrelor").
Build the image with `--build-arg FASTEMBED=1` and set `EMBEDDER=fastembed` for real semantic search (bge-small, local CPU, nothing leaves the VPC).
