# Integrating the AI system with the legacy EHR

This is a work-in-progress teaching record. It separates confirmed behavior from
steps that still need implementation and verification. Use synthetic data only.

## Starting point

- The Azure legacy EHR and the separate Streamlit POC share a temporary VM. The
  POC reads the EHR's FHIR API; it is not the integrated dashboard.
- The existing integrated code contains an AI service, a dashboard, a SMART
  launch, FHIR access, a simulated payer, webhooks, and an AI database.
- The integrated checkout had uncommitted work. We copied its source and local
  authentication work into an isolated `course/integrated-live-ehr` worktree;
  the original checkout was not reset or overwritten.
- The running legacy VM and its patient data remain in place. No database reset
  or reseed is part of this work.
- The VM already has an Azure auto-shutdown schedule to control spend. It is not
  a hard cap: disk, public IP, model usage, and billing delays are separate.

## The intended connection

```text
Clinician -> legacy EHR -> SMART launch -> integrated AI dashboard
                   |                           |
                   +------ FHIR API <----------+
                   +------ events/webhooks ---> AI service -> AI database
                                                |        -> simulated payer
                                                +--------> Azure Foundry
```

The AI database is separate from the legacy EHR database. FHIR reads the EHR's
chart; the AI database supports the AI service's own workflows. An existing POC
remains separate. Preserve the existing legacy data when registering clients,
adding webhook configuration, and provisioning the AI database.

## Authentication gate: not yet complete

The integrated code refuses deployment startup while staff OIDC is unfinished.
The deployed legacy teaching image has a user selector rather than a verified
staff session. The integrated service expects session-bound SMART tokens and
checks the originating staff session. These are not interchangeable. Network
restriction alone does not provide staff authentication. Do not bypass the
deployment guard, disable session checks, or present the existing selector as
an authenticated deployment.

Before a deployed cutover, implement and verify the identity provider login,
explicit staff identity mapping, protected EHR routes, session-bound SMART
launch, role and patient authorization, logout/revocation, and negative tests.
Then deploy alongside the existing legacy service, and switch traffic only after
the new flow is verified and rollback is prepared.

## Verification record

The deployed integrated system has not yet been enabled.

### 1. Validate on an isolated local stack

The verification stack must have its own Compose project, ports, images, and
volumes. Never point destructive test fixtures at an existing hospital database.
For a fresh reproduction, from this worktree:

```bash
export PG_PORT=15441 PAYER_PORT=18096 MEDITRACK_PORT=18095
export PUBLIC_BASE_URL=http://localhost:18095 LLM_PROVIDER=offline
docker compose -p cityforward-integrated-staging -f docker-compose.yml -f deploy/course/integrated-staging.yml build meditrack
docker compose -p cityforward-integrated-staging -f docker-compose.yml -f deploy/course/integrated-staging.yml build ai
docker compose -p cityforward-integrated-staging -f docker-compose.yml -f deploy/course/integrated-staging.yml up -d --no-build
python deploy/course/verify_local_integration.py
```

The verification script provisions a temporary random password only in the
isolated staging EHR, logs in as the synthetic attending, opens a chart, follows
the SMART authorization-code/PKCE flow, calls the AI dashboard and summary, then
logs out and confirms the old token is rejected. It does not print the password
or token. Expected: three `passed` lines. The offline provider means this verifies
the integration and fallback, not a live model.

On this run the same workflow passed using previously built local login images
with the current backend source mounted into the isolated containers. The
customer login test and 13 AI/auth/redaction tests also passed against a separate
throwaway PostgreSQL instance. These results do not verify an Azure EHR launch.

To reproduce those database tests, first create a **new disposable** PostgreSQL
container. These tests intentionally reset its data:

```bash
docker run -d --name cityforward-integration-test-db \
  -p 127.0.0.1:15440:5432 -e POSTGRES_PASSWORD=postgres \
  -v "$PWD/customer/platform/compose/initdb:/docker-entrypoint-initdb.d:ro" \
  pgvector/pgvector:pg16
export CITYFORWARD_DISPOSABLE_TEST_DB=1
export MEDITRACK_DB_URL=postgresql://meditrack:meditrack@localhost:15440/meditrack
export COPILOT_DB_URL=postgresql://copilot:copilot@localhost:15440/copilot
APP_ENV=local AUTH_MODE=local python -m pytest -q customer/tests/test_local_login.py
APP_ENV=local AUTH_MODE=demo python -m pytest -q \
  ai/tests/test_foundry_identity.py ai/tests/test_smart_token_verification.py \
  ai/tests/test_redact.py customer/tests/test_auth_config.py deploy/tests/test_auth_guard.py
```

Wait until `docker exec cityforward-integration-test-db pg_isready -U postgres`
reports it is accepting connections before running pytest. Afterward stop and
remove this disposable container; it is unrelated to the normal local EHR.
The passwords above are the deliberately non-secret credentials in the demo
database initializer, not real staff or cloud credentials.

To remove only this temporary stack and its synthetic volumes when finished:

```bash
docker compose -p cityforward-integrated-staging -f docker-compose.yml -f deploy/course/integrated-staging.yml down --volumes
```

### 2. Prepare model authentication

The integrated model adapter supports `FOUNDRY_AUTH=managed_identity` in
addition to the existing key-based setup. It obtains a renewable Entra token
instead of storing an API key. A harmless test prompt using this adapter and the
existing VM identity successfully reached the Foundry deployment. This was an
adapter test, not a deployed integrated application or a patient-chart test.

### 3. Deploy the integrated system

**Deployed alongside the legacy demo; browser sign-in not yet verified.** State
checked on 2026-11-07, from outside the VM:

- The secured EHR, the integrated AI service (with its own AI database) and an
  HTTPS gateway on port 8443 run beside the legacy and POC containers.
- Port 8443 is open only to the owner's current public IP (NSG rule
  `course-integrated-https`). If your network changes, update that rule.
- `/` serves the staff sign-in page with a valid certificate, `/int/census`
  returns 401 without a session, `/ai/healthz` returns 200, and
  `/auth/entra/start` redirects to the Microsoft sign-in page.
- Verified in a browser on 2026-11-07 (owner signed in with Microsoft): the
  Entra callback created a staff session (Dr. Priya Raman, ATTENDING) and the
  census loaded; opening IP2610-0142 and the AI Clinical Insights tab ran the
  SMART launch and token exchange and loaded the dashboard and readiness
  checks; "Ask the chart" returned a grounded answer from the live Foundry
  model (`LLM_PROVIDER=foundry`, `FOUNDRY_AUTH=managed_identity`); after
  "Log out", `/auth/session`, `/int/census` and the AI dashboard API all
  returned 401.
- Verified on Azure, second session (same day, signed in as the ATTENDING):
  - Access scope: opening Doyle (IP2610-0151, unit 5S-ORTH) from the EHR and
    then the AI tab showed "This patient is on another unit" with an audited
    emergency-access request; the AI API returned 403. Break-glass was NOT used.
  - Prior authorization: Hale's tab loaded (200) and correctly suggests nothing
    ("Nothing in the notes suggests a service that needs prior authorization").
    The patients that do have suggestions (Doyle: SNF-ADM and E0143; Whitaker:
    E1390) are outside the attending's unit and are meant for the case manager
    or PA coordinator roles, so the drafting path was not exercised.
  - Coding: "Suggest codes for this stay" returned worksheet CW-0A50F6EBCB
    (version 1, OPEN) with documentation queries (provisional STEMI, unspecified
    hyperlipidemia) and four lines. Accepting I10 recorded the decision as
    reviewed by `attending.raman`; "Finalize codes" stayed disabled with
    "Review 3 remaining code(s) before finalizing". Finalize was not clicked.
  - No 5xx responses in the AI service log during these steps.
  - Break-glass, with the owner's approval: emergency access to Doyle was
    requested with the reason "Owner-approved integration test ... on
    synthetic demo data (not a real emergency)". The dashboard then loaded.
    This creates an audit entry and a privacy-officer review item on the Azure
    demo; the 4-hour grant ends on its own.
  - Prior authorization on Doyle: the tab suggested E0143 (walker) and SNF-ADM,
    each with the quoted chart passage. "Prepare draft" for SNF-ADM created a
    draft (POST 201) that lists each payer requirement as Documented (with
    "Show evidence") or Not documented (the discharge plan, with a "Provide"
    action). "Approve for submission" was NOT clicked, so nothing reached the
    simulated payer.
- Not yet verified on Azure: that a SMART token copied before logout is
  rejected afterwards (checked only on the isolated local stack), prior
  authorization approval, submission and the payer decision, coding finalize
  and write-back, webhooks into the AI service, roles other than ATTENDING,
  the privacy-officer review of the break-glass entry, and sign-in by an Entra
  account that is not mapped to a staff member.

### 4. Real AI in the AI Clinical Insights tab (fix and re-test, 2026-11-07)

**Symptom.** "Hospital stay summary" showed "Template summary (no AI used): model
unavailable or invalid output: no JSON object in the model reply", and the
discharge packet showed "template (model unavailable or invalid output)". The
chart text itself was fine; the AI had not answered.

**Cause.** `gpt-oss-120b` is a reasoning model: it thinks first, and those hidden
reasoning tokens count against the output cap. The Azure compose file set
`LLM_MAX_OUTPUT_TOKENS: '2000'`. On larger charts (about 3,000 to 3,700 input
tokens) the model used all 2,000 tokens thinking and returned an empty answer.
The `llm_call` audit table showed it: the failed rows had `status=invalid` and
`tokens_out=2000` exactly; the same patient's smaller calls succeeded. A direct
probe of the model with a small synthetic prompt answered normally, so the
endpoint, the managed identity and the prompt path were healthy.

**Fix** (three small changes):

- `deploy/course/azure-integrated.yml`: `LLM_MAX_OUTPUT_TOKENS` 2000 to 8000 (you
  pay only for tokens used) and new `LLM_REASONING_EFFORT: medium`.
- `ai/copilot/llm.py`: optional `LLM_REASONING_EFFORT` (low, medium, high), and an
  empty reply with `finish_reason=length` now raises "the model used all N output
  tokens on reasoning and wrote no answer: raise LLM_MAX_OUTPUT_TOKENS or lower
  LLM_REASONING_EFFORT" instead of the misleading "no JSON object".
- `ai/tests/test_foundry_identity.py`: a test for that error (run with
  `PYTHONPATH=ai python -m pytest --noconftest ai/tests/test_foundry_identity.py`,
  which never touches a database).

Redeploy only the AI service (legacy, POC, EHR and database untouched):

```bash
cd ~/cityforward-integrated && C='sudo docker compose -p cityforward-integrated --env-file runtime.conf -f deploy/course/azure-integrated.yml'
$C build ai-integrated && $C up -d --no-deps ai-integrated
```

Teaching point: when a model-backed feature "falls back", read the audit row (status
and `tokens_out`) before changing prompts. A token count equal to the cap means
truncation, not a bad prompt. Template results are not cached, so Regenerate or
Refresh calls the model again.

**Re-test on Azure as the ATTENDING (patient IP2610-0209, 4W-CARD), all with the
live model:**

| Feature | Result |
| --- | --- |
| Overview: Hospital stay summary (Regenerate) | "AI summary by foundry:gpt-oss-120b. Every citation and number was checked against the chart." Findings carry source chips. |
| Overview: Needs attention (rule-based) | Loads; no documentation gaps found. |
| Overview: Discharge packet (Refresh) | Stay summary "ready, cached llm". One earlier Refresh fell back with "the model's summary failed grounding checks twice": the grounding gate rejected two model drafts and fell back to the template, which is by design (the model is nondeterministic). A second Refresh passed. |
| Timeline | Events load; clicking an event shows its source resource in the side panel; type filter chips toggle. |
| Ask the chart | Medications question: grounded list with cited passages (C1, C3, ...). Unanswerable-by-design question (penicillin allergy): answered "No Known Allergies" and cited the note. |
| Prior authorization | Nothing suggested for this patient (correct: no qualifying service in the notes). The model-written draft path was verified on Doyle earlier (`prior-auth` call `ok`). |
| Coding | "Generate new version" produced worksheet CW-7A24F390D7 with documentation queries. Not finalized. |

After the fix the audit table shows 6 model calls (summary x3, ask x2, coding x1),
all `ok`, with no 4xx or 5xx from the AI service. One summary
used 2,391 output tokens, which the old cap would have truncated.

Not re-tested here: a grounding failure on the packet can still happen on any
single draft (see above); finalize, approve and submit paths are as listed in the
"Not yet verified" list.

### 5. Reject and Change buttons did nothing (fix, 2026-11-07)

**Symptom.** On the coding worksheet, Accept worked but Reject and Change did
nothing visible.

**Cause.** Both buttons asked for the reason through the browser's `prompt()`
pop-up. A browser or embedded frame that suppresses dialogs makes `prompt()`
return nothing at once, and the code then silently skips the request. The same
pattern was in three prior-authorization actions.

**Fix.** `ai/dashboard/src/parts.jsx` has a small `InlineForm` that asks on the
page (fields, a disabled submit until the minimum length is met, Cancel, and the
server's error shown inside the form). Coding Reject and Change, and prior
authorization Confirm, Provide and "Approve anyway" now use it. No `prompt()` is
left in the dashboard. The form closes only when the server accepts the request.
Deploy as in section 4 (the dashboard is built inside the AI image).

**Checked on Azure as the ATTENDING (IP2610-0204):** Reject needs a reason of at
least 5 characters, then the line shows "rejected", the reason and the reviewer.
Change with the invalid code `ZZZ` stays open and shows "'ZZZ' is not a billable
ICD-10-CM code" inside the form; with `I50.22` the line shows "modified, was I50.9".
The decisions were saved on synthetic demo data with test reasons ("Test invalid
code", "Test of inline reject form"). The prior-authorization forms compile and use
the same component but were not clicked through in a browser.

### 6. Revising a coding decision (2026-11-07)

**Symptom.** A coder accepted J32.9 and J01.90, then Finalize failed with "J32.9 and
J01.90 cannot be reported together (Excludes1)". The decided lines had no buttons, so
the mistake could not be fixed.

**Fix.** The backend already allowed a new decision while the worksheet is OPEN; the
screen hid the buttons. Every decided line now has "Revise decision", which brings
back Accept, Reject and Change (and "Keep ..." to leave it as it was). The history
entry records the earlier answer, for example `reject J01.90 (was accepted)`, and the
audit log keeps every decision. If the line chosen as principal diagnosis is revised
away, the principal choice clears. The Finalize error now also shows next to the
Finalize button. Once a worksheet is FINAL, decisions are locked.

**Checked.** Test `test_a_coder_can_revise_a_decision_to_clear_a_conflict` (accept
everything, Finalize returns 409 Excludes1, reject the conflicting line, Finalize
returns 200) passes on a disposable database. On Azure, J01.90 was revised from
accepted to rejected with a reason. Finalize was not clicked (it files a document
into the EHR). One older test in `ai/tests/test_coding.py`,
`test_worksheet_flags_documentation_queries_and_needs_human_decisions`, fails on the
disposable database with or without this change (a code status expectation), so it is
a data or environment difference to investigate, not a result of this work.

### 7. Finalize codes (2026-11-07)

**Report.** "Finalize codes" seemed not to work.

**Cause.** Not the button. The presenter's public IP had changed, and the firewall
(NSG rules `course-owner-https-ssh` and `course-integrated-https`) only allows one
address, so every request from the browser timed out and the page stayed busy. Run
`curl -m 15 https://<demo domain>:8443/ai/healthz` to see it: a timeout means the
IP changed. Fix: set both rules' source to the new address, for example
`az network nsg rule update -g <group> --nsg-name cf-demoNSG -n course-integrated-https --source-address-prefixes <new-ip>/32`
(same for the other rule). The staff session also expires while blocked, so sign in
again.

**Result after access came back.** On worksheet CW-F3D6D4397C (principal I50.22,
six secondary codes, the conflicting J01.90 rejected): `POST /api/coding/.../finalize`
returned 200, the worksheet became FINAL ("finalized 7 code(s)" in its history), and a
`CODING_SUMMARY` document (id 76, filed by `cityforward-ai-backend`) appeared in the
patient's Documents tab in the EHR. This is the write-back into the legacy record.
That patient's demo chart now has a finalized worksheet and that document.

**Small change.** After finalizing, the screen showed nothing but a FINAL badge. It
now shows a "Codes finalized" panel with who, when, the principal diagnosis and the
secondary codes, and says the decisions are locked.

**Test note.** In the built-in browser pane a native dropdown cannot be driven by
keyboard, so the principal diagnosis was set through the page's change event; the
Finalize click itself was a real click.

Do not run the destructive legacy seed against the existing Azure database.

### Local test database incident

An initial test invocation mistakenly used the existing local Compose database
on port 5432. Existing fixtures reseeded the local MediTrack tables and cleared
several local AI working tables. This may have lost user-created local demo data.
The Azure demo database was not used by the tests. A workspace search found no
database backup; do not overwrite the current database in an attempted restore.
Fixtures now require an explicit disposable-database opt-in before they reseed.
Run subsequent database tests only against a separately created disposable
database with both test database URLs set explicitly.
