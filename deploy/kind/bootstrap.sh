#!/usr/bin/env bash
# Bring the whole stack up on a laptop Kubernetes (kind): cluster, ingress, images, the secrets the chart expects, then the chart.
#   deploy/kind/bootstrap.sh            create or update
#   deploy/kind/bootstrap.sh down       delete the cluster
# Needs: docker, kind, kubectl, helm, openssl, python3 with `cryptography` and `pyjwt` (for the key pair). Optional: GROQ_API_KEY to switch the AI on.
# STATUS: written and statically verified; it could not be executed in the authoring sandbox (nested containers are blocked there). Expect to fix small things on the first run.
set -euo pipefail
cd "$(dirname "$0")/../.."
CLUSTER=cityforward
NS=cityforward

if [[ "${1:-}" == "down" ]]; then kind delete cluster --name "$CLUSTER"; exit 0; fi

kind get clusters | grep -qx "$CLUSTER" || kind create cluster --config deploy/kind/kind-config.yaml

echo "== ingress-nginx"
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.11.3/deploy/static/provider/kind/deploy.yaml
kubectl -n ingress-nginx rollout status deploy/ingress-nginx-controller --timeout=180s

echo "== images"
docker build -t cityforward-customer:dev customer
docker build -f ai/Dockerfile -t cityforward-ai:dev .
kind load docker-image cityforward-customer:dev cityforward-ai:dev --name "$CLUSTER"

echo "== namespace and secrets"
kubectl get ns "$NS" >/dev/null 2>&1 || kubectl create ns "$NS"
kubectl label ns "$NS" pod-security.kubernetes.io/enforce=restricted --overwrite
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
if ! kubectl -n "$NS" get secret cityforward-ai-keys >/dev/null 2>&1; then
  python3 ai/scripts/gen_keys.py "$TMP/keys" >/dev/null
  kubectl -n "$NS" create secret generic cityforward-ai-keys --from-file=ai-backend.pem="$TMP/keys/ai-backend.pem"
  kubectl -n "$NS" create configmap cityforward-ai-jwks --from-file=ai-backend.jwks.json="$TMP/keys/ai-backend.jwks.json"
fi
if ! kubectl -n "$NS" get secret cityforward-meditrack-signing >/dev/null 2>&1; then
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$TMP/sig.pem" 2>/dev/null
  kubectl -n "$NS" create secret generic cityforward-meditrack-signing --from-file=pem="$TMP/sig.pem"
fi
kubectl -n "$NS" get secret cityforward-webhook >/dev/null 2>&1 || kubectl -n "$NS" create secret generic cityforward-webhook --from-literal=secret="$(openssl rand -hex 24)"
kubectl -n "$NS" get secret cityforward-payer >/dev/null 2>&1 || kubectl -n "$NS" create secret generic cityforward-payer --from-literal=api-key="$(openssl rand -hex 16)"
EXTRA=()
if [[ -n "${GROQ_API_KEY:-}" ]]; then
  kubectl -n "$NS" create secret generic cityforward-llm --from-literal=api-key="$GROQ_API_KEY" --dry-run=client -o yaml | kubectl apply -f -
  EXTRA+=(--set copilot.llm.provider=groq)
fi

echo "== chart"
helm upgrade --install cityforward deploy/helm/cityforward -n "$NS" -f deploy/helm/cityforward/values-kind.yaml "${EXTRA[@]}" --wait --timeout 10m
helm test cityforward -n "$NS" || true
echo
echo "MediTrack:           http://localhost:8081"
echo "AI service health:   http://localhost:8081/ai/healthz"
echo "Open a patient in MediTrack and choose AI Clinical Insights."
