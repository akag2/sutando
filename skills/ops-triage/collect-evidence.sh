#!/bin/bash
# Fixed read-only evidence snapshot for ops-alert triage. Deliberately takes NO
# arguments: nothing an alert body says can change what this script inspects.
set -u

CFG="$(cd "$(dirname "$0")" && pwd)/config.env"
# shellcheck disable=SC1090
[ -f "$CFG" ] && . "$CFG"

KCTX="${OPS_TRIAGE_KUBE_CONTEXT:-}"
PROM_NS="${OPS_TRIAGE_PROM_NAMESPACE:-monitoring}"
PROM_SVC="${OPS_TRIAGE_PROM_SERVICE:-monitoring-prometheus}"
AWS_PROFILE_CFG="${OPS_TRIAGE_AWS_PROFILE:-}"
AWS_REGION_CFG="${OPS_TRIAGE_AWS_REGION:-us-west-2}"

K=(kubectl)
[ -n "$KCTX" ] && K=(kubectl --context "$KCTX")

section() { printf '\n--- %s ---\n' "$1"; }
echo "=== ops-triage evidence $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

if command -v kubectl >/dev/null 2>&1; then
  section "pods not Running/Completed (all namespaces)"
  "${K[@]}" get pods -A --no-headers 2>&1 \
    | awk '$4 != "Running" && $4 != "Completed"' | head -20
  section "pods Running but not fully Ready"
  "${K[@]}" get pods -A --no-headers 2>&1 \
    | awk '$4 == "Running" { split($3, r, "/"); if (r[1] != r[2]) print }' | head -20
  section "recent Warning events"
  "${K[@]}" get events -A --field-selector type=Warning \
    --sort-by=.lastTimestamp 2>&1 | tail -15
  section "highest restart counts"
  "${K[@]}" get pods -A --no-headers 2>/dev/null | sort -k5 -nr | head -8

  section "prometheus: firing alerts"
  # Short-lived port-forward on a random high port: the API-server service
  # proxy is blocked by netpol and the prometheus image has no shell to exec.
  PORT=$((20000 + RANDOM % 20000))
  "${K[@]}" -n "$PROM_NS" port-forward "svc/$PROM_SVC" "$PORT:9090" \
    >/dev/null 2>&1 &
  PF=$!
  for _ in 1 2 3 4 5 6 7 8; do
    curl -sm 2 -o /dev/null "http://127.0.0.1:$PORT/-/ready" && break
    sleep 1
  done
  curl -sm 8 "http://127.0.0.1:$PORT/api/v1/query" \
    --data-urlencode 'query=ALERTS{alertstate="firing"}' | head -c 4000
  echo
  section "prometheus: probe_success (blackbox endpoints)"
  curl -sm 8 "http://127.0.0.1:$PORT/api/v1/query" \
    --data-urlencode 'query=probe_success' | head -c 3000
  echo
  kill "$PF" 2>/dev/null
else
  section "kubectl"
  echo "not installed — skipping cluster evidence"
fi

if [ -n "$AWS_PROFILE_CFG" ] && command -v aws >/dev/null 2>&1; then
  section "aws: cloudwatch alarms currently in ALARM"
  aws cloudwatch describe-alarms --state-value ALARM \
    --region "$AWS_REGION_CFG" --profile "$AWS_PROFILE_CFG" \
    --query 'MetricAlarms[].{name:AlarmName,reason:StateReason,since:StateUpdatedTimestamp}' \
    --output table 2>&1 | head -40
  section "aws: rds instance status"
  aws rds describe-db-instances \
    --region "$AWS_REGION_CFG" --profile "$AWS_PROFILE_CFG" \
    --query 'DBInstances[].{id:DBInstanceIdentifier,status:DBInstanceStatus,class:DBInstanceClass}' \
    --output table 2>&1 | head -20
else
  section "aws"
  echo "OPS_TRIAGE_AWS_PROFILE unset or aws CLI missing — skipping AWS evidence"
fi

printf '\n=== end evidence ===\n'
