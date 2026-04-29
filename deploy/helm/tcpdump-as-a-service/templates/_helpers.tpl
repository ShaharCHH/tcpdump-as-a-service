{{/*
Common labels applied to all resources.
*/}}
{{- define "tcpdump.labels" -}}
app.kubernetes.io/name: tcpdump-as-a-service
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Hub WebSocket URL — used by agents to connect back to the hub.
If hub.wsUrl is set in values, use it. Otherwise build from the release namespace.
*/}}
{{- define "tcpdump.hubWsUrl" -}}
{{- if .Values.hub.wsUrl -}}
{{ .Values.hub.wsUrl }}
{{- else -}}
ws://{{ .Release.Name }}-hub.{{ .Release.Namespace }}.svc.cluster.local:{{ .Values.hub.service.port }}
{{- end -}}
{{- end }}
