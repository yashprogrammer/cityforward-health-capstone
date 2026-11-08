#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
export KUBECONFIG="$PWD/.data/kubeconfig-kind"
helm upgrade --install otel-collector opentelemetry-collector \
  --repo https://open-telemetry.github.io/opentelemetry-helm-charts --version 0.111.0 \
  --kube-context kind-cityforward -n observability --create-namespace \
  -f deploy/platform/otel/collector-values.yaml -f deploy/azure-demo/otel-local.yaml --wait --timeout 5m
helm upgrade --install monitoring kube-prometheus-stack \
  --repo https://prometheus-community.github.io/helm-charts --version 92.13.1 \
  --kube-context kind-cityforward -n monitoring --create-namespace \
  -f deploy/azure-demo/prometheus-local.yaml --wait --timeout 8m
kubectl --context kind-cityforward apply -f deploy/platform/monitoring/prometheus-rules.yaml
