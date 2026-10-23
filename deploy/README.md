# Deploying CityForward into a hospital-style cluster

```
                         Internet ─x─ (nothing public)                         Hospital network / VPN
                                                                                      │
                                         ┌────────────────────────────────────────────▼───────────────────────────────────────┐
                                         │ VNet 10.20.0.0/16                                                                  │
   Entra ID / Keycloak  ──OIDC──┐        │  snet-aks (private cluster, Cilium network policy, Linkerd mTLS)                    │
                                │        │   ┌─────────────┐   ┌─────────────┐   ┌──────────┐   ┌─────────────────────────┐  │
                                └───────►│   │ ingress-    │──►│ MediTrack   │   │ payer    │   │ Copilot (3+, HPA)       │  │
                                         │   │ nginx (TLS) │──►│ EHR + FHIR  │◄──│ (mock)   │◄──│ RAG · prior auth · cds  │  │
                                         │   └─────────────┘   │ + SMART     │   └──────────┘   │ audit · redaction · SMART│  │
                                         │                     └──────┬──────┘                  └─┬───────┬──────┬────────┘  │
                                         │  snet-data (delegated)     │                           │       │      │ egress     │
                                         │   PostgreSQL flexible server (pgvector, TLS, private DNS, zone-redundant HA) ◄─────┘       │      ▼ to the model
                                         │  snet-private-endpoints: Key Vault (CSI + workload identity) · ACR · Azure Files │   endpoint (private)
                                         │  observability: OTel collector (PHI scrub) → Jaeger · Prometheus · Grafana · Falco · Kyverno
                                         └───────────────────────────────────────────────────────────────────────────────────┘
```

"On-prem-style" means the same shape without the public cloud conveniences: the API server is private, there is no public ingress, data stores have private endpoints only, the model is reached over a private
link or runs inside the VNet, and images/secrets come from internal registries and a vault. The Helm chart does not care whether the cluster is AKS, OpenShift or an on-prem Kubernetes.

## What is where

| Path | What | How it was checked |
|---|---|---|
| `helm/cityforward` | the application chart: EHR, payer, Copilot, dev Postgres, seed job, ingress, NetworkPolicies, HPA/PDB, Key Vault sync | `helm lint`, rendered for kind and AKS values, **every manifest validated against Kubernetes 1.31 schemas**, 19 assertions on security posture (`tests/test_chart.py`) |
| `platform/otel` | OTel collector with PHI scrubbing (attribute drop, path templating, regex redaction) | **ran the real collector image** and proved PHI does not survive it (`tests/test_otel_collector.py`) |
| `platform/kyverno` | admission policies: non-root, limits, no `:latest`, registry allow-list, signed images + SBOM | YAML structure and cross-file consistency (`tests/test_platform.py`). **Not executed against a Kyverno instance** |
| `platform/falco` | runtime rules: shell in container, private-key read, unexpected egress, writes under `/app` | structure, and the egress ports are checked against the NetworkPolicy |
| `platform/monitoring` | Prometheus alerts (audit failing, redaction stopped, fallback rate, latency, spend, dead letters) | **every metric in every alert exists in `copilot/telemetry.py`** (test) |
| `platform/keycloak` | demo Keycloak + the realm in `customer/platform/keycloak` | structure only |
| `argocd` | AppProject, app-of-apps root, 12 Applications with sync waves (CRDs → mesh → policy → apps) | structure, ordering, repo/namespace allow-lists. Chart versions are **ranges and unverified** against the live registries: pin them after the first sync |
| `terraform/azure` | landing zone: VNet, private AKS (Cilium, Entra RBAC, workload identity, Defender), PostgreSQL flexible server + pgvector, Key Vault (premium, purge-protected), ACR, Azure Files, Log Analytics | `terraform fmt -check`, HCL parsed, 10 security properties asserted. **Not `validate`d or applied**: the provider registry is unreachable from the authoring sandbox |
| `kind/` | `bootstrap.sh` (cluster, ingress, images, secrets, chart, helm test), Linkerd certificate script | `bash -n` only. **Not run**: nested containers are blocked in the authoring sandbox (kubelet cannot create pod sandboxes) |

Run the checks: `pip install pytest pyyaml kubernetes-validate python-hcl2 && pytest deploy/tests` (needs `helm` or Docker for the chart tests; the collector test needs `docker pull otel/opentelemetry-collector-contrib:0.116.1`).

## Laptop (kind)

```bash
export GROQ_API_KEY=...           # optional: without it the AI runs in its labelled no-model mode
deploy/kind/bootstrap.sh          # http://localhost:8081  (MediTrack), /ai/healthz
deploy/kind/bootstrap.sh down
```
kindnet does not enforce NetworkPolicy: for a laptop cluster that does, create the cluster with Calico (`disableDefaultCNI: true` + the Calico manifest) and set `networkPolicy.enabled=true`.

## Azure

1. `cd deploy/terraform/azure && cp terraform.tfvars.example terraform.tfvars` (set your admin group) → `terraform init && terraform apply`.
2. From inside the VNet: run `scripts/db-roles.sql`; put the model key, SMART signing key, webhook secret and payer key in Key Vault (names are the `keyVault.objects` in the chart).
3. Build and push images from CI (the release workflow signs them with cosign and attaches an SBOM; Kyverno refuses unsigned images). The registry is private: the pipeline needs a self-hosted runner in the VNet or an ACR task.
4. `az aks get-credentials ...; kubectl apply -f deploy/argocd/project.yaml -f deploy/argocd/root-app.yaml`. Argo CD installs the platform in order and then the application chart with `values-aks.yaml`.
5. Linkerd needs a trust anchor: `deploy/kind/linkerd-certs.sh` for a demo, cert-manager with the hospital's CA for production.

## Things deliberately left for the real engagement

* the hospital's own ingress/WAF and DNS, and their identity provider (Keycloak here is a stand-in with the same role and claim contract)
* egress control by FQDN (Azure Firewall rules) instead of CIDRs, and a private endpoint to the model service
* backup/restore drills and the retention policy for logs (six years for HIPAA documentation; archive to immutable storage)
* penetration test, and the hospital's own security review (see `docs/compliance`)
