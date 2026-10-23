# Course materials

Everything an instructor or learner reads rather than runs. Code lives on the other branches; each lecture below says where.
(CityForward Health, MediTrack, all people and payers are **fictional**; patients are **synthetic**.)

## Lecture map (the lead's sheet → material → code)

| Sheet lecture | Material | Code / tag |
|---|---|---|
| Playbook: Discovery, MECE & Scope Docs | [01-discovery-playbook.md](01-discovery-playbook.md), [02-mece-issue-tree.md](02-mece-issue-tree.md), [03-scope-document.md](03-scope-document.md) | none (uses the MIS report in `legacy`) |
| Mock Call: CMIO & Clinical Ops | [04-mock-call-cmio-clinical-ops.md](04-mock-call-cmio-clinical-ops.md) | none |
| Call Debrief | [05-call-debrief.md](05-call-debrief.md) | none |
| Scoping the Wedge | [06-wedge-memo.md](06-wedge-memo.md) | none |
| Working With Synthea FHIR Bundles | [lectures/synthea-fhir.md](lectures/synthea-fhir.md) | `legacy`: `customer/synthea` |
| Build Session 1 (POC) · Presenting the POC | [lectures/build-sessions.md](lectures/build-sessions.md), [07-presenting-the-poc.md](07-presenting-the-poc.md) | `poc` branch |
| Build Session 2 (RAG) · 3 (prior auth) | [lectures/build-sessions.md](lectures/build-sessions.md) | tags `bs2-rag`, `bs3-prior-auth` |
| ICD-10/SNOMED coding with clinician in the loop | [lectures/coding.md](lectures/coding.md) | tag `coding` |
| FDA SaMD or CDS-exempt? | [lectures/fda-cds.md](lectures/fda-cds.md) | tag `fda-cds`, `docs/compliance/fda-cds-memo.md` |
| HITRUST CSF | [lectures/hitrust.md](lectures/hitrust.md) | `docs/compliance/` |
| Build Session 4 (PHI redaction & audit) · RBAC & row-level controls | [lectures/build-sessions.md](lectures/build-sessions.md) | tags `bs4-phi-audit`, `rbac-rls` |
| Build Session 5 (webhooks) · Golden eval · Build Session 6 (dashboard) | [lectures/build-sessions.md](lectures/build-sessions.md) | tags `bs5-webhooks`, `golden-eval`, `bs6-dashboard` |
| VPC / on-prem-style deployment | [lectures/deployment.md](lectures/deployment.md) | tag `deploy`, `deploy/` |
| Passing a hospital-style security review | [lectures/security-review.md](lectures/security-review.md) | tag `security-review` |
| Cost & latency budgeting · Multi-agent design | [lectures/build-sessions.md](lectures/build-sessions.md) | tags `cost-latency`, `multi-agent` |
| What Almost Went Wrong (live incident) | [lectures/incident.md](lectures/incident.md) | tag `incident`, `incident/` |
| Executive Readout to the CMIO | [08-executive-readout.md](08-executive-readout.md) | none (numbers come from your runs) |
| Handoff | [09-handoff.md](09-handoff.md) | tag `handoff` |
| Presenting this project in an interview | [10-interview-guide.md](10-interview-guide.md) | none |
| **Assignment** | [11-assignment.md](11-assignment.md) | `course/check_submission.py` |

## How to teach it

The engagement is one story told in order: a discovery call finds a measurable pain (insured patients wait 6.3 hours after discharge is advised; about 38% of authorizations come back with a documentation query); a **wedge** is chosen; a POC proves the idea cheaply;
the production build session by session adds what a hospital will demand (retrieval, a human in every consequential decision, de-identification, audit, access control, events, evaluation, a dashboard inside the EHR); then deployment, the security review, a near-miss, the readout and the handoff.
Keep returning to two questions: *what would the customer say if we showed them this?* and *what would we say if it went wrong?*
