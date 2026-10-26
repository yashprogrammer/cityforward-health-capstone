# Prompt for the local agent: Azure demo deployment (rehearse, record, tear down)

Paste everything inside the block into the Claude Code session that already knows the CityCare / Azure webinar project. It is written so that agent can reuse what it learned there. If it is a fresh session, it still works: it only needs the repo and the CityCare lessons in section 3.

```text
You are going to take the CityForward Health capstone and run it on Azure as a short-lived, cost-controlled demo: rehearse once, record a 20-30 minute demo, then destroy everything. You already know the older CityCare project (Bicep + azd + Container Apps + Caddy gateway + Foundry model). This is a DIFFERENT project with a DIFFERENT stack (Terraform + AKS + Helm). Reuse the lessons, not the files.

## 0. Confirm scope with me first (do not skip)
"Migration" can mean two things. I mean (A): deploy the CityForward capstone (branch `integrated` of https://github.com/yashprogrammer/cityforward-health-capstone, private, use my git credentials) to Azure as a demo and then delete it. I do NOT mean (B): rewrite the CityCare project onto Kubernetes. Before doing anything, restate (A) in three lines and ask me to confirm. Also ask me for:
  - Azure subscription (id, name), whether it has credits or a spending limit, and the region I prefer (default eastus2 in the Terraform; CityCare used Central/South India, so offer to switch if quota or latency is a problem)
  - my public IP (to restrict the Kubernetes API), my Entra group/user object id for cluster admin
  - whether I will record on this machine (recording day) and the target date, so you can plan the rehearsal one day before
  - whether the model is Groq (cheapest, already works, my key is in the env var GROQ_API_KEY) or Azure AI Foundry (CityCare used gpt-oss-120b in South India because 20b was not deployable there). Default: Groq called from the cluster, because the point of the demo is Kubernetes + OpenTelemetry + Prometheus, not the model.

## 1. Why this demo exists (so you make good trade-offs)
It is the closing section of a Forward Deployed Engineer course. The audience watches an engineer ship an AI layer into a hospital-style environment and prove it is safe and observable. The three cloud-native projects on screen are:
  1. Kubernetes + Helm  - chart `deploy/helm/cityforward`, values `values-aks.yaml` (a security-posture test already guards it)
  2. OpenTelemetry Collector - `deploy/platform/otel/collector-values.yaml` (strips PHI from telemetry; tested with the real contrib image)
  3. Prometheus - `deploy/platform/monitoring/prometheus-rules.yaml` + the service's `/metrics` (copilot/telemetry.py)
Argo CD and Kyverno are optional 2-minute mentions only. Do NOT install Linkerd, Falco, Jaeger, Keycloak or Argo CD for this demo unless I ask. Less running = less cost and fewer ways to fail on camera. (Keycloak is only needed if the chart's auth config requires it; check `values.yaml`/`values-kind.yaml` and tell me what the kind profile does for the identity provider, then mirror that.)

## 2. Hard rules
1. SECRETS: the Groq key lives only in the env var GROQ_API_KEY. Never write it to a file, log, tfvars, commit, screenshot, shell history or command output; never put it in Terraform state if avoidable (create the Kubernetes Secret from the env var with kubectl, not from a tf variable). Never commit `.env`, `*.tfvars` with real ids, kubeconfigs or Terraform state. Add them to .gitignore if missing. The key was pasted in a chat earlier: tell me to revoke it and create a new one before recording.
2. SYNTHETIC DATA ONLY. No real patient data anywhere.
3. ASK BEFORE: `terraform apply`, `terraform destroy`, any `az` command that creates or deletes something, any push/tag, anything that costs money. Show the plan and the cost estimate each time. Local commits are fine.
4. BUDGET FIRST: before the first apply, create a subscription or resource-group budget (suggest 30 USD) with alerts at 50/80/100% to my email. Tag every resource `purpose=cf-demo`, `owner=<me>`, `delete-after=<date>`.
5. DESTROY IS PART OF DONE: the task is not finished until teardown is verified (section 7). Never leave the cluster running overnight without telling me the hourly burn rate and asking.
6. Do not weaken safety properties to make the demo work: the de-identification gateway, grounding checks, CDS lint, row-level security (fail closed), human sign-off, audit log. If one blocks you, tell me.
7. Never claim something works unless you ran it. Say "not run" when you did not. Never say "HIPAA compliant". No marketing numbers: measure.
8. Keep changes small, tested (`python -m pytest deploy/tests -q` must stay green), commit as "[integrated] what and why". Ask before pushing.

## 3. Lessons from CityCare to carry over (apply them proactively)
  - Port mismatches: CityCare's POC broke because the gateway pointed at 80 while the app listened on 8501. Before anything else, list every Service port / targetPort / containerPort / probe port / Ingress backend in the rendered chart (`helm template`) and check they agree with what each image actually listens on (ai: 8003 and metrics port, meditrack: 8001, payer: see chart). Print the table.
  - Ordering/dependency deadlocks: CityCare's Bicep deadlocked because the identity and the AcrPull role depended on each other (fixed by moving the identity into its own module). In Terraform watch for the same pattern: `azurerm_role_assignment.aks_acr_pull` vs the cluster identity, and Key Vault role assignments vs secrets written by the deployer. Use `terraform graph`/plan to check, and add explicit `depends_on` only where needed.
  - Model availability: CityCare's `gpt-oss-20b` could not be deployed in the chosen Foundry region and 120b worked in South India. If I pick Foundry, check model + region availability with `az cognitiveservices` BEFORE applying, and expect throttling if you PUT the same deployment repeatedly (do it once, wait).
  - Secrets before hooks: azd checked required secrets before the hooks that create them. Equivalent here: create the Kubernetes Secrets and run the DB role script BEFORE installing the chart, in a deliberate order, and make the script idempotent.
  - CLI quirks: `azd env get-value` prints errors as values. Equivalent trap: `terraform output` and `az ... -o tsv` returning empty/ERROR strings. Always check exit codes and non-empty values before using them.
  - Post-deploy settle time: CityCare saw database auth errors for ~90 s after deploy. Expect the same (Postgres flexible server + roles + pods starting). Build in a readiness wait (poll `/healthz` and `/readyz`) instead of sleeping, and do NOT start recording until the smoke test passes twice in a row.
  - Cost visibility: CityCare never measured real cost and its stack kept billing after the demo. Here, measure: after the rehearsal, read the actual cost per resource from Azure Cost Management (costs lag several hours, say so) and report it against my estimate.
  - One command up, one command down: CityCare had `./run.sh cloud-down`. Build the same here: `deploy/azure-demo/demo.sh up | smoke | stop | start | down | status`. `down` must also remove what Terraform does not (see section 7).
  - Local ports from CityCare (8011/5433) do not matter here, but check 5432/8081/8001/8003 on the laptop if you do the kind dry run.

## 4. What I expect you to build (small, in `deploy/azure-demo/` plus tiny edits elsewhere)
Read first: docs/KT.md, docs/FINAL_REVIEW.md, deploy/README.md, docs/decisions.md, deploy/terraform/azure/*, deploy/helm/cityforward/{values.yaml,values-aks.yaml,values-kind.yaml}, deploy/kind/bootstrap.sh, docs/LOCAL_AGENT_PROMPT.md (phases 5 and 7 describe kind and Terraform).

Facts you must respect (verified in the repo):
  - The Terraform has NEVER been `validate`d, planned or applied. Expect fixes. The kind bootstrap has also never been run on a real machine.
  - Defaults are hospital-style: private AKS API, ACR Premium with public access off, Key Vault premium with purge protection and public access off, PostgreSQL with zone-redundant HA and public access off, storage with public access off, Defender on, 3 x Standard_D4s_v5 across 3 zones, Log Analytics 365 days. That is ~1.3-1.6 USD/hour and unreachable from my laptop. A demo profile must change that.
  - The AI image needs: GROQ_API_KEY (or LLM_API_KEY), LLM_PROVIDER, COPILOT_DB_URL, MEDITRACK_BASE_URL, PAYER_BASE_URL, AI_BACKEND_KEY_FILE, AI_WEBHOOK_SECRET, METRICS_PORT, OTEL_EXPORTER_OTLP_ENDPOINT, EMBEDDER, COPILOT_DAILY_BUDGET_USD. `Gateway.__init__` refuses a real model client without a redactor (override only with LLM_IN_BOUNDARY=1, which you must NOT set for Groq).
  - The chart expects Secrets cityforward-ai-keys, cityforward-meditrack-signing, cityforward-webhook, cityforward-payer, cityforward-db, cityforward-llm and ConfigMap cityforward-ai-jwks. `deploy/kind/bootstrap.sh` shows how they are generated for the laptop; reuse `ai/scripts/gen_keys.py`.
  - Images: build in ACR with `az acr build` (so my laptop does not need to push over a private endpoint). The ai image has a build arg FASTEMBED=1; for the demo prefer the offline embedder unless I ask, to keep the image small and boot fast.
  - pgvector: the Postgres flexible server needs `vector` (and pgaudit) allow-listed (Terraform does this) and `CREATE EXTENSION vector` in the copilot database via scripts/db-roles.sql. Because Postgres is private, run that script from a throwaway pod inside the cluster (`kubectl run psql --rm -it --image=postgres:16 ...`) or `az aks command invoke`; do not open the database to the internet.

### 4a. Demo Terraform profile (`deploy/azure-demo/demo.tfvars` + minimal variable additions)
Keep the hospital defaults for the course's "production" story; add a demo profile that overrides them. Add variables (default = current behavior so nothing else changes) for: ACR SKU (Basic or Standard for the demo; Premium cannot be public-restricted the same way), `enable_defender` (false), `kv_purge_protection` (false for the demo so teardown is clean; if it must stay true, document the soft-delete name reservation and add `az keyvault purge`), `aks_sku_tier` (Free), `zones` (single zone or none), log retention (30 days). Demo values:
  - private_cluster=false, authorized_ip_ranges=["<my ip>/32"], admin_group_object_ids=[<mine>]
  - node_count=2, node_vm_size=Standard_D2s_v5 (or the cheapest size that passes the quota check and fits the pods; check the chart's resource requests and report if 2 x D2 cannot schedule everything including the OTel collector and Prometheus)
  - postgres_ha=false, postgres_sku = smallest Burstable that supports pgvector (e.g. B2s) if the Terraform allows it; say what you changed
  - location per my answer
Run `az vm list-usage` / `az quota` for the VM family and region and report headroom BEFORE planning. If quota is zero, tell me and propose the nearest region/size.
Then `terraform init`, `terraform fmt -check`, `terraform validate`, `terraform plan -var-file=demo.tfvars`. Fix real errors (provider arguments that changed, missing attributes). Show me the plan summary (resource count, no surprises like Defender or HA) and a cost estimate table (per resource, per hour, and total for 2 h rehearsal + 4 h recording day). Wait for my OK to apply.

### 4b. Kubernetes layer
  1. `az aks get-credentials`; verify nodes Ready.
  2. Create namespaces and the Secrets from the env var / generated keys (no values on disk in the repo). Create the ConfigMap with the JWKS.
  3. Run db-roles.sql from an in-cluster pod. Verify the roles and `\dx` shows vector.
  4. Build images with `az acr build`; use the chart values to point at them (tag or digest). Do not edit `values-aks.yaml` for the demo; create `deploy/azure-demo/values-demo.yaml` that layers on top and: sets externalDatabase to the Terraform server, uses Kubernetes Secrets instead of the Key Vault CSI driver (`keyVault.enabled=false`) unless wiring the CSI path is quick and I want it shown, sets `copilot.llm.provider=groq` + model, `copilot.replicas=1`, `networkPolicy.enabled=true` with an egress rule that still allows DNS, Postgres, the OTel collector, and api.groq.com (a CIDR-based rule cannot do FQDN: say so and use a broad 443 egress for the AI pods for the demo, noting that production uses Azure Firewall FQDN rules), ingress via the AKS-managed or a plain LoadBalancer Service on a public IP that is IP-restricted to my address (NSG/loadBalancerSourceRanges) and without TLS if no domain (say so on camera; or use a free nip.io host plus cert-manager only if quick). The URL must be same-origin for MediTrack and /ai like the Caddy gateway in CityCare.
  5. `helm lint`, `helm template | kubeconform` (already done by tests), then `helm upgrade --install`. `helm test`.
  6. Platform pieces, each installed with pinned chart versions and their values from the repo:
     - OpenTelemetry Collector (`deploy/platform/otel/collector-values.yaml`) - it is wired to export traces to Jaeger; for the demo point traces at a `debug`/logging exporter or the collector's own prometheus exporter (port 8889) so nothing else needs installing. Keep the PHI-scrubbing processors untouched.
     - Prometheus: install kube-prometheus-stack OR (cheaper, simpler on camera) a plain Prometheus chart with the ServiceMonitor CRDs; load `deploy/platform/monitoring/prometheus-rules.yaml`. Grafana only if it fits the node budget; otherwise use the Prometheus UI (graph + alerts page).
  7. `ai/e2e/smoke.py` against the public URL, then `python -m incident.drill` against the cluster (adapt only the URL/connection settings, not the logic). Run `python course/check_submission.py` if it applies to the deployment.

### 4c. Demo choreography (write it to `docs/AZURE_DEMO_SCRIPT.md`, 20-30 min)
Timed beats, each with the exact command or click and what to say, in this order:
  1. (2 min) The environment: `kubectl get nodes,pods -A`, `helm list`, one-sentence what each of the 3 CNCF projects does here.
  2. (4 min) Helm: show `values.yaml` vs `values-demo.yaml`, `helm template` excerpt, the security posture (non-root, read-only fs, NetworkPolicy), `helm test`.
  3. (6 min) The product: MediTrack -> patient Robert Hale -> AI Clinical Insights (SMART launch + PKCE) -> Summarize stay (summary says it came from the groq model) -> Why panel -> act-as switch.
  4. (6 min) OpenTelemetry: send traffic, show a trace/span and prove no PHI is in it (show the collector config's scrub processors and the before/after).
  5. (6 min) Prometheus: the metrics the code exports, the alert rules, then the climax: run the incident drill (redaction breaks) and show `RedactionStoppedWorking` firing, then the fix and the alert clearing.
  6. (2 min) Optional mentions: Argo CD (GitOps would do `helm upgrade` for us) and Kyverno (refuse unsigned images). Slides or a repo file only; do not install.
  7. (2 min) Teardown on camera: `demo.sh down`, then show the empty resource group.
Also write a 1-page "if it breaks on camera" runbook: the three most likely failures from rehearsal and the fastest recovery, and a pre-recorded fallback clip suggestion.

## 5. Phases and acceptance criteria (report after each, wait for my OK at the gates marked GATE)
  P0  Scope confirmation + prerequisites (az, terraform >= the version in versions.tf, kubectl, helm, docker, jq; `az login`; subscription selected; providers registered: Microsoft.ContainerService, DBforPostgreSQL, KeyVault, ContainerRegistry, OperationalInsights, Storage). Acceptance: a table.
  P1  Local dry run on kind first (free): `deploy/kind/bootstrap.sh` with the 3 platform pieces. Acceptance: dashboard flow at localhost:8081, OTel scrub verified, Prometheus alert fires in the drill. Fix script/chart problems (tests stay green). This is where most bugs should die, not on Azure.
  P2  Demo Terraform profile + quota check + plan + cost table. GATE: my OK to apply (and to the budget alert).
  P3  Apply (rehearsal day). Acceptance: all resources created; `terraform output` values non-empty.
  P4  Kubernetes layer + platform + smoke + drill. Acceptance: smoke passes twice in a row; drill alert fires and clears; timings noted (time-to-ready from `up`).
  P5  Rehearse the full 20-30 minute script once while timing it. Fix the script. Take screenshots into ./shots/azure (nothing containing secrets). Then, GATE: ask me whether to `down` now (cheapest, ~10-15 min to rebuild) or `stop` overnight (AKS stop + Postgres stop: compute stops, disks/ACR/IP/storage keep billing roughly 0.1-0.2 USD/hour; Postgres auto-restarts after 7 days, AKS stop is limited too). Default recommendation: down, and re-up on recording day using the proven scripts.
  P6  Recording day: `up` -> smoke x2 -> I record -> `down`. 
  P7  Teardown + verification (section 7) + cost report (estimate vs actual).

## 6. Cost rules
  - Quote the estimate before every apply. Reference numbers (from memory, unverified): production defaults ~1.3-1.6 USD/h; lean demo ~0.4-0.6 USD/h. A forgotten 3-day run of defaults costs ~100 USD. Verify prices with the Azure pricing calculator/API for my region and give me the real figure, flagging anything you are unsure about.
  - Model calls: use the budget env var COPILOT_DAILY_BUDGET_USD to cap spend. Groq calls are cents for a demo.
  - Prefer one session of work over leaving things idle. Never `az aks start` or re-apply without telling me the burn rate.

## 7. Teardown (must be scripted and verified)
`demo.sh down` does, in order, after asking me to type the resource group name to confirm:
  1. `helm uninstall` (releases) and delete the LoadBalancer Services first, so Azure releases the public IPs and load balancers cleanly (leftover node-resource-group objects are a classic leak)
  2. `terraform destroy -var-file=demo.tfvars`
  3. If Key Vault purge protection was on: list soft-deleted vaults and tell me the name stays reserved for the retention period (or purge if allowed). Also check soft-deleted Cognitive Services accounts if Foundry was used (`az cognitiveservices account list-deleted`), because those reserve names and quota too.
  4. Remove the node resource group leftovers (MC_*), the `NetworkWatcherRG` is NOT ours, leave it.
  5. Verify: `az group list --query "[?tags.purpose=='cf-demo']"` is empty, `az resource list` shows nothing for the demo, no public IPs or disks tagged for the demo remain, Terraform state is empty, local kubeconfig context removed, the budget alert remains (it is free) but I get a reminder to check the cost page the next day.
  6. Report: what was destroyed, what remains (budget, Terraform state backend if any), and the actual cost.
`demo.sh status` prints, at any time: resources existing, hours since `up`, estimated spend so far, and the exact command to destroy.

## 8. Reporting format (after every phase)
  Done / Not done / Evidence (command + output summary, never secrets) / Surprises vs the CityCare lessons / Money spent so far and burn rate now / Next step and what I must approve.

## 9. Finish
When all phases are done: update docs/decisions.md (correct "all CNCF graduated": OpenTelemetry and Keycloak are, to my knowledge, incubating - verify on cncf.io/projects and fix the wording; Kubernetes, Helm, Prometheus, Argo CD, Kyverno status: verify), add `deploy/azure-demo/README.md` (up/down usage, costs, the demo profile vs production profile, the five things that went wrong and how they were fixed), commit locally, show me the diff and ask before pushing.
Start now with section 0.
```

## Notes for you (not part of the prompt)

* The Terraform has never been planned or applied, and `deploy/kind/bootstrap.sh` has never run on a real machine. Expect the agent to fix things in P1/P2. That is why the kind dry run comes before any Azure spend.
* The cost numbers are from memory and have not been checked against the Azure calculator. The prompt tells the agent to verify them.
* Rotate the Groq key that was pasted in chat before you record.
