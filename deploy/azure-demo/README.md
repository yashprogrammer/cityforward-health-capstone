# CityForward Azure development checkpoint

## Agreed scope — 2026-10-26

- Work on this repository's `integrated` branch. CityCare provides lessons only.
- Development first: local kind validation, then a short Azure validation and verified teardown.
- Use Microsoft Foundry. Do not use Groq or either existing `gpt-4.1-mini` deployment without a new selection.
- Subscription: `Databricks_live` (selected by the owner). The model, region, credit/spending-limit status and budget-alert email are pending.
- Proposed Azure validation budget: **US$5**. This is not authorization to spend. Obtain approval with a concrete estimate before paid calls, cloud creation, start, apply or destroy. A Cost Management budget sends alerts; it is not a hard spending cap.
- Course outlining and recording happen after development is finalized; no recording or rehearsal date is scheduled.
- Only synthetic data. Keep de-identification, grounding, CDS lint, RLS, human sign-off and audit controls intact.

This directory is a development checkpoint, **not a working Azure deployment wrapper**. `demo.sh`, the demo Terraform profile and the Azure deployment have not been built or run yet. Hospital defaults remain unchanged.

## P0 evidence

| Check | Observed result |
|---|---|
| Git | `integrated`; existing local commit `1ce0d6c` preserved; no push |
| Azure CLI | 2.78.0; login works; selected subscription enabled |
| Terraform | 1.17.0; satisfies the repository's `>= 1.6.0` requirement |
| kubectl / Helm / kind | v1.32.2 / v4.3.1 / v0.33.0 |
| Docker | Server 28.0.4; 10 CPUs; about 7.7 GiB allocated |
| Python / Node / jq | 3.13.2 / v22.23.4 / jq-1.7.1 |
| Required resource providers | ContainerService **not registered**; DBforPostgreSQL, KeyVault, ContainerRegistry, OperationalInsights and Storage registered |
| Existing Foundry discovery | Two AIServices accounts in North Central US, each with a succeeded GlobalStandard `gpt-4.1-mini` deployment; neither invoked |
| Local stacks | CityCare and CityForward Compose containers already running; left untouched |
| Ports | 5432, 8001, 8003 occupied; 8081 free; no kind cluster exists |
| Deployment tests | Initial host environment: 25 passed, 4 skipped. Isolated validator environment plus collector image: **29 passed, zero skipped** |

Test command actually run:

```bash
/tmp/cityforward-deploy-checks/bin/python -m pytest deploy/tests -q
```

Temporary setup outside the repository: `/tmp/cityforward-deploy-checks` contains pytest, PyYAML, kubernetes-validate, python-hcl2, httpx, prometheus-client, opentelemetry-api, cryptography and PyJWT. The pinned `otel/opentelemetry-collector-contrib:0.116.1` image was pulled. The collector test ran a temporary container, verified synthetic identifier scrubbing and removed its container. The validator environment is disposable.

### Rendered port audit

Both kind and AKS profiles were rendered in memory with `helm template`. Secret documents were not printed or saved. Application listener ports below were checked against `ai/Dockerfile`, `customer/scripts/serve.py` and the chart; these are configuration checks, not a live kind test.

| Service | Service port | targetPort → container port | Probe | Ingress backend |
|---|---:|---|---|---|
| copilot | 8080 | http → 8080 | startup/readiness/liveness: `/healthz`, http → 8080 | `/ai(/\|$)(.*)` → copilot:http |
| copilot-metrics | 9100 | metrics → 9100 | None | None |
| meditrack | 8001 | http → 8001 | readiness/liveness: `/int/healthz`, http → 8001 | `/` → meditrack:http |
| payer | 8003 | http → 8003 | readiness/liveness: `/healthz`, http → 8003 | None |
| postgres (kind only) | 5432 | implicit 5432 → 5432 | `pg_isready -U postgres` | None |

The supplied deployment prompt's AI port of 8003 is incorrect for this checkout: **AI uses 8080; payer uses 8003**. Seed and Key Vault sync jobs have no container/probe/Service ports. No `/readyz` handler currently exists; the AI `/healthz` performs a database query.

## Findings to resolve in P1

1. **Gateway lifecycle:** the kind bootstrap installs ingress-nginx v1.11.3. Kubernetes retired ingress-nginx in March 2026. Choose and validate a maintained same-origin gateway before treating this as a current course deployment. Source: [Kubernetes retirement notice](https://kubernetes.io/blog/2025/11/11/ingress-nginx-retirement/).
2. **Foundry bootstrap:** the application and Helm chart support Foundry, but `deploy/kind/bootstrap.sh` only wires Groq. Add an explicit Foundry configuration path after the model/region decision. Credentials must be passed in memory to Kubernetes, not command arguments, files, logs, Terraform state or Helm values.
3. **Cost controls:** the chart does not pass `COPILOT_DAILY_BUDGET_USD`; the app defaults that setting to zero (disabled). Its model prices are explicitly illustrative. Wire a positive development cap and verify actual provider rates before paid tests. This application check is not a subscription-wide hard cap.
4. **Bootstrap reliability:** commands rely on the current Kubernetes context and `helm test` failure is ignored with `|| true`. Target the intended cluster explicitly and propagate failures before running the script against a machine with other clusters.
5. **Cluster acceptance tooling:** `ai/e2e/smoke.py` hardcodes localhost:8001. `incident.drill` uses an in-process test harness and a spy model, not the deployed cluster's Prometheus. Running these unchanged would not establish the requested kind acceptance criteria. Keep the incident logic intact and provide explicit deployment connections and observable alert evidence.

The kind profile uses MediTrack's own SMART authorization server with empty Keycloak settings. Keycloak is not required for this local profile. NetworkPolicy is disabled there because kindnet does not enforce it; that is not evidence of enforced isolation. The AKS security defaults remain intact.

## Not run / next approval

- **Not run:** kind bootstrap, kind dashboard flow, in-cluster OTel/Prometheus, live alert firing/clearing, Foundry inference, Terraform init/validate/plan/apply, cloud rehearsal or teardown.
- No cloud resources were created, changed or deleted; no billable model calls were made. Added Azure spend from this work: **US$0**. Existing subscription spend and burn rate were not queried and are not claimed to be zero.
- Next input: Foundry model and preferred region; whether the subscription has credits or a spending limit. Then prepare the configuration and a bounded test-cost estimate for approval.
- For later AKS planning: admin identity, authorized public IP, owner/delete-after tags and budget-alert email remain required. Provider registration and any paid operation require the owner's approval.
- Before any cloud apply, implement and review the complete down path and verify the estimate fits the agreed budget. Do not deploy the unchanged hospital defaults as a cheap demo.

Repository changes at this checkpoint: this report and ignore rules for real tfvars, saved plans/state variants and kubeconfigs. No application, chart, Terraform or bootstrap source behavior changed.
