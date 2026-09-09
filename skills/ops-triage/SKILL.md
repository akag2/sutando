---
name: ops-triage
description: Fixed read-only evidence snapshot for ops-alert triage — cluster health (pods, events, restarts), Prometheus firing alerts and probe_success, and optional AWS CloudWatch/RDS state. Invoked by the gateway's ops-alert evidence lane, never parameterized by alert text.
---

# Ops Triage — evidence collection

Alerts delivered by an ops relay bot arrive at GUEST tier: the sender is not
the owner and the alert *text* is untrusted input (anyone holding a webhook URL
can put arbitrary words in it). The evidence lane keeps that boundary while
still giving the sandboxed triage real data to reason about:

1. The core (outside the sandbox) runs `collect-evidence.sh` — a **fixed**
   script that takes no arguments. Nothing the alert says can change what it
   inspects; its configuration comes only from the owner-controlled
   `config.env` next to it.
2. The sandboxed analysis then reads the alert text *and* the evidence file,
   and writes the triage answer.

The lane only activates for sender/room pairs allowlisted in the channel
`.env` (`OPS_ALERT_SENDERS` + `OPS_ALERT_ROOMS`, both must match). Every other
guest task keeps the plain no-network sandbox path.

## What the script collects (all read-only)

- Pods not Running/Completed, and Running pods not fully Ready
- Recent Warning events and highest restart counts
- Prometheus `ALERTS{alertstate="firing"}` and `probe_success`, via a
  short-lived random-port port-forward
- If `OPS_TRIAGE_AWS_PROFILE` is set: CloudWatch alarms currently in ALARM and
  RDS instance status

Every source is best-effort: a missing CLI, expired credential, or unreachable
cluster degrades to a note in the output, never a failure.

## Configuration

Copy `config.env.example` to `config.env` (gitignored) and set:

| variable | meaning | default |
|---|---|---|
| `OPS_TRIAGE_KUBE_CONTEXT` | kubectl context to inspect | current context |
| `OPS_TRIAGE_PROM_NAMESPACE` | Prometheus namespace | `monitoring` |
| `OPS_TRIAGE_PROM_SERVICE` | Prometheus service name | `monitoring-prometheus` |
| `OPS_TRIAGE_AWS_PROFILE` | read-only AWS profile; unset skips AWS | unset |
| `OPS_TRIAGE_AWS_REGION` | AWS region | `us-west-2` |
