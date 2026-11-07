#!/usr/bin/env bash
# One command up, one command down for the short-lived Azure demo (AKS + PostgreSQL + Key Vault + ACR + Foundry model). Synthetic data only.
#
#   deploy/azure-demo/demo.sh plan        read-only: what Terraform would create (no cost)
#   CF_APPROVED=yes deploy/azure-demo/demo.sh providers   register Microsoft.ContainerService (free, subscription-level change)
#   CF_APPROVED=yes deploy/azure-demo/demo.sh up          create everything, deploy, smoke test (COSTS MONEY)
#   deploy/azure-demo/demo.sh smoke       smoke test the running deployment
#   CF_APPROVED=yes deploy/azure-demo/demo.sh redeploy [--images] [--reseed]   push chart/gateway (--images: rebuilt code, --reseed: reload the synthetic data) to the running deployment
#   CF_APPROVED=yes deploy/azure-demo/demo.sh stop|start  deallocate / resume the cluster and database between sessions
#   CF_APPROVED=yes deploy/azure-demo/demo.sh down        destroy everything and verify nothing is left
#   deploy/azure-demo/demo.sh status      what exists and what it costs per hour
#
# Anything that creates, starts, stops or deletes cloud resources needs CF_APPROVED=yes, set deliberately for that one command.
# SECURITY GATE: up, redeploy, resume and start are disabled until the staff-authentication release checks pass. Stop/down remain available.
# No secret is printed, written to the repo, or passed as an argument: see azure_secrets.py and azure_db_roles.py.
set -euo pipefail
set +x
umask 077
cd "$(dirname "$0")/../.."
ROOT="$PWD"
SUB_ID="90a95abd-9615-4480-a1e4-f2f713767b6e"        # Databricks_live, selected by the owner
SUB_NAME="Databricks_live"
TFDIR=deploy/terraform/azure
DATA="$ROOT/.data"
STATE="$DATA/demo.tfstate"
PLAN="$DATA/demo.tfplan"
LOCAL_VARS="$DATA/demo-local.tfvars"
export KUBECONFIG="$DATA/kubeconfig-azure"
export ARM_SUBSCRIPTION_ID="$SUB_ID" ARM_RESOURCE_PROVIDER_REGISTRATIONS=none TF_DATA_DIR="$DATA/tfdata" TF_IN_AUTOMATION=1
PYTHON="${PYTHON:-python3}"
mkdir -p "$DATA"

die() { echo "ERROR: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 is required${2:+ ($2)}"; }
approved() { [[ "${CF_APPROVED:-}" == "yes" ]] || die "'$1' changes cloud resources: set CF_APPROVED=yes for this command after reviewing 'demo.sh plan' and the cost estimate"; }
nonempty() { [[ -n "${2:-}" && "$2" != "null" && "$2" != ERROR* ]] || die "empty or invalid value for $1"; }   # az/terraform can print errors as values

require_subscription() {
  local id name; id=$(az account show --query id -o tsv); name=$(az account show --query name -o tsv)
  [[ "$id" == "$SUB_ID" && "$name" == "$SUB_NAME" ]] || die "selected subscription is '$name'; run: az account set --subscription $SUB_ID"
}

tf() { terraform -chdir="$TFDIR" "$@"; }
tf_vars=(-var-file="$ROOT/deploy/azure-demo/demo.tfvars" -var-file="$LOCAL_VARS" -state="$STATE")

make_local_vars() {
  local ip oid
  ip=$(curl -fsS -m 10 https://api.ipify.org); nonempty "public IP" "$ip"
  oid=$(az ad signed-in-user show --query id -o tsv); nonempty "signed-in user object id" "$oid"
  cat >"$LOCAL_VARS" <<EOF
authorized_ip_ranges     = ["$ip/32"]
cluster_admin_object_ids = ["$oid"]
tags = {
  purpose             = "cf-demo"
  owner               = "yashpatil"
  data-classification = "synthetic"
  delete-after        = "$(date -v+2d +%Y-%m-%d 2>/dev/null || date -d '+2 days' +%Y-%m-%d)"
}
EOF
  MY_IP="$ip"
}

tf_out() { local v; v=$(tf output -state="$STATE" -raw "$1") || die "terraform output $1 failed"; nonempty "terraform output $1" "$v"; printf '%s' "$v"; }

cmd_plan() {
  need terraform; need az; need curl; require_subscription
  make_local_vars
  tf init -input=false -backend=false >/dev/null
  tf plan -input=false -no-color "${tf_vars[@]}" -out="$PLAN" | grep -E '^Plan:|^No changes' || true
  echo "Plan saved to .data/demo.tfplan. Nothing was created."
}

cmd_providers() {
  approved providers; require_subscription
  az provider register --namespace Microsoft.ContainerService --wait
  echo "Microsoft.ContainerService: $(az provider show -n Microsoft.ContainerService --query registrationState -o tsv)"
}

# Gateway (HTTPS, public only to this machine's /32) then the chart. Used by 'up' and 'redeploy'. Expects RG AKS ACR_LOGIN KV CLIENT_ID TENANT MY_IP and K().
deploy_app() {
  render_gateway() { sed -e "s#__IMAGE__#$ACR_LOGIN/cityforward-gateway:demo#" -e "s#__SOURCE_RANGE__#$MY_IP/32#" -e "s#__HOST__#$1#g" deploy/azure-demo/azure-gateway.yaml; }
  echo "== gateway (public to $MY_IP only, HTTPS with a self-signed certificate)"
  IP=$(K -n gateway get svc gateway -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
  if [[ -z "$IP" ]]; then
    render_gateway localhost | K apply --validate=false -f -              # first apply only to obtain the public IP the certificate must name
    for _ in $(seq 1 60); do IP=$(K -n gateway get svc gateway -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true); [[ -n "$IP" ]] && break; sleep 10; done
  fi
  nonempty "gateway public IP" "$IP"
  render_gateway "$IP" | K apply --validate=false -f -
  K -n gateway rollout restart deploy/gateway >/dev/null
  K -n gateway rollout status deploy/gateway --timeout=180s

  echo "== chart"      # --disable-openapi-validation: the API schema download can take minutes on some networks and fails the install; the chart is validated against Kubernetes schemas in CI (deploy/tests)
  helm --kube-context "$AKS" upgrade --install cityforward deploy/helm/cityforward -n cityforward \
    -f deploy/helm/cityforward/values-aks.yaml -f deploy/helm/cityforward/values-azure-demo.yaml \
    --set-string "images.customer.repository=$ACR_LOGIN/cityforward-customer" --set-string "images.ai.repository=$ACR_LOGIN/cityforward-ai" \
    --set-string "keyVault.name=$KV" --set-string "keyVault.tenantId=$TENANT" --set-string "keyVault.workloadIdentityClientId=$CLIENT_ID" \
    --set-string "meditrack.publicBaseUrl=https://$IP" --set "meditrack.seedForce=${RESEED:-false}" --disable-openapi-validation --wait --timeout 15m
  helm --kube-context "$AKS" test cityforward -n cityforward
  echo "https://$IP" >"$DATA/azure-gateway-url"
}

# Common setup for commands that act on a running deployment.
attach() {
  need terraform; need az; need kubectl; need helm; need kubelogin; need curl; require_subscription
  [[ -f "$STATE" ]] || die "no running demo (no .data/demo.tfstate)"
  make_local_vars
  RG=$(tf_out resource_group); AKS=$(tf_out aks_name); ACR_LOGIN=$(tf_out acr_login_server); KV=$(tf_out key_vault_name); CLIENT_ID=$(tf_out copilot_identity_client_id)
  ACR=${ACR_LOGIN%%.*}; TENANT=$(az account show --query tenantId -o tsv); nonempty tenant "$TENANT"
  az aks get-credentials -g "$RG" -n "$AKS" --file "$KUBECONFIG" --overwrite-existing >/dev/null
  kubelogin convert-kubeconfig -l azurecli --kubeconfig "$KUBECONFIG"
  K() { kubectl --context "$AKS" "$@"; }
}

build_images() {
  echo "== images (built inside ACR; nothing is pushed from this laptop)"
  az acr build -r "$ACR" -t cityforward-customer:demo customer >/dev/null
  az acr build -r "$ACR" -t cityforward-ai:demo -f ai/Dockerfile . >/dev/null
  az acr build -r "$ACR" -t cityforward-gateway:demo -f deploy/azure-demo/gateway.Dockerfile deploy/azure-demo >/dev/null
}

# Namespace, Key Vault secrets and the database roles: all idempotent. Expects KV AKS and K().
prepare_cluster() {
  echo "== namespace, secrets, database roles"
  K get ns cityforward >/dev/null 2>&1 || K create ns cityforward
  K label ns cityforward pod-security.kubernetes.io/enforce=restricted --overwrite
  PYTHON="$PYTHON" "$PYTHON" deploy/azure-demo/azure_secrets.py --vault "$KV" --kube-context "$AKS" --kubeconfig "$KUBECONFIG"
  "$PYTHON" deploy/azure-demo/azure_db_roles.py --vault "$KV" --kube-context "$AKS" --kubeconfig "$KUBECONFIG"

}

# Continue an 'up' that stopped after the infrastructure and images (for example on a network blip): secrets, roles, chart, smoke.
cmd_resume() {
  approved resume; attach
  prepare_cluster
  deploy_app
  cmd_smoke && cmd_smoke
  echo "Up. Open $(cat "$DATA/azure-gateway-url")  (accept the one-time certificate warning)."
}

# Push local code/chart changes to the running deployment (the feedback loop). Rebuilds images with --images, restarting the pods that use them.
cmd_redeploy() {
  approved redeploy; attach
  RESEED=false
  [[ " $* " == *" --reseed "* ]] && RESEED=true        # wipe MediTrack and reload the synthetic data (needed when the seed itself changed)
  if [[ " $* " == *" --images "* ]]; then
    build_images
    K -n cityforward rollout restart deploy/copilot deploy/meditrack deploy/payer >/dev/null
  fi
  deploy_app
  cmd_smoke
}

cmd_up() {
  approved up
  need terraform; need az; need kubectl; need helm; need kubelogin "brew install kubelogin"; need curl; need openssl; need jq
  require_subscription
  [[ "$(az provider show -n Microsoft.ContainerService --query registrationState -o tsv)" == "Registered" ]] || die "Microsoft.ContainerService is not registered: CF_APPROVED=yes demo.sh providers"
  [[ -f "$DATA/foundry-resource.json" ]] || die "no Foundry resource state: run deploy/azure-demo/foundry.py up first"
  make_local_vars

  echo "== terraform"
  tf init -input=false -backend=false >/dev/null
  tf plan -input=false -no-color "${tf_vars[@]}" -out="$PLAN" | grep -E '^Plan:|^No changes' || true
  tf apply -input=false -no-color -state="$STATE" "$PLAN"

  RG=$(tf_out resource_group); AKS=$(tf_out aks_name); ACR_LOGIN=$(tf_out acr_login_server); KV=$(tf_out key_vault_name); CLIENT_ID=$(tf_out copilot_identity_client_id)
  ACR=${ACR_LOGIN%%.*}; TENANT=$(az account show --query tenantId -o tsv); nonempty tenant "$TENANT"

  echo "== cluster credentials"
  az aks get-credentials -g "$RG" -n "$AKS" --file "$KUBECONFIG" --overwrite-existing >/dev/null
  kubelogin convert-kubeconfig -l azurecli --kubeconfig "$KUBECONFIG"
  K() { kubectl --context "$AKS" "$@"; }
  for _ in $(seq 1 40); do K get ns >/dev/null 2>&1 && break; sleep 15; done      # the cluster-admin role assignment takes a few minutes to propagate
  K get ns >/dev/null || die "cannot reach the cluster API"

  echo "== images (built inside ACR; nothing is pushed from this laptop)"
  az acr build -r "$ACR" -t cityforward-customer:demo customer >/dev/null
  az acr build -r "$ACR" -t cityforward-ai:demo -f ai/Dockerfile . >/dev/null
  az acr build -r "$ACR" -t cityforward-gateway:demo -f deploy/azure-demo/gateway.Dockerfile deploy/azure-demo >/dev/null

  prepare_cluster
  deploy_app
  echo "== smoke (twice, as required before recording)"
  cmd_smoke && cmd_smoke
  echo
  echo "Up. Open https://$IP  (accept the one-time certificate warning; reachable only from $MY_IP). Burn rate: see 'demo.sh status'. Tear down: CF_APPROVED=yes deploy/azure-demo/demo.sh down"
}

cmd_smoke() {
  [[ -f "$DATA/azure-gateway-url" ]] || die "no deployment recorded (run 'up' first)"
  "$PYTHON" deploy/azure-demo/smoke.py --base-url "$(cat "$DATA/azure-gateway-url")" --insecure --expect-model foundry --model-call
}

cmd_stop() {
  approved stop; require_subscription
  local rg aks pg; rg=$(tf_out resource_group); aks=$(tf_out aks_name)
  pg=$(az postgres flexible-server list -g "$rg" --query '[0].name' -o tsv); nonempty "postgres server" "$pg"
  az aks stop -g "$rg" -n "$aks"
  az postgres flexible-server stop -g "$rg" -n "$pg"      # Azure restarts a stopped server by itself after 7 days
  echo "Stopped. Compute billing paused; storage, public IPs and the registry still bill (see status). 'start' resumes."
}

cmd_start() {
  approved start; require_subscription
  local rg aks pg; rg=$(tf_out resource_group); aks=$(tf_out aks_name)
  pg=$(az postgres flexible-server list -g "$rg" --query '[0].name' -o tsv); nonempty "postgres server" "$pg"
  az postgres flexible-server start -g "$rg" -n "$pg"
  az aks start -g "$rg" -n "$aks"
  kubelogin convert-kubeconfig -l azurecli --kubeconfig "$KUBECONFIG" || true
  echo "Started. Database authentication errors for about 90 seconds after a start are expected; smoke polls instead of sleeping."
  cmd_smoke
}

cmd_down() {
  approved down; need terraform; need az; require_subscription
  [[ -f "$STATE" ]] || die "no Terraform state at .data/demo.tfstate: nothing of this demo is tracked (check 'az group list --tag purpose=cf-demo')"
  # Outputs only exist after a fully successful apply; after a partial one, fall back to the demo's fixed names and ask Azure for the vault.
  local rg kv; rg=$(tf output -state="$STATE" -raw resource_group 2>/dev/null) || rg="rg-cityforward-demo"
  kv=$(az keyvault list -g "$rg" --query '[0].name' -o tsv 2>/dev/null || true)
  make_local_vars
  echo "== terraform destroy"
  tf destroy -input=false -no-color -auto-approve "${tf_vars[@]}"
  echo "== verify"
  local left=0
  for g in "$rg" "MC_${rg}_aks-cityforward-demo_eastus2"; do
    if [[ "$(az group exists -n "$g")" == "true" ]]; then echo "STILL EXISTS: resource group $g"; left=1; fi
  done
  if [[ -n "$kv" ]] && az keyvault show-deleted --name "$kv" >/dev/null 2>&1; then
    az keyvault purge --name "$kv" && echo "purged soft-deleted Key Vault $kv"
  fi
  # The tag listing is an index that lags deletes (a deleted server was listed for minutes): confirm each hit with a direct lookup.
  local id
  while IFS= read -r id; do
    [[ -z "$id" ]] && continue
    if az resource show --ids "$id" >/dev/null 2>&1; then echo "STILL EXISTS: $id"; left=1; else echo "index entry only (resource is gone): ${id##*/}"; fi
  done < <(az resource list --tag purpose=cf-demo --tag environment=demo --query '[].id' -o tsv)
  [[ $left -eq 0 ]] || die "teardown is NOT verified: resources remain (see above)"
  rm -f "$STATE" "$STATE.backup" "$PLAN" "$DATA/azure-gateway-ip" "$DATA/azure-gateway-url" "$KUBECONFIG"
  echo "Torn down and verified: demo resource groups gone, no tagged resources left, Key Vault purged. The Foundry resource group and the budget are separate (foundry.py down)."
}

cmd_status() {
  require_subscription
  if [[ ! -f "$STATE" ]]; then echo "No demo state: nothing deployed from this checkout."; return 0; fi
  local rg; rg=$(tf_out resource_group) || die "state exists but has no outputs"
  echo "resource group $rg exists: $(az group exists -n "$rg")"
  az resource list -g "$rg" --query '[].{type:type,name:name}' -o table
  az aks show -g "$rg" -n "$(tf_out aks_name)" --query '{power:powerState.code,tier:sku.tier,nodes:agentPoolProfiles[0].count,size:agentPoolProfiles[0].vmSize}' -o table
  echo "Approx. hourly burn while running (D2as_v7 list 0.0908 USD x2 nodes verified from the Azure Retail Prices API; other lines are estimates):"
  echo "  nodes ~0.18 | PostgreSQL GP D2ds_v5 ~0.17 (estimate) | load balancer + public IPs ~0.04 | ACR Basic ~0.007 | disks+logs ~0.01  =>  about 0.41 USD per hour, ~10 USD per day"
}

case "${1:-}" in
  plan) cmd_plan ;; providers) cmd_providers ;;
  up|redeploy|resume|start) die "Azure startup and deployment are disabled until deployment authentication is implemented and verified" ;;
  smoke) cmd_smoke ;; stop) cmd_stop ;; down) cmd_down ;; status) cmd_status ;;
  *) sed -n '2,13p' "$0"; exit 2 ;;
esac
