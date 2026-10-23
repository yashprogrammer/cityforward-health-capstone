# Build sessions: objectives, checkpoints, pitfalls

Each session is one git tag on `integrated` (Session 1 is the `poc` branch). The code to read is named; the **exercise** is what learners change themselves; the **checkpoint** is how both of you know it works.
Prerequisites: PostgreSQL with pgvector (`docker compose up -d db`), `pip install -r customer/requirements.txt -r ai/requirements.txt`, `PYTHONPATH=ai:customer pytest ai/tests` green.

| Tag | Session | Read | Exercise | Checkpoint |
|---|---|---|---|---|
| `bs1-poc` (branch `poc`) | **Build Session 1: patient-history summarizer** | `poc/summarizer/records.py`, `graph.py`, `app.py` | add one rule to the grounding check (for example: reject a finding that quotes a unit the source does not contain); run it on three patients | `PYTHONPATH=poc pytest poc/tests`; the app shows citations; with the model off it shows a labelled template |
| `bs2-rag` | **Build Session 2: RAG over patient records** | `ai/copilot/chart.py`, `rag.py`, `ask.py` | add a synonym to `rag.SYNONYMS` that fixes a failing question; write the golden case first | `tests/test_rag.py`; "Is the Lp(a) result back?" cites the pending result; an unanswerable question is declined |
| `bs3-prior-auth` | **Build Session 3: prior-authorization drafting assistant** | `priorauth.py`, `priorauth_api.py`, `customer/mock_payer` | add a policy requirement to the mock payer and make the assistant handle it, deterministically if you can, with the model only if you must | home oxygen end to end with the human steps; a fabricated number never reaches the draft; the pend loop closes |
| `coding` | ICD-10-CM coding with a clinician in the loop | `coding.py`, `terminology.py` | handle one more Excludes1 pair in the worksheet flags | worksheet flags R09.02 + J96.01; cannot finalize a conflicting set |
| `fda-cds` | FDA SaMD or CDS-exempt? | `cds.py`, `docs/compliance/fda-cds-memo.md` | write three outputs that cross the line and make `lint` catch them | `tests/test_cds.py`; the envelope on every output |
| `bs4-phi-audit` | **Build Session 4: PHI redaction and audit logging** | `redact.py`, `audit.py` | find an identifier type the redactor misses (try a relative's name in a note) and fix or document it | the leakage test over all patients; audit chain verifies; tamper is detected |
| `rbac-rls` | Role-based access and row-level controls | `access.py`, `schema_rls.sql`, `breakglass.py` | add a role and its row-level rule; prove it fails closed | `tests/test_rbac.py`; a missing principal sees nothing |
| `bs5-webhooks` | **Build Session 5: EHR event ingestion** | `events.py`, `customer/meditrack/backend/events.py` | add a new event type end to end | signature, replay window, idempotency, retries, dead letter, replay |
| `golden-eval` | Clinician-graded golden eval set | `ai/evalkit` | add five cases from your own reading of a chart, including two unanswerable | `python -m evalkit run --provider offline`; the gates run; a bad model fails them |
| `bs6-dashboard` | **Build Session 6: clinical dashboard** | `dashboard.py`, `ai/dashboard/src` | add a flag rule with its rule text and evidence | launched from MediTrack via SMART; every flag opens its Why panel |
| `deploy` | VPC / on-prem-style deployment | `deploy/` | change a Helm value and watch the policy test fail or pass | `pytest deploy/tests`; chart renders for kind and AKS |
| `security-review` | Passing a hospital-style review | `docs/compliance/` | answer three new questionnaire questions with evidence links | `ai/tests/test_compliance_docs.py` passes (every evidence path exists) |
| `cost-latency` | Cost and latency budgeting | `docs/cost-latency.md`, `evalkit/budget.py`, `cache.py` | change an assumption and re-price; add a cache key part | `python -m evalkit.budget`; the budget circuit breaker test |
| `multi-agent` | Multi-agent design | `discharge.py`, `docs/multi-agent-design.md` | add a fourth agent (for example a "pending results" agent) with its own permission | agents run in parallel, one failing does not stop the rest |
| `incident` | What almost went wrong | `incident/`, `canary.py` | introduce your own regression and see which control fires first | `python -m incident.drill` |

## Teaching notes that apply to every session
* **Write the failing test first.** The tests are the specification a hospital will read.
* **Always ship the fallback.** If the model is off, what does the user see? Build that first.
* **Measure, then optimize.** The evaluation caught a retrieval bug no one would have found by reading code.
* **Name what you did not do.** Every session ends with the list of shortcuts that remain.
