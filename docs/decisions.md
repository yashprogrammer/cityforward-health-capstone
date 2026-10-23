# Capstone 1 (Healthcare): decision record and build plan

The Udemy capstone for the Forward Deployed Engineer course, built from the lead's sheet ("Capstone 1, Healthcare: CityForward Health").
Decisions from the grill-me rounds of 2026-10-23. All organizations, people and patients are fictional; patient data is synthetic (Synthea).

## The premise

- **The customer's world is given, not built by learners.** CityForward Health (US) runs **MediTrack**, a legacy EHR, plus a hospital **Kubernetes platform** run by its IT team. The instructor *explains* both; learners study them like an FDE on day one.
- **Learners build the AI layer** and everything an FDE must do to get it into production at a hospital: the integration, compliance controls, deployment and security review.
- The India-based webinar version lives in a separate repository (`FDE_health_care`, branches `part-1-legacy`, `part-2-poc`, `part-3-integrated`) and is **frozen**. Its MediTrack is the starting point for this repository's `legacy` branch.

## Decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Setting | **US.** Client **CityForward Health** (name from the sheet). MRN, NPI, payers and **prior authorization**, ICD-10-CM, $, MM/DD dates. HIPAA, HITRUST and FDA CDS apply. |
| 2 | Scope | **Every build session in the sheet** is built: summarizer POC, RAG, prior-auth assistant, ICD-10-CM/SNOMED coding with clinician sign-off, PHI redaction + audit, RBAC + row-level security, EHR webhooks, clinician golden eval, clinical dashboard, Kubernetes deployment, multi-agent split. Non-code lectures (discovery, mock call, debrief, wedge, readout, handoff, interview) get written course materials. |
| 3 | Patient data | **Synthea** cohort (generated once and committed as FHIR bundles, plus the generator config), ETL'd into MediTrack's legacy tables. A hand-crafted **golden patient** is kept for the demo. |
| 4 | EHR integration surface | MediTrack ships (pre-built) with a read-only **FHIR R4 API** (Cures Act-style), **SMART on FHIR EHR launch** for embedded apps, and **event webhooks** (CloudEvents, signed). The legacy hot-folder stays as a secondary write-back path. |
| 5 | Identity / SSO | **Keycloak** locally (hospital IdP + SMART authorization server), **Entra ID** in Azure. The AI app speaks plain OIDC/SMART, so it works with either. Roles: attending, resident, nurse, prior-auth coordinator, HIM coder, IT admin. Break-the-glass access. |
| 6 | Platform | **Kubernetes**: **kind** locally (free), **AKS** in Azure (East US 2), private networking (no public DB endpoint). Hospital platform add-ons (pre-built): **Argo CD** (GitOps), **Linkerd** (mTLS), **Falco** (runtime alerts), **OpenTelemetry Collector** (PHI redaction) → **Jaeger** + **Prometheus**, optional **Kyverno** policies. All CNCF graduated. |
| 7 | Packaging | The AI app ships as a **Helm chart**, deployed through the hospital's Argo CD. It must pass their policies (non-root, signed images, mesh mTLS, OTel). |
| 8 | IaC | **Terraform** (replaces Bicep): AKS, private Azure Postgres with VNet integration, Key Vault, ACR, Azure Files, Foundry, Entra app registrations. |
| 9 | LLM | **gpt-oss-20b**: **Groq** locally (synthetic data only: no BAA), **Azure AI Foundry** in Azure (Microsoft BAA). Embeddings: a small open-source model run locally (fastembed); a Foundry embedding model in Azure. This boundary is taught as a HIPAA lesson. |
| 10 | Agent framework | **LangGraph from the very first POC.** One graph in the POC; specialist agents (summarizer, coder, prior-auth) under a supervisor in the multi-agent session. The current Pydantic AI code is retired from the capstone line. |
| 11 | RAG store | **pgvector** in the AI app's own Postgres. Row-level security applies to retrieval as well. |
| 12 | Prior auth | **FHIR-based**, modelled on Da Vinci PAS: payer rules check → letter of medical necessity → FHIR prior-auth request → **mock payer API** (approve / deny / pend for more info). |
| 13 | Tracing / audit | **OpenTelemetry → Jaeger + Prometheus in the cluster**; PHI redacted in the collector; nothing leaves the hospital network. LangSmith and Logfire are discussed in the security review as SaaS tools that would need a BAA. |
| 14 | Dashboard | A **SMART on FHIR app embedded in MediTrack**: timeline, risk flags, "Why" panel, discharge draft, coding review, prior-auth tabs. Continues the "a tab, not a new app" lesson. |
| 15 | Compliance demonstrated | **FDA CDS** (four-criteria memo, intended-use labelling, clinician sign-off, "Why" panel, no time-critical alerts) · **HIPAA** (Safe Harbor redaction for POC/eval data and telemetry, BAA boundary, minimum necessary, audit) · **HITRUST** control mapping: encryption (Linkerd mTLS, TLS, encryption at rest), access (SSO roles + RLS + break-the-glass), audit (append-only + read logging + pgAudit + GitOps history), incident response (Falco/Prometheus alerts + IR plan + runbooks), vendor assessment (BAA register, SBOM, signed images). Certification itself is out of scope. |
| 16 | Repository and branches | Own repository, `cityforward-health-capstone` (the webinar repo `FDE_health_care` stays as it is). Stacked branches, like the webinar: **`main`** (course materials only) → **`legacy`** (+ the customer's world: MediTrack, mock payer, hospital platform) → **`poc`** (+ Build Session 1 POC) → **`integrated`** (+ the production AI layer, compliance and deployment; a **git tag per build session** on this branch). Course-material changes are made on `main` and merged upward; code changes on the lowest branch that owns them, merged upward. |

## Licensing constraints (US code sets)

- **ICD-10-CM** is public domain (CMS): the full code table can ship in the repo.
- **SNOMED CT** requires a (free, US) UMLS licence and **cannot be redistributed**. Only the codes that appear in the Synthea output ship; the full terminology is looked up by learners with their own UMLS licence (optional).
- **CPT** is AMA-licensed: no CPT descriptors in the repo. Procedures use **SNOMED** codes (as Synthea does) and HCPCS Level II (public) where needed. The webinar line's CPT codes stay only in the frozen branches.

## Build plan (checkpoint after each phase)

### Phase 0: the customer's world (pre-built, explained by the instructor)

1. **US conversion of MediTrack**: CityForward Health, US fields and labels, payers and prior-auth status instead of TPA.
2. **Synthea pipeline**: cohort generation config, committed bundles, ETL into MediTrack, golden patient.
3. **MediTrack interoperability**: FHIR R4 read API (Patient, Encounter, Condition, Procedure, Observation, MedicationRequest, DocumentReference, Coverage), SMART on FHIR EHR launch against Keycloak, signed CloudEvents webhooks.
4. **Mock payer** service (prior-auth API, payer rules).
5. **Hospital platform**: kind bootstrap script, then Terraform for AKS. Argo CD, Linkerd, Falco, OTel Collector, Jaeger, Prometheus, Keycloak, Postgres; Kyverno policies.

### Phases 1–N: learner build sessions (one git tag each)

| Tag | Sheet session | What gets built |
|---|---|---|
| `bs1-poc` | Build Session 1 | LangGraph patient-history summarizer over Synthea FHIR data (Streamlit), the "vibe-coded" POC |
| `bs2-rag` | Build Session 2 | Chunking + pgvector retrieval with citations |
| `bs3-prior-auth` | Build Session 3 | Prior-auth drafting assistant → mock payer |
| `coding` | ICD-10/SNOMED | Code suggestions with evidence; clinician accept/reject; nothing final without sign-off |
| `fda-cds` | FDA SaMD/CDS | Four-criteria memo, intended-use labelling, guardrails against treatment directives |
| `bs4-phi-audit` | Build Session 4 | Safe Harbor redaction, append-only audit with read logging, pgAudit |
| `rbac-rls` | RBAC & RLS | Keycloak roles → Postgres RLS (including retrieval), break-the-glass |
| `bs5-webhooks` | Build Session 5 | Webhook ingestion: signature check, idempotency, retries, CloudEvents |
| `golden-eval` | Golden eval | Clinician grading UI, golden set, eval runner, regression gate |
| `bs6-dashboard` | Build Session 6 | SMART on FHIR dashboard: timeline, risk flags, "Why" panel |
| `deploy` | VPC/on-prem deployment | Helm chart, Argo CD app, private AKS via Terraform |
| `security-review` | Security review + HITRUST | Filled questionnaire with evidence links, HITRUST mapping, IR plan, vendor register |
| `cost-latency` | Cost & latency | Budgets, Prometheus dashboards, caching/model-routing decisions |
| `multi-agent` | Multi-agent design | Supervisor + summarizer/coder/prior-auth specialists in LangGraph |
| `incident` | What almost went wrong | Scripted near-miss (e.g. a retrieval that crosses patients), diagnosed with traces and audit, fixed |
| `handoff` | Readout, handoff, interview | Executive readout deck outline, ownership/runbook handoff pack, interview case-study notes |

Course materials for the non-code lectures (discovery playbook, MECE issue tree, scope document, mock-call script, debrief, wedge memo) are written alongside.


## Implementation notes (added after the build)

The build followed this record with the deviations listed in [`FINAL_REVIEW.md`](FINAL_REVIEW.md) section 5 (notably: MediTrack is the SMART authorization server with Keycloak as an optional staff IdP, a different incident scenario, pgAudit added, Foundry/Entra not in Terraform, tags pushed as `session/*` branches).
