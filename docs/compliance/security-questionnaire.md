# Hospital security questionnaire: completed for the CityForward Copilot

Format follows the shape of common vendor questionnaires (SIG-lite, HECVAT-style). Answers are written the way they should be: short, honest, with evidence. "Partial" and "No" are not failures here; unexplained "Yes" answers are.
Paths are relative to the repository root.

## A. Governance

| # | Question | Answer | Evidence |
|---|---|---|---|
| A1 | Do you have a documented security program and a named owner? | Partial: controls and owner roles documented; no formal ISMS certification | `docs/compliance/` |
| A2 | Is there a documented risk assessment, reviewed in the last 12 months? | Yes (engineering threat model); organization-wide assessment not yet | `docs/compliance/threat-model.md` |
| A3 | Do you hold SOC 2 / ISO 27001 / HITRUST? | No (planned: SOC 2 Type I first) | `docs/compliance/hipaa-hitrust-mapping.md` gap G4 |
| A4 | Do you sign a HIPAA BAA? | Yes, as a vendor; our sub-processors' BAAs are tracked | `docs/compliance/vendor-register.md` |
| A5 | How are third parties assessed? | tiered procedure with annual review | `docs/compliance/vendor-register.md` |

## B. Data

| # | Question | Answer | Evidence |
|---|---|---|---|
| B1 | What PHI do you receive, store and why? | Reads the chart through FHIR for the patients a clinician opens; stores searchable chart text and the AI work products (drafts, worksheets) in its own database | `ai/copilot/schema.sql`, `ai/copilot/rag.py` |
| B2 | Is PHI minimized? | Yes: patient-scoped, unit-scoped, IDs-only events, de-identified prompts, no text in audit/metrics/traces | `ai/tests/test_redact.py`, `ai/tests/test_telemetry.py` |
| B3 | Do you use PHI to train models? | No. Models are used as-is; contractual no-training terms required from the provider | `docs/compliance/vendor-register.md` |
| B4 | Is data sent outside our network? | Only de-identified prompts to the model endpoint (private endpoint in production) and prior-authorization packets to the payer | `deploy/helm/cityforward/templates/networkpolicy.yaml` |
| B5 | Encryption in transit? | TLS (ingress, HSTS), mTLS in mesh, TLS to PostgreSQL | `deploy/helm/cityforward/templates/ingress.yaml`, `deploy/terraform/azure/main.tf` |
| B6 | Encryption at rest? Key management? | Platform encryption on database, files, vault; customer-managed keys supported; secrets in Key Vault | `deploy/terraform/azure/main.tf` |
| B7 | Data retention and deletion? | Chunks follow the chart (re-index removes deleted text); audit retained per policy; deletion on contract end by dropping the database | `ai/tests/test_rag.py::test_indexing_is_idempotent_and_replaces_changed_text` |
| B8 | Where is data stored (region)? | Single Azure region chosen by the customer (`location` variable); backups geo-redundant within the pairing the customer approves | `deploy/terraform/azure/variables.tf` |
| B9 | Do you use real PHI in development or test? | No: synthetic (Synthea) patients only | `customer/synthea/README.md` |

## C. Access control

| # | Question | Answer | Evidence |
|---|---|---|---|
| C1 | How do users authenticate? | The hospital's SSO: SMART EHR launch with PKCE (EHR session) or OIDC (Keycloak/Entra); MFA is the IdP's policy | `ai/copilot/auth.py` |
| C2 | Role-based access? Least privilege? | Yes: roles for attending, resident, nurse, PA coordinator, coder, IT, compliance; IT sees no charts | `ai/copilot/access.py`, `ai/tests/test_priorauth.py` |
| C3 | Row-level restrictions? | Database row-level security by unit and launch patient; fails closed | `ai/copilot/schema_rls.sql`, `ai/tests/test_rbac.py` |
| C4 | Emergency access? | Break-the-glass with reason, 4-hour expiry, privacy-officer review | `ai/copilot/breakglass.py` |
| C5 | Service-to-service auth? | `private_key_jwt` (RS256), 5-minute tokens, replay protection | `ai/copilot/fhir_client.py`, `customer/tests/test_smart_fhir.py` |
| C6 | Privileged access to production? | Entra RBAC on AKS, local accounts disabled, private API server, Key Vault RBAC; no standing database credentials for humans | `deploy/terraform/azure/main.tf` |
| C7 | Session management? | 60-minute access tokens, in-memory token in the browser, IdP idle timeout | `ai/dashboard/src/smart.js` |

## D. Logging and monitoring

| # | Question | Answer | Evidence |
|---|---|---|---|
| D1 | Are access and changes logged? Tamper-evident? | Yes: append-only hash-chained audit log with verifier; identifiers and outcomes only | `ai/copilot/audit.py`, `ai/tests/test_audit.py` |
| D2 | Can you produce an access report for a patient? | Yes: `GET /api/audit?patient=` | `ai/tests/test_audit.py::test_access_is_recorded_allowed_and_denied_and_the_chain_verifies` |
| D3 | Is monitoring in place? | Prometheus alerts, Falco runtime rules, OTel traces without PHI | `deploy/platform/monitoring/prometheus-rules.yaml`, `deploy/platform/falco/values.yaml` |
| D4 | Log retention? | 365 days online; six-year archive planned | gap G6 |

## E. Application security and change management

| # | Question | Answer | Evidence |
|---|---|---|---|
| E1 | Secure development lifecycle? | Pull requests with CI: tests, SAST (CodeQL, Bandit), dependency audit, secret scan, IaC scan, image scan | `.github/workflows/ci.yml` |
| E2 | Software bill of materials? Signed releases? | SBOM and provenance attached to each image; keyless cosign signature verified at admission | `.github/workflows/release.yml`, `deploy/platform/kyverno/policies.yaml` |
| E3 | How are model and prompt changes controlled? | Treated as releases: the golden evaluation set (automatic metrics + clinician grading) gates them | `ai/evalkit/golden/gates.json` |
| E4 | Vulnerability management? | Automated scanning in CI; disclosure policy; patch SLA proposal: critical 7 days, high 30 | `SECURITY.md` |
| E5 | Penetration testing? | Not yet (gap G3); scheduled before go-live | `docs/compliance/hipaa-hitrust-mapping.md` |
| E6 | Input handling (injection, SSRF, request forgery)? | Parameterized SQL, schema-validated JSON, payload `fhirBase` ignored, size limits, signed webhooks, CSP and same-origin API | `ai/tests/test_webhooks.py::test_payload_fhir_base_is_never_trusted` |

## F. Infrastructure and resilience

| # | Question | Answer | Evidence |
|---|---|---|---|
| F1 | Network segmentation? | Private cluster, default-deny network policy, private endpoints for data services | `deploy/helm/cityforward/templates/networkpolicy.yaml` |
| F2 | Container hardening? | Non-root, read-only filesystem, capabilities dropped, seccomp, no API token; admission policies enforce it | `deploy/tests/test_chart.py` |
| F3 | High availability and backup? | Replicas with PDB/HPA, zone-redundant PostgreSQL, 35-day backups | `deploy/terraform/azure/main.tf` |
| F4 | DR tested? | No drill yet (G5) | |
| F5 | What happens when the AI is down? | Every feature degrades to labelled no-AI behaviour; clinical work continues | `ai/tests/test_rag.py::test_ask_model_outage_degrades_to_passages` |

## G. AI-specific

| # | Question | Answer | Evidence |
|---|---|---|---|
| G1 | Is the product a medical device? | Designed as non-device CDS (clinical features) and administrative support (prior authorization, coding); classification memo with the four criteria | `docs/compliance/fda-cds-memo.md` |
| G2 | How do you prevent fabricated information? | citation and number grounding with fallback; verified by tests | `ai/copilot/grounding.py`, `ai/tests/test_summary.py` |
| G3 | Does the AI act without a human? | No: attestations, approvals, submissions and coding decisions need a named human with the right role | `ai/tests/test_priorauth.py::test_home_oxygen_end_to_end_with_human_in_the_loop` |
| G4 | How is quality measured? | golden set with 17 questions (5 unanswerable), summaries, prior-auth, coding, safety baits; clinician grading and inter-rater agreement | `ai/evalkit/README.md` |
| G5 | Can clinicians see why? | every statement links to sources; flags show their rule | `ai/dashboard/src/Overview.jsx` |
| G6 | Prompt injection? | no tools or network for the model, grounded and schema-validated outputs, identifiers not in prompts, egress lockdown | `docs/compliance/threat-model.md` L3 |
| G7 | Which model, hosted where, with what retention? | gpt-oss-20b via the customer-approved endpoint (Foundry private endpoint in production); retention terms pending the BAA | `docs/compliance/vendor-register.md` rows 2-3 |
