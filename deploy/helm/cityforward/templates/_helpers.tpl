{{- define "cf.name" -}}{{ .Chart.Name }}{{- end -}}
{{- define "cf.labels" -}}
app.kubernetes.io/part-of: cityforward
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}
{{- define "cf.selector" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
{{- end -}}
{{- define "cf.image" -}}
{{- $r := .repository -}}{{- if hasPrefix "sha256:" .tag -}}{{ $r }}@{{ .tag }}{{- else -}}{{ $r }}:{{ .tag }}{{- end -}}
{{- end -}}
{{/* the pod-level and container-level security context every workload gets: non-root, no privilege escalation, read-only root filesystem, no capabilities */}}
{{- define "cf.podSecurity" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
seccompProfile: {type: RuntimeDefault}
{{- end -}}
{{- define "cf.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities: {drop: ["ALL"]}
{{- end -}}
{{- define "cf.publicBase" -}}
{{- if .Values.meditrack.publicBaseUrl -}}{{ .Values.meditrack.publicBaseUrl }}{{- else if .Values.ingress.host -}}{{ ternary "https" "http" .Values.ingress.tls.enabled }}://{{ .Values.ingress.host }}{{- else -}}http://localhost{{- end -}}
{{- end -}}
{{/* Key Vault objects grouped by the Kubernetes Secret they are synced into: {secretName: [{name, key}, ...]} */}}
{{- define "cf.kvSecrets" -}}
{{- $out := dict -}}
{{- range .Values.keyVault.objects -}}
{{- $list := get $out .secret | default list -}}
{{- $_ := set $out .secret (append $list (dict "name" .name "key" .key)) -}}
{{- end -}}
{{- toYaml $out -}}
{{- end -}}
