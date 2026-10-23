# HIPAA Security Rule and HITRUST control areas: where each is met

Status words: **Implemented** (in code/config and tested), **Partial** (some of it, gap named), **Customer** (the hospital's responsibility in this deployment model), **Gap** (not done; listed in section 4).
HITRUST references name the CSF control (IDs from the CSF v11 naming as the author recalls them: verify against your licensed copy before quoting them to an assessor).

## 1. The five questions every hospital asks

| Question | Answer | Evidence |
|---|---|---|
| **Are patient records encrypted?** | In transit: TLS at the ingress (HSTS), mTLS between pods (Linkerd), `sslmode=require` to PostgreSQL. At rest: PostgreSQL flexible server, Azure Files and Key Vault are encrypted by the platform (Microsoft-managed keys by default, customer-managed keys supported through `customer_managed_key_id`). Secrets never sit in git or images: Key Vault, synced by the CSI driver. The AI service stores chart text (to search it) in its own database: same controls. Prompts to the model are de-identified first | `deploy/helm/cityforward/templates/ingress.yaml`, `deploy/helm/cityforward/templates/keyvault.yaml`, `deploy/terraform/azure/main.tf`, `ai/copilot/redact.py`, `ai/tests/test_redact.py` |
| **Can unauthorized staff access them?** | Three layers: role matrix (who may do what), signed SMART token scope (one patient, one unit), and Postgres row-level security that hides other units' rows and all charts from IT/compliance roles, failing closed. Break-the-glass is time-boxed, reasoned, audited and reviewed. Cluster side: private API server, Entra RBAC, network policy default-deny, no service-account tokens | `ai/copilot/access.py`, `ai/copilot/schema_rls.sql`, `ai/copilot/breakglass.py`, `ai/tests/test_rbac.py`, `deploy/helm/cityforward/templates/networkpolicy.yaml` |
| **Are access and changes recorded?** | Hash-chained, append-only audit log of every allowed/denied access, AI call (purpose, model, token counts, redactions; never text), approval, attestation, submission and coding decision, with a chain verifier. Platform side: Kubernetes audit through Azure Monitor, Key Vault and PostgreSQL diagnostic logs, Falco runtime events, Argo CD/git history as the change record, signed images as the build record | `ai/copilot/audit.py`, `ai/tests/test_audit.py`, `deploy/terraform/azure/main.tf` (diagnostic settings), `deploy/platform/falco/values.yaml`, `.github/workflows/release.yml` |
| **Is there an incident-response plan?** | Yes, with playbooks for the incidents this system can actually have (PHI sent to the model provider, audit chain broken, credential compromise, prompt-injection exfiltration, wrong AI output reaching a clinician) and the 60-day breach clock. Alerts exist for the detectable ones | [incident-response-plan.md](incident-response-plan.md), `deploy/platform/monitoring/prometheus-rules.yaml` |
| **Are third-party vendors assessed?** | Register with data shared, BAA status, risk tier and review date for every external party, including the model provider and open-source supply chain; image signing and SBOMs for what we ship | [vendor-register.md](vendor-register.md), `.github/workflows/ci.yml`, `.github/workflows/release.yml` |

## 2. HIPAA Security Rule (45 CFR 164.3xx)

| Safeguard | Citation | Status | How / where |
|---|---|---|---|
| Risk analysis and risk management | 164.308(a)(1)(ii)(A)-(B) | Partial | [threat-model.md](threat-model.md) is the engineering risk analysis; the organization-wide analysis is the covered entity's. Residual risks listed in section 4 |
| Information access management; workforce clearance | 164.308(a)(3)-(4) | Implemented | role matrix and unit scope: `ai/copilot/access.py`; roles come from the hospital IdP (`customer/platform/keycloak/cityforward-realm.json`) |
| Security awareness and training | 164.308(a)(5) | Customer | training content and records belong to the workforce's employer; the "About this tool" panel carries the intended-use text to every clinician |
| Security incident procedures | 164.308(a)(6) | Implemented | [incident-response-plan.md](incident-response-plan.md) |
| Contingency plan (backup, DR, emergency mode) | 164.308(a)(7) | Partial | PostgreSQL zone-redundant HA, 35-day backups, geo-redundant backup (`deploy/terraform/azure/main.tf`); every AI feature degrades to a labelled no-AI fallback so clinical work continues (`ai/tests/test_rag.py::test_ask_model_outage_degrades_to_passages`). No restore drill executed: Gap G5 |
| Evaluation (periodic technical review) | 164.308(a)(8) | Implemented | CI security scans (`.github/workflows/ci.yml`) and the golden evaluation gates (`ai/evalkit/golden/gates.json`) |
| Business associate contracts | 164.308(b), 164.314(a) | Customer + vendor | [vendor-register.md](vendor-register.md): no BAA exists for the model provider, so real PHI must not flow until one does (Gap G1) |
| Facility access, workstation, device and media controls | 164.310 | Customer | cloud datacenter controls via the provider's attestations; the vendor has no on-premises PHI |
| Unique user identification | 164.312(a)(2)(i) | Implemented | every request is a named principal from the SMART/OIDC token (`ai/copilot/auth.py`) |
| Emergency access procedure | 164.312(a)(2)(ii) | Implemented | break-the-glass: `ai/copilot/breakglass.py`, `ai/tests/test_rbac.py::test_break_glass_flow` |
| Automatic logoff | 164.312(a)(2)(iii) | Partial | access tokens last 60 minutes; the dashboard keeps the token in memory only (a reload ends the session); idle timeout is the IdP's (`ssoSessionIdleTimeout` 900 s in the Keycloak realm) |
| Encryption and decryption | 164.312(a)(2)(iv) | Implemented | see question 1 |
| Audit controls | 164.312(b) | Implemented | `ai/copilot/audit.py` |
| Integrity (alteration/destruction of ePHI) | 164.312(c) | Implemented | the AI layer is read-only on the chart (it writes only new, labelled PDFs through FHIR); audit chain detects tampering; signed images; Kyverno blocks unsigned images |
| Person or entity authentication | 164.312(d) | Implemented | SMART EHR launch with PKCE for users, `private_key_jwt` for services, HMAC + replay window for webhooks (`ai/tests/test_webhooks.py`) |
| Transmission security | 164.312(e) | Implemented | TLS, mTLS, signed webhooks, `Strict-Transport-Security` |
| Policies and documentation (6 years) | 164.316 | Partial | this pack lives in git (history is the revision record); log retention 365 days online, archive for six years: Gap G6 |

## 3. HIPAA Privacy Rule items the engineering touches

| Item | Citation | Status | How |
|---|---|---|---|
| Minimum necessary | 164.502(b) | Implemented | patient-scoped retrieval, unit scope, IDs-only events, de-identified prompts, `llm_call` rows store no text |
| De-identification (Safe Harbor) before the model | 164.514(b)(2) | Partial | exact-match removal of the patient's own identifiers, patterns for the rest, reversible date offsets, ages over 89 generalized. Free-text can still contain a relative's name or an unusual identifier, so a BAA with the model provider is still required: Gap G2. Leakage test over all 22 patients: `ai/tests/test_redact.py` |
| Accounting of disclosures / access reports | 164.528 | Implemented | `GET /api/audit?patient=` |
| Breach notification | 164.404-410 | Implemented (process) | [incident-response-plan.md](incident-response-plan.md) section 7 |

## 4. HITRUST CSF control areas

| Category | Status | Where / note |
|---|---|---|
| 0 Information Security Management Program | Partial | this pack, CI evidence; no ISMS scope document |
| 01 Access Control (01.b registration, 01.c privilege management, 01.q authentication, 01.v information access restriction) | Implemented | `ai/copilot/access.py`, row-level security, IdP realm, break-the-glass |
| 02 Human Resources Security | Customer | |
| 03 Risk Management | Partial | [threat-model.md](threat-model.md) |
| 04 Security Policy | Partial | policies are described, not formally approved |
| 05 Organization of Information Security (05.i risks of external parties, 05.k third-party agreements) | Partial | [vendor-register.md](vendor-register.md) |
| 06 Compliance (06.d data protection and privacy of covered information) | Partial | FDA memo, HIPAA mapping |
| 07 Asset Management | Implemented | SBOM per image, pinned images, registry allow-list (Kyverno) |
| 08 Physical and Environmental Security | Customer | cloud provider attestations |
| 09 Communications and Operations (09.aa audit logging, 09.ab monitoring system use, 09.ad administrator logs) | Implemented | audit log, Prometheus alerts, Falco, OTel (PHI-scrubbed) |
| 10 Systems Acquisition, Development, Maintenance (10.f cryptographic controls, 10.k change control) | Implemented | git + pull request + CI gates; golden evaluation re-run on any model/prompt/retrieval change; image signing |
| 11 Incident Management (11.a reporting events, 11.c responsibilities and procedures) | Implemented | [incident-response-plan.md](incident-response-plan.md) |
| 12 Business Continuity | Partial | HA and fallbacks yes; restore drill: Gap G5 |
| 13 Privacy Practices | Partial | notice and consent are the covered entity's |

## 5. Gaps (what a reviewer will find, stated first)

| # | Gap | Why it matters | Plan |
|---|---|---|---|
| G1 | No signed BAA with the model provider (Groq / Foundry) | HIPAA requires one before PHI is disclosed to a business associate | use Azure AI Foundry under the Microsoft BAA or a model inside the VNet; until then synthetic data only |
| G2 | De-identification is best-effort on free text | residual re-identification risk | BAA (G1), plus expert determination if the data is ever to leave the BAA boundary |
| G3 | No penetration test, no formal threat-model review with the customer | assurance | schedule before go-live |
| G4 | Not independently assessed (no HITRUST/SOC 2) | many hospitals require one | SOC 2 Type I first, HITRUST e1/i1 next |
| G5 | No restore/DR drill executed | untested backups are not backups | quarterly drill, record RTO/RPO |
| G6 | Log retention is 365 days online | HIPAA documentation retention is six years | archive audit exports to immutable storage |
| G7 | MFA depends on the customer's IdP | access control strength | require it in the IdP contract |
| G8 | Kyverno, Falco, Argo apps, Terraform and the kind bootstrap were written and statically checked but not executed against real systems | correctness of operational config | first deployment is the real test; see `deploy/README.md` |
| G9 | Chart versions in Argo apps are ranges, not pins | supply chain | pin after first sync |
