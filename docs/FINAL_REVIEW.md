# Final review: what was built overnight, what was proven, what was not

*Written for the owner to read first. Everything below is true of the repository state at the commit this file was merged with. Date: 2026-10-23.*

## 1. In one paragraph

The whole capstone is built: a US hospital's legacy EHR with FHIR/SMART/webhooks and a mock payer (`legacy`), the patient-history summarizer POC (`poc`), and the production AI layer with every build session from the lead's sheet, the compliance controls, the dashboard inside the EHR, the deployment, the security pack, the evaluation harness, the incident drill (`integrated`), plus the non-code course materials and the assignment checker (`main`). **301 automated tests pass** (39 EHR, 13 POC, 220 AI service, 29 deployment/policy). The full Docker Compose stack was brought up and driven end to end in a real browser (SMART launch with PKCE, dashboard, every tab) and by a real signed webhook. **Nothing has been run against a real language model**, and kind, AKS, Argo CD, Kyverno, Falco, Linkerd, Keycloak and Terraform were written and statically verified but could not be executed in the sandbox. Section 4 is the honest list.

## 2. How to run and review it (about two hours)

```bash
git clone https://github.com/yashprogrammer/cityforward-health-capstone && cd cityforward-health-capstone && git checkout integrated
PG_PORT=55432 docker compose up --build            # EHR, payer, AI service, gateway; http://localhost:8001 (PG_PORT only if 5432 is taken)
export GROQ_API_KEY=...                             # optional: switches the AI on (otherwise it runs in its labelled no-AI mode)
```
1. **(10 min)** Open http://localhost:8001, open a patient, choose **AI Clinical Insights** (EHR launch). Try the Overview flags and their Why panel, the summary, Ask, Prior authorization (act as `pa.hughes` and as `attending.okafor` through the "act as" switch in MediTrack), Coding.
2. **(15 min)** `PYTHONPATH=ai:customer pytest customer/tests ai/tests` (needs PostgreSQL with pgvector, see `ai/README.md`), then `pytest deploy/tests`.
3. **(15 min)** `PYTHONPATH=ai:customer:. python -m incident.drill` and read the report. Then `python -m evalkit run --provider offline` and read `ai/evalkit/README.md`.
4. **(20 min)** Read `docs/compliance/hipaa-hitrust-mapping.md` section 5 (the gaps) and `deploy/README.md` (what ran and what did not).
5. **(30 min)** Skim `course/README.md` and the readout, wedge and assignment documents.
6. **Decide** the items in section 6.

Real-model run (the most valuable next hour): `GROQ_API_KEY=... python -m evalkit run --provider groq --out results/gpt-oss-20b` and `python -m evalkit gate results/gpt-oss-20b`. That gives you the first real quality numbers and will probably surface prompt/parse issues the fake models cannot.

## 3. What exists

| Branch | Contents | Tests |
|---|---|---|
| `main` | `course/` (22 documents + the assignment checker), `docs/decisions.md`, this file, `scripts/make_tags.sh` | none (documents) |
| `legacy` | MediTrack US EHR (React "Clarity" UI, Postgres, legacy-schema ETL from Synthea FHIR bundles), FHIR R4 API, SMART on FHIR authorization server (EHR launch + PKCE, backend services with `private_key_jwt`), signed CloudEvents webhooks with outbox/retry/dead-letter/replay, mock payer (Da Vinci PAS-shaped, pend/deny/approve/manual review), Docker image, compose stack, instructor README | 39 |
| `poc` | LangGraph patient-history summarizer with grounding checks, Streamlit app, offline template fallback, list of POC shortcuts | 13 |
| `integrated` | `ai/` service: FHIR client, chart facts, hybrid pgvector retrieval, grounded Q&A, summaries, prior-auth assistant, ICD-10-CM coding assistant, FDA CDS controls, de-identification gateway, hash-chained audit, RBAC + row-level security + break-the-glass, Keycloak/SSO claims, event ingestion, dashboard (React SMART app), cost/latency controls, multi-agent supervisor, redaction canary; `ai/evalkit` golden-set harness; `deploy/` (Helm, Argo CD, platform policies, Terraform, kind); `docs/compliance/` (FDA memo, HIPAA/HITRUST mapping, threat model, IR plan, vendor register, questionnaire); `incident/` drill; CI and release workflows | 220 + 29 |

Session tags (the sheet's lectures): `bs1-poc` (on `poc`), then on `integrated`: `bs2-rag`, `bs3-prior-auth`, `coding`, `fda-cds`, `bs4-phi-audit`, `rbac-rls`, `bs5-webhooks`, `golden-eval`, `bs6-dashboard`, `deploy`, `security-review`, `cost-latency`, `multi-agent`, `incident`, `handoff`.
**The sandbox's git proxy rejects tag pushes**, so each tag is pushed as a branch `session/<tag>`. Run `scripts/make_tags.sh` once from your machine to turn them into real tags (the script is written and syntax-checked; `--dry-run` shows what it will do).

## 4. Verified and not verified

### Verified by running it
| Claim | How |
|---|---|
| EHR FHIR/SMART/webhook layer, payer, ETL | 39 tests in-process against PostgreSQL |
| Whole AI service behaviour (retrieval, grounding, fallbacks, prior-auth human steps, coding rules, CDS lint, redaction, audit chain, RBAC/RLS, webhooks with retry/dead-letter, dashboard data, cost controls, multi-agent supervisor, incident drill, assignment checker) | 220 tests, EHR and AI service wired together in-process, scripted fake models |
| De-identification: no patient identifier in any prompt, for every one of the 22 patients | `ai/tests/test_redact.py` |
| Audit tamper detection; database refuses to rewrite history | `ai/tests/test_audit.py` |
| Row-level security fails closed; roles without PHI access see nothing even with all units | `ai/tests/test_rbac.py` |
| Helm chart: lint, rendered for kind and AKS, **every manifest valid for Kubernetes 1.31**, security posture assertions | `deploy/tests/test_chart.py` (helm via the pinned `alpine/helm` image, `kubernetes-validate`) |
| **The real OpenTelemetry collector image** strips PHI with the repo's processor chain | `deploy/tests/test_otel_collector.py` (Docker) |
| **Docker Compose stack** (db, keygen, seed, EHR, payer, AI service, gateway) up from scratch; a real browser completed the SMART launch and exercised every dashboard tab; a real MediTrack webhook produced prior-auth drafts and a coding worksheet through the worker | run in the sandbox (this caught a real bug: a missing dependency in the image requirements, now fixed and guarded by a test) |
| Assignment checker passes against the live compose stack | `course/check_submission.py` |
| Alerts only reference metrics the service exports; Falco egress ports match the network policy; Kyverno trusts the workflow that exists | `deploy/tests/test_platform.py`, `ai/tests/test_compliance_docs.py` |

### Written and statically checked, **not executed**
| Item | Why not | First thing to do |
|---|---|---|
| Any run against a real LLM (Groq, Foundry) | no key/no BAA; fakes only | run the evaluation with a real provider; expect prompt/JSON-parse tuning |
| kind cluster (`deploy/kind/bootstrap.sh`) | nested containers cannot start in the sandbox (kubelet fails to create sandboxes) | run it on a laptop |
| Argo CD apps, Linkerd, Kyverno policies, Falco rules, Keycloak realm import, ingress-nginx annotations | need a cluster; chart versions are **ranges, unverified against the live registries** | first sync, then pin versions |
| Terraform (`terraform fmt` and HCL parse only) | the provider registry is unreachable from the sandbox, so no `init`/`validate`/`plan` | `terraform init && terraform validate`, expect a few argument renames (azurerm 4.x) |
| GitHub Actions workflows (`ci.yml`, `release.yml`) | not run on GitHub; the release workflow assumes a self-hosted runner inside the VNet | push and watch the first run |
| `FASTEMBED=1` image (real embeddings) | Hugging Face is blocked from the sandbox | build with the argument on a normal network; the evaluation will show the retrieval difference |
| Mobile layout of the dashboard | CSS written for it, only desktop Chromium was exercised | check on a phone/tablet |
| Accessibility | semantic markup, focus styles, contrast considered; no audit tool run | run axe/Lighthouse |
| Clinician grading | no clinicians available: the review pack and scoring exist and are tested with synthetic grade files | have two real clinicians grade a pack |

## 5. Decisions I made on your behalf (reverse any of them)

| # | Decision | Why | To reverse |
|---|---|---|---|
| 1 | **MediTrack is the SMART authorization server**; Keycloak is an *optional hospital IdP* for staff SSO (claim mapping tested, realm unrun). `docs/decisions.md` row 5 said Keycloak would be both | in reality the EHR vendor is the SMART server; the AI app should not care which IdP the hospital uses | swap `trusted_issuers()` in `ai/copilot/auth.py` |
| 2 | Default embedder is a dependency-free **hash embedder + Postgres full-text + RRF + clinical synonym expansion**; real embeddings are a build argument | works offline and in tests; the evaluation showed what a lexical embedder cannot do | build with `FASTEMBED=1`, `EMBEDDER=fastembed` |
| 3 | Row-level security keys on the patient's **current ward**; roles: attending, resident (can attest, cannot sign), nurse, PA coordinator, HIM coder, IT admin (no PHI), compliance | matches MediTrack's users; simple to explain and test | `ai/copilot/access.py`, `schema_rls.sql` |
| 4 | The assistant **never attests** and **never generates free prose for the payer**: the letter is assembled from verified statements | a payer letter is a legal document | `priorauth.compose_letter` |
| 5 | Prior-auth service date uses the **chart's own clock** (latest chart date), not the real date | the synthetic data is dated to a fixed demo day | `priorauth.draft_prior_auth` |
| 6 | **Flags are rules, never risk scores**; no numeric confidence anywhere; lint blocks directives and risk numbers | keeps the product inside the CDS exemption | `copilot/cds.py` |
| 7 | The sheet's "discharge draft" dashboard tab became the **stay summary + discharge packet** on the Overview | the packet (summary, coding, prior auth, blockers) is the draft | `ai/dashboard/src/Overview.jsx` |
| 8 | **The incident drill is a redaction bypass**, not the cross-patient retrieval in `decisions.md` | de-identification is the HIPAA control that fails silently; cross-patient access is covered by RLS tests (variation noted in `incident/README.md`) | `incident/drill.py` |
| 9 | Because of the drill: a **redaction canary**, an alert on unredacted calls, and a gateway that **refuses a real model client without a redactor** (`LLM_IN_BOUNDARY=1` to opt out) | closes the root cause | `copilot/gateway.py`, `canary.py` |
| 10 | Terraform region **East US 2** (as decided); **pgAudit added**; Foundry resources and Entra app registrations are **not** in Terraform | model availability/quotas vary and Entra needs directory permissions: documented manual steps | `deploy/terraform/azure` |
| 11 | Added `ward-code` to MediTrack's FHIR Encounter, `AI_BACKEND_JWKS_FILE` and `AI_LAUNCH_URL` to its seed (on `legacy`) | consumers need the ward for unit access; onboarding by file | small, in `legacy` history |
| 12 | Fictional stakeholders for the course: Dr. Naomi Feldman (CMIO), Marcus Webb (clinical ops), Walter Brandt (CISO), Sam Whitlock (IT, already in the seed data), Carol Hughes (prior-auth coordinator, in the seed data) | the mock call needs people | `course/04-*.md` |
| 13 | Evaluation **gate thresholds** (e.g. ≥ 0.80 correct, 0 unsafe) and the **budget assumptions** (45 discharges/day, $0.10/discharge) are mine | starting points | `evalkit/golden/gates.json`, `evalkit/budget.py` |
| 14 | Pilot **targets** in the scope document (TAT ≤ 4.8 h, queries ≤ 25%) are proposals, labelled as such | the sponsor sets them | `course/03-scope-document.md` |
| 15 | LLM prices are illustrative list prices (gpt-oss-20b $0.075/$0.30 per million tokens) | check the provider's sheet | `copilot/llm.py` `PRICES` |

## 6. What I need from you

1. **A real model run**: set `GROQ_API_KEY`, run the evaluation, tell me what breaks (or run it and read `evalkit/README.md`).
2. **Run `scripts/make_tags.sh`** from a machine that can push tags.
3. **Names and numbers in the course documents**: confirm or change the fictional people and the pilot targets.
4. **Two clinician graders** (or accept that the grading workflow is demonstrated, not performed) and review `ai/evalkit/golden/cases.json` with one of them.
5. **GitHub**: branch protection on `main`/`integrated`, the Actions secrets/variables listed in `release.yml`, a self-hosted runner if you want the signed-release flow.
6. **Azure** (if you want the cloud half run): credentials for a subscription; `terraform validate` will probably need small fixes.
7. **Older work**: the webinar repo `FDE_health_care` (branch `claude/serene-cori-14xpa7`) still has the India-version UI/Azure changes and `docs/HANDOFF_local_claude.md`; its `webinar-content.md` **still says "MIS replica"** (the design changed to a read-only role + views on the same database), and its Azure deployment was never tested. Both repositories are independent; this capstone does not depend on that one.

## 7. The weakest parts (look here first)

1. **Quality on real notes is unknown.** The golden set was written from three hand-made charts. Real notes are longer, uglier and contradictory. Expect the first real evaluation to be humbling, and treat that as the point of having it.
2. **The prior-auth UI uses `prompt()` dialogs** for the human edit steps (statement, value, reasons). It works and is tested through the API; it is not pretty.
3. **De-identification is best effort on free text**, as the documents say repeatedly; the BAA is the real control.
4. **The rule-based flags are narrow** (five rules). They are transparent, not comprehensive.
5. **The mock payer is simple** (four services, deterministic). Real payers differ in every detail.
6. **Operational configuration has never met a real cluster** (section 4). I would expect a day of fixes.
7. **Some tests share one PostgreSQL database with the dev stack**: running the tests reseeds MediTrack. Use a separate `MEDITRACK_DB_URL` if you keep a dev stack running.
8. **Tag pushes** (section 3).

## 8. Suggested next steps

1. Real-model evaluation, then tune prompts and `rag.SYNONYMS` against the failures.
2. First deployment to kind, then AKS; pin chart versions; run `terraform validate`.
3. Record the executive readout and the demo; use `course/check_submission.py` as the dry run of the assignment.
4. Add five golden cases from a clinician's own reading of the charts.
5. Replace `prompt()` dialogs with proper forms; add a keyboard-only pass.
