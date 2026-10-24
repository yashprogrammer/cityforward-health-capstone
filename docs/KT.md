# Knowledge transfer: continue this project on your own machine

*Written 2026-10-24 so you (or a local Claude Code session) can pick up exactly where the cloud session stopped. Read `docs/FINAL_REVIEW.md` for the long version; this file is the practical one.*

## 1. Context in five lines
- You are building an Udemy **Forward Deployed Engineer** course. This repo is **Capstone 1, Healthcare**: a fictional US hospital (**CityForward Health**) with a legacy EHR (**MediTrack**). Learners add an AI layer, then do the compliance, deployment, security review, readout and handoff.
- Everything is fictional; patients are synthetic (Synthea). The customer's world is pre-built and *explained* by you; learners build the AI layer.
- Repo: `https://github.com/yashprogrammer/cityforward-health-capstone` (private). Older webinar repo `FDE_health_care` is separate and frozen.
- Decision record: `docs/decisions.md`. Deviations from it: `docs/FINAL_REVIEW.md` section 5.
- The **lecture-to-material-to-code map** is `course/README.md`.

## 2. Repo map (stacked branches)
| Branch | Contains |
|---|---|
| `main` | course materials (`course/`), `docs/` (decisions, FINAL_REVIEW, this file), `scripts/make_tags.sh` |
| `legacy` | `customer/`: MediTrack EHR (FHIR R4, SMART launch, signed webhooks), mock payer, Synthea data + ETL, Dockerfile, `docker-compose.yml` |
| `poc` | `poc/`: Build Session 1 LangGraph summarizer + Streamlit |
| `integrated` | `ai/` service, `ai/dashboard` (React SMART app), `ai/evalkit` (golden set), `deploy/` (Helm, Argo CD, Terraform, kind), `docs/compliance/`, `incident/`, `.github/workflows/` |

Changes flow upward: edit course material on `main`, code on the lowest branch that owns it, then merge `main -> legacy -> poc -> integrated`.
Session markers are branches named `session/<tag>` (e.g. `session/bs3-prior-auth`) because tag pushes were blocked in the cloud sandbox.

## 3. What is done
- **EHR + payer** (`legacy`): 39 tests. FHIR API, SMART EHR launch with PKCE, backend-services auth (`private_key_jwt`), HMAC-signed CloudEvents with retry/dead-letter/replay, mock payer (approve/deny/pend/manual review).
- **POC** (`poc`): 13 tests. Cited patient-history summarizer with grounding checks and a no-model fallback.
- **AI service** (`integrated`, 220 tests): hybrid retrieval (pgvector + full-text + clinical synonyms), grounded Q&A and summaries, prior-auth assistant (human attests/approves/submits), ICD-10-CM coding worksheet, FDA CDS controls, de-identification gateway, hash-chained audit log, RBAC + Postgres row-level security + break-the-glass, Keycloak/SSO claim mapping, webhook ingestion, golden evaluation harness with clinician review pack and release gates, cost/latency controls (budget breaker, result cache, per-purpose model routing), multi-agent supervisor, redaction canary, incident drill.
- **Dashboard**: React SMART app launched from MediTrack (timeline, rule-based flags with Why panel, summary, Ask, prior auth, coding, discharge packet).
- **Deployment** (`deploy/`, 29 tests): Helm chart validated against Kubernetes 1.31, Argo CD app-of-apps, Kyverno/Falco/OTel/Prometheus/Keycloak config, Azure Terraform (East US 2, private AKS, Postgres+pgvector, Key Vault, pgAudit), kind bootstrap.
- **Security pack** (`docs/compliance/`): FDA CDS memo, HIPAA/HITRUST mapping with gaps, threat model, incident response plan, vendor register, completed questionnaire; a test checks every evidence path exists.
- **Course materials** (`course/`): discovery playbook, MECE tree, scope doc, mock call, debrief, wedge memo, POC presentation, executive readout, handoff, interview guide, assignment + `check_submission.py`, lecture notes.
- **Proven by running**: the full Docker Compose stack, driven in a real browser (SMART launch, every tab) and by a real signed webhook; the real OTel collector image scrubbing PHI.

## 4. What is NOT done / not verified
| Item | Status |
|---|---|
| Any run with a **real LLM** | never run (no key reachable from the sandbox). Expect prompt/JSON tuning |
| **kind** cluster, **Argo CD**, **Linkerd**, **Kyverno**, **Falco**, **Keycloak** import | written, statically checked only |
| **Terraform** `init/validate/plan` | only `fmt` + parse done; expect small azurerm 4.x fixes |
| **GitHub Actions** (`ci.yml`, `release.yml`) | never executed on GitHub; release needs a self-hosted runner in the VNet |
| Real embeddings (`FASTEMBED=1`) | never built (Hugging Face blocked in sandbox) |
| **Clinician grading** | workflow exists, nobody has graded |
| Argo chart versions | ranges, unverified: pin after first sync |
| Real **git tags** | pushed as `session/*` branches; run `scripts/make_tags.sh` |
| UI polish | prior-auth edits use browser `prompt()` dialogs; mobile layout and accessibility not audited |
| Foundry model + Entra app registrations | not in Terraform (manual steps) |

## 5. Do this next, in order

### Step 0: prerequisites on your machine
Docker, Git, Python 3.13, Node 22, `kind`, `kubectl`, `helm`, (later) `terraform` and the Azure CLI. Create a **new** Groq key (the one pasted in chat earlier should be revoked) and keep it only in an environment variable, never in a file.

### Step 1: run it (15 min)
```bash
git clone https://github.com/yashprogrammer/cityforward-health-capstone && cd cityforward-health-capstone
git checkout integrated
export GROQ_API_KEY=<new key>            # omit for no-AI mode
docker compose up --build                # prefix PG_PORT=55432 if 5432 is busy
```
Open http://localhost:8001 -> open patient Robert Hale -> **AI Clinical Insights**. Try the "act as" switch (e.g. `pa.hughes`, `attending.okafor`). Note anything that looks wrong: that is the first look at a real model.

### Step 2: run the tests (10 min)
Needs PostgreSQL with pgvector and roles `meditrack` and `copilot` (see `.github/workflows/ci.yml` for the exact SQL, or `docker compose up -d db` and set `PG_PORT`).
```bash
pip install -r customer/requirements.txt -r ai/requirements.txt pyyaml kubernetes-validate python-hcl2
PYTHONPATH=ai:customer pytest customer/tests ai/tests deploy/tests       # expect ~288 passed
PYTHONPATH=poc pytest poc/tests                                         # on the poc branch: 13
```
Gotcha: the tests **reseed MediTrack's database**, so don't run them against a database you are demoing from.

### Step 3: first real-model evaluation (30 min)
```bash
PYTHONPATH=ai:customer python -m evalkit run --provider groq --out results/gpt-oss-20b
python -m evalkit gate results/gpt-oss-20b
```
Read the failing cases in `results/.../outputs.jsonl` and `review_pack.md`. Likely fixes: prompts in `ai/copilot/{ask,summary,priorauth,coding}.py`, synonyms in `ai/copilot/rag.py` (`SYNONYMS`), JSON extraction in `ai/copilot/llm.py`. Bring `FASTEMBED=1` into play after this: `docker compose build --build-arg FASTEMBED=1 ai` and `EMBEDDER=fastembed`, then re-run to see the retrieval difference. Offline baseline for comparison: `ai/evalkit/baselines/offline`.

### Step 4: tags (2 min)
`scripts/make_tags.sh --dry-run`, then `scripts/make_tags.sh`. It turns every `session/<name>` branch into a tag `<name>` and pushes them. (Tags let learners `git checkout bs3-prior-auth` to land on exactly one lecture's code.)

### Step 5: Kubernetes on your laptop (1-2 h)
`deploy/kind/bootstrap.sh` (needs Docker, kind, kubectl, helm). It has never run, so expect small fixes. Then `helm test cityforward -n cityforward`. For a policy-enforcing cluster use Calico (kindnet ignores NetworkPolicy).

### Step 6: GitHub setup (30 min)
Branch protection on `main` and `integrated`; push once and watch `ci.yml`; add the Actions variables/secrets named in `release.yml` when you want signed releases; enable secret scanning and Dependabot.

### Step 7: Azure (when ready, half a day)
`cd deploy/terraform/azure && cp terraform.tfvars.example terraform.tfvars` (set your admin group), `terraform init && terraform validate && terraform plan`. Fix argument names as needed. Apply, run `scripts/db-roles.sql` from inside the VNet, put secrets in Key Vault, then `kubectl apply -f deploy/argocd/project.yaml -f deploy/argocd/root-app.yaml` and **pin the chart versions** in `deploy/argocd/apps/`. Full list: `deploy/README.md`.

### Step 8: course work only you can do
- Read `course/` end to end; confirm the fictional names and the pilot targets in `course/03-scope-document.md`.
- Find two clinicians to grade a pack (`review_pack.md` -> `grades_template.csv`) and extend `ai/evalkit/golden/cases.json`.
- Record the demo and the executive readout; run `course/check_submission.py` against your deployment as a dry run of the assignment.
- Polish: replace the `prompt()` dialogs in `ai/dashboard/src/PriorAuth.jsx`, test on a phone/tablet, run an accessibility audit.

## 6. Useful commands
| Task | Command |
|---|---|
| Incident drill (instructor session) | `PYTHONPATH=ai:customer:. python -m incident.drill --pause` |
| Cost/latency budget | `PYTHONPATH=ai:customer python -m evalkit.budget` |
| Browser smoke test of the whole flow | `python ai/e2e/smoke.py ./shots` (needs `pip install playwright`) |
| Render the Helm chart | `helm template t deploy/helm/cityforward -f deploy/helm/cityforward/values-aks.yaml` |
| Run without Docker | see `customer/README.md` and `ai/README.md`; `ai/scripts/devgateway.py` stands in for the gateway |

## 7. Gotchas learned
- Port 5432 busy -> `PG_PORT=55432 docker compose up`.
- `pkill -f` patterns can match your own shell command: kill by PID.
- A real model client **cannot be constructed without the redactor** (deliberate; `LLM_IN_BOUNDARY=1` opts out for a model inside the hospital network).
- Metrics are on port 9100 inside the cluster, never through the ingress.
- Daily spend cap: `COPILOT_DAILY_BUDGET_USD`. Per-job model: `LLM_MODEL_CODING=...`.
- Do not put real patient data anywhere; the BAA/privacy items are taught as concepts, not something learners must obtain.

## 8. Prompt to start a local Claude Code session
> Read `docs/KT.md`, `docs/FINAL_REVIEW.md` and `course/README.md` in this repo (branch `integrated`). Start the stack with `docker compose up --build` using my `GROQ_API_KEY` from the environment, run the tests, then run `python -m evalkit run --provider groq` and fix the failures in the order of KT section 5. Never write the key to a file or commit it. Ask me before pushing.
