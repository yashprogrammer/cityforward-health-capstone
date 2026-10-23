# Third-party register and assessment procedure

Rule of thumb: **no real PHI goes to any party in this list unless its row says a BAA (or the covered-entity relationship) is in place.** Today the project runs on synthetic data only; rows marked *blocks PHI* must be closed before that changes.

## Register

| # | Party | Service | Data it sees | PHI? | BAA / basis | Tier | Evidence to collect | Status / open items | Review |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Microsoft Azure | AKS, PostgreSQL flexible server, Key Vault, ACR, Azure Files, Monitor, Defender | everything the system stores | yes | Microsoft's HIPAA BAA is part of the online services terms for covered services (confirm in the customer's agreement and that each service is in scope) | 1 | SOC 2 Type II, ISO 27001, HITRUST CSF certification from the Service Trust Portal; region and data-residency commitments | open: confirm service scope; customer-managed keys decision | annual |
| 2 | Azure AI Foundry (model hosting) | gpt-oss-20b inference for the Copilot | de-identified prompts (placeholders for identifiers) | residual | covered by the Microsoft BAA if the service is in scope (verify); zero-retention / no-training terms required | 1 | data-handling terms, abuse-monitoring opt-out for PHI workloads, region | open (blocks PHI until verified) | annual |
| 3 | Groq | OpenAI-compatible inference for gpt-oss-20b (demo and local development) | de-identified prompts | residual | **none** | 1 | would need a BAA and retention terms | **blocks PHI**: synthetic data only | before any real data |
| 4 | Payer (fictional "Summit Health Plan" family) | prior authorization API | patient demographics, diagnoses, supporting evidence | yes | disclosure for payment between covered entities (45 CFR 164.506), not a BA relationship; minimum necessary applies to what we send | 1 | payer's API security terms, mTLS/OAuth profile (the mock uses an API key) | production payers use OAuth2/mTLS: replace `X-Api-Key` | at onboarding |
| 5 | GitHub | source control, CI, OIDC identity for signing | code and config; no PHI by policy (Synthea data is synthetic) | no | n/a | 2 | SOC 2 report, secret-scanning enabled | gitleaks in CI; branch protection to enable | annual |
| 6 | Sigstore (Fulcio, Rekor) | keyless signing and transparency log | signing identity and image digests (public) | no | n/a | 3 | public good service terms | note: the transparency log is public by design | annual |
| 7 | Container registries (Docker Hub, Quay, GHCR) | base images and third-party images | nothing | no | n/a | 2 | images pinned by version, scanned (Trivy), registry allow-list at admission | digests to pin in production values | continuous |
| 8 | Hugging Face | embedding model download at image build (optional `FASTEMBED=1`) | nothing | no | n/a | 3 | pin the model revision; verify checksum; bake into the image so runtime has no external dependency | revision pin to add | per model change |
| 9 | Identity provider (customer's Entra ID/Okta, or the demo Keycloak) | workforce authentication | staff identities (workforce data, not PHI) | no | customer's | 1 | MFA enforced, session policy, role mapping contract | customer's responsibility (G7) | customer's |
| 10 | NLM UMLS / SNOMED International | SNOMED CT licence; CMS ICD-10-CM code set | nothing | no | licence terms | 3 | licence acceptance on file; SNOMED not redistributed (only a demonstration subset of mappings is bundled) | production needs the licensed map | annual |
| 11 | AMA | CPT | not used | no | n/a | n/a | HCPCS Level II and SNOMED CT used instead | n/a | n/a |
| 12 | Synthea (Apache-2.0) | synthetic patient generator | none (generates fictional people) | no | licence | 3 | records of generation parameters (`customer/synthea/README.md`) | none | n/a |
| 13 | Python and npm ecosystems | libraries | nothing | no | licences | 2 | `pip-audit`, SBOM per image, CodeQL, Dependabot | continuous | continuous |
| 14 | In-cluster OSS (Argo CD, Linkerd, Kyverno, Falco, Prometheus, Grafana, Jaeger, OTel, ingress-nginx, Keycloak) | platform | telemetry and access metadata (PHI-scrubbed) | no | licences | 2 | images signed or pinned, charts pinned (open: G9), CVE scanning | pin versions after first sync | continuous |

Tiers: **1** touches PHI or can disclose it; **2** is in the build or run path; **3** is informational or build-time only.

## Assessment procedure

| Step | Tier 1 | Tier 2 | Tier 3 |
|---|---|---|---|
| Before use | security questionnaire (SIG-lite or equivalent), independent report (SOC 2 Type II / ISO 27001 / HITRUST), BAA or documented HIPAA basis, data-flow review, subprocessor list | public security documentation, vulnerability-handling policy, licence check | licence check |
| Contract terms | breach notification within 24-72 hours, subprocessor flow-down, retention and deletion, no training on our data, audit rights, termination assistance | standard terms | n/a |
| Technical gate | minimization and de-identification verified by test; egress allow-listed to the vendor's endpoint only (network policy / firewall) | pinned versions, signature or checksum verification | n/a |
| Ongoing | annual re-assessment, review of the vendor's incident history, SOC report exceptions read, re-run golden evaluation when a model version changes | automated CVE scanning, review on major upgrades | review on change |
| Offboarding | revoke credentials, confirm deletion in writing, remove egress rules and secrets, update this register | remove dependency | n/a |

**Model-specific questions** (added because the model provider is the unusual vendor): Are prompts and outputs retained, for how long, and can retention be set to zero? Are they used for training or evaluation? Who at the provider can read them? Which regions process them? How are model versions changed, and with what notice (a silent model update is a clinical-quality change: we re-run the golden set on any version change)? What happens during an outage, and does the provider publish incident post-mortems?
