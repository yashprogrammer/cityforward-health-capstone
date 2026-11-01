# Part 3: The integrated system

The hospital system remains in `customer/`. The new vendor application lives in
`ai/`: it reads patient records through MediTrack's FHIR interface, launches a
dashboard from the patient's chart, and stores its own indexes and workflow data.
The mock payer is still a simulation. All patients and staff are fictional.

The code in this branch is a teaching system. Its demo user switcher is not a
staff login; do not use this version with real patient data or expose it as a
secure production deployment.

## Run the local demo

Start Docker Desktop, then run from this branch's root:

```bash
docker compose up --build -d
```

Open http://localhost:8001, pick a patient in **IPD Census**, and open
**AI Clinical Insights** in the patient file. The dashboard presents overview,
timeline, chart questions, prior authorization, and coding review. The offline
mode can show a labelled fallback without a model call; a model-backed demo
needs the provider configuration in `ai/README.md`.

## Explain one feature at a time

| Lesson | Files to start with | What to show |
|---|---|---|
| EHR connection | `ai/copilot/fhir_client.py`, `chart.py` | FHIR resources become a patient chart with source references. |
| Chart search | `ai/copilot/rag.py`, `ask.py`, `summary.py` | Index, retrieve, generate, check citations, and fall back. |
| Prior authorization | `ai/copilot/priorauth.py`, `priorauth_api.py` | Compare evidence to payer rules; a person reviews and signs off. |
| Coding | `ai/copilot/coding.py`, `terminology.py` | Suggest codes with evidence; a coder or attending reviews them. |
| Privacy and access | `ai/copilot/redact.py`, `audit.py`, `access.py` | Review redaction, audit, roles, unit rules and their limitations. |
| Events | `ai/copilot/events.py` | Verify a webhook, queue it, and process it separately. |
| UI | `ai/dashboard/src/` | Show the chart, evidence, and human decision points. |

## Show what changed in each build session

The repository already contains lesson snapshots. To see exactly the files and
lines introduced by a session, use `git show` on the corresponding branch:

```bash
git fetch origin
git show --stat origin/session/bs2-rag
git show origin/session/bs2-rag
```

Repeat with `bs1-poc`, `bs3-prior-auth`, `coding`, `bs4-phi-audit`, `rbac-rls`,
`bs5-webhooks`, and `bs6-dashboard`. These are historical snapshots, not a
promise that the current integrated branch contains only that lesson's code.
The lesson map in `course/README.md` connects them to the course materials.

For the first two stages, switch to `legacy` for the hospital before the AI
project and `poc` for the standalone Streamlit summarizer. Use separate clones
or worktrees if you need to switch while preserving uncommitted work.
