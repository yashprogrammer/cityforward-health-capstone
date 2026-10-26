# CityForward Azure development checkpoint

## Agreed scope — 2026-10-26

- Work on this repository's `integrated` branch. CityCare provides lessons only.
- Development first: local kind validation, then a short Azure validation and verified teardown.
- Use Microsoft Foundry. Do not use Groq or either existing `gpt-4.1-mini` deployment without a new selection.
- Subscription: `Databricks_live` (selected by the owner). ARM reports `PayAsYouGo_2014-09-01` and `spendingLimit=Off`; remaining promotional credits are unverified. Budget-alert email is pending.
- Model/region selected after the owner delegated the choice: **Foundry `gpt-oss-120b`, version `1`, Global Standard, East US 2**. This preserves the course's open-weight model approach and the existing Terraform region. It is a development baseline, not a measured quality winner. Validate it against the capstone's safety and grounding cases before finalizing the course.
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
- Next work: prepare the selected Foundry configuration and enforceable test limits, then request approval for a bounded paid validation. The model/region choice is settled; it does not authorize resource creation or inference spending.
- For later AKS planning: admin identity, authorized public IP, owner/delete-after tags and budget-alert email remain required. Provider registration and any paid operation require the owner's approval.
- Before any cloud apply, implement and review the complete down path and verify the estimate fits the agreed budget. Do not deploy the unchanged hospital defaults as a cheap demo.

Repository changes at this checkpoint: this report and ignore rules for real tfvars, saved plans/state variants and kubeconfigs. No application, chart, Terraform or bootstrap source behavior changed.

## Foundry selection evidence — 2026-10-26

- Read-only ARM model inventory lists `gpt-oss-120b` version `1` with GlobalStandard in East US 2 and South India. It also lists `gpt-oss-20b` now; the older CityCare deployment limitation must not be treated as current availability evidence.
- `az cognitiveservices usage list --location eastus2`: `AIServices.GlobalStandard.gpt-oss-120b` has current usage 50 and limit 5000 in the API's quota units. This confirms quota headroom, not successful deployment or guaranteed live capacity.
- [Azure Retail Prices API](https://prices.azure.com/api/retail/prices?$filter=productName%20eq%20%27Azure%20OpenAI%20OSS%20Models%27%20and%20armRegionName%20eq%20%27eastus2%27): Global Standard input meter `gpt-oss-120B Inp glbl Tokens` is US$0.00015/1K tokens; output meter `gpt-oss-120B Outp glbl Tokens` is US$0.0006/1K tokens. That is **US$0.15 input / US$0.60 output per million tokens**, excluding tax. This is inference pricing, not the unrelated hourly fine-tuning meters.
- Illustrative token envelope: 100 calls with 5,000 billable input and 1,000 billable output tokens each cost **US$0.135**. Actual usage, including billed reasoning tokens, must be measured; this is not an observed capstone run or an enforced limit. Reserve at most US$0.50 of the overall US$5 for initial model validation, subject to explicit approval and implemented limits.
- Global Standard is pay-per-token, with no provisioned GPU/throughput commitment selected. [Global processing may happen outside the resource region](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure-region-availability); East US 2 does not imply US-only inference. Synthetic data and the de-identification gateway remain required.
- No deployment was created and no inference call was made for this selection. Existing Foundry accounts/deployments remain untouched.

## Local kind validation — 2026-10-26

Run with `CF_LOCAL_DEMO=1 deploy/kind/bootstrap.sh`, then `deploy/azure-demo/platform.sh` (OTel Collector + kube-prometheus-stack).

| Check | Observed |
|---|---|
| Gateway | Caddy (rootless, `setcap -r`, port 8080) replaces retired ingress-nginx; MediTrack `/` and `/ai/` answer on `localhost:8081`; pods run with drop-ALL |
| Platform | Collector Running (Jaeger exporter neutralised in `otel-local.yaml`); Prometheus scrapes `copilot-metrics` (up); 3 rule groups loaded |
| Foundry | `gpt-oss-120b` called from the cluster via a Secret applied over stdin; 5 calls, about US$0.007 by the app's own price table (not Azure billing; Cost Management lags) |
| Ask the chart | `mode=llm`, grounded, correct ("twice daily") |
| Summary | Fell back to the template **twice**: model wrote "day 0", a number absent from the cited evidence. Grounding behaved as designed; this is a model-quality finding to weigh, not a bug to bypass |
| Deployment tests | 30 passed (`deploy/tests`) |

Not run: alert firing/clearing, Coding and Prior authorization with the live model, `ai/tests` (deps missing on host), any Terraform or AKS step.

## Alert drill on kind — 2026-10-26

`python deploy/azure-demo/alert_drill.py --kube-context kind-cityforward --kubeconfig .data/kubeconfig-kind --namespace cityforward --base-url http://localhost:8081 --prometheus-port 19090 denials down`

Every connection is an explicit argument; the drill refuses non-`kind-` contexts and a Prometheus that already has active alerts. Real signals only, no safety control weakened, no model called.

| Alert | Signal | Fired | Cleared |
|---|---|---|---|
| `UnusualAccessDenials` | 40 requests as `it.admin` (IT_ADMIN), each refused 403 | 20 s after the traffic (41.3 denials in the 10 min window) | 601 s after firing (window slid past) |
| `CopilotDown` | copilot scaled to 0 | 321 s after the outage (rule `for: 5m`) | 40 s after scale-up |

Finding: a counter series that first appears already at N has no earlier sample, so `increase()` reports 0. The first denials for a new label pair are invisible to `UnusualAccessDenials`; the first run (no priming) never fired. The drill now sends one request, waits a scrape, then the burst. Alertmanager is disabled in this profile: Prometheus state only, no notification delivered. `RedactionCanaryFailed` / `RedactionStoppedWorking` are not drilled live (they need the gateway running without its redactor); `incident.drill` covers that logic in-process.

## Azure demo profile — prepared, NOT applied

`deploy/azure-demo/demo.sh plan | providers | up | smoke | stop | start | down | status`. Anything that changes cloud resources needs `CF_APPROVED=yes` on that command.

| Piece | File |
|---|---|
| Cost/posture overrides (hospital defaults unchanged; new variables default to the old behaviour) | `demo.tfvars`, `deploy/terraform/azure/variables.tf` |
| Chart overlay, same-origin gateway (Caddy, public only to your /32) | `deploy/helm/cityforward/values-azure-demo.yaml`, `azure-gateway.yaml` |
| Key Vault population, DB roles from inside the cluster, smoke test | `azure_secrets.py`, `azure_db_roles.py` (+ idempotent `db-roles.sql`), `smoke.py` |

Verified: `terraform validate` passes; `plan` = **37 to add, 0 to change, 0 to destroy** (read-only, nothing created); 32 deployment tests pass; `smoke.py` 8/8 against the kind stack. **Not run:** apply, image builds in ACR, Key Vault CSI sync, DB role job, `helm upgrade` on AKS, `stop/start`, `down`, `kubelogin` flow.

Environment findings that shaped the profile: this subscription has **no quota or access to the v5/v6/B-v2 VM families** in eastus2 (D2s_v5 is `NotAvailableForSubscription`); `Standard_D2as_v7` is available with 10 vCPU quota and lists at 0.0908 USD/h. `Microsoft.ContainerService` and `Microsoft.PolicyInsights` are not registered. `kubelogin` is not installed (cluster local accounts are disabled).

Defects found in the hospital-default Terraform: Key Vault and ACR have public access off but **no private endpoints are defined**, so the cluster could not reach them; the chart's `llmEgressCidrs` comment points at the data subnet, not a model endpoint. The demo profile sidesteps the first by enabling public access with RBAC only; the production story still needs the private endpoints.

Estimated burn while running (nodes verified from the retail price API, the rest estimated): about 0.26 USD/h, roughly 1 USD for a 4-hour session.
