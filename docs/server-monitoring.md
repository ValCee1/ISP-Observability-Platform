# Monitoring the monitoring server

Everything else in this stack watches the *network*. This layer watches the
*box running the stack* and the monitoring pipeline itself, because a full
disk or a dead Alertmanager silently degrades every other alert.

## What is collected

| Source | Job | What |
|---|---|---|
| `node-exporter` (container, `:9100`) | `node-exporter` | host CPU, memory, disk, network, load, clock |
| Prometheus (self) | `prometheus` | rule evaluation, TSDB size/ingest |
| Alertmanager `/metrics` | `alertmanager` | notifications sent / failed per channel |
| Grafana `/metrics` | `grafana` | scrape reachability |
| `whatsapp-bridge` `/metrics` | `whatsapp-bridge` | messages sent, budget used vs cap |

Only standard Prometheus-ecosystem pieces are used (node-exporter is the
upstream project's own host exporter) — no extra agents or SaaS.

## Alerts (`prometheus/rules/server-health.yml`)

All carry `scope: monitoring` and `exporter: monitoring-server`, so Telegram
titles them *Monitoring: monitoring-server*. They are **not** suppressed by
`MonitoringVantageLost` — a sick server is exactly what you want to hear
about while the network is out.

| Alert | Severity | Fires when |
|---|---|---|
| `ServerDiskLow` | critical | a real filesystem has < 10 % free for 5 m |
| `ServerDiskFillingUp` | warning | linear extrapolation of the last 6 h hits zero within 24 h (for 1 h) |
| `ServerMemoryLow` | warning | < 10 % memory available for 10 m |
| `ServerCpuHigh` | warning | average CPU > 90 % for 15 m |
| `ServerClockSkew` | warning | clock > 0.5 s off NTP for 10 m |
| `PrometheusRuleFailures` | critical | any rule failed to evaluate in 10 m |
| `AlertDeliveryFailing` | critical | Alertmanager failed to send on a channel in 10 m |

`ExporterDown` (`exporter-health.yml`) also covers `node-exporter`,
`alertmanager`, `grafana` and `whatsapp-bridge`.

## Dashboard

**Server Health** (`grafana/dashboards/server-health.json`): CPU / memory /
lowest-disk-free / uptime tiles, CPU by mode, memory, disk-free per mount,
network, load, clock offset, stack component up/down, notification
sent-vs-failed, Prometheus ingest + TSDB size, rule-evaluation time and
failures, WhatsApp budget.

## Known limits

- **Dev machine caveat:** on Docker Desktop (macOS/Windows) node-exporter sees
  the Linux VM, not the host OS. Rules and dashboard are correct; the numbers
  only describe the real server on a Linux deployment.
- **Prometheus cannot alert about its own death.** `up{job="prometheus"}` is
  visible in Grafana, but nothing pages if Prometheus itself stops. The usual
  fix is an always-firing "Watchdog" alert pinged to an *external*
  dead-man's-switch service. Not built — it needs a third-party service and
  is a decision for later.
- If Telegram itself is the broken channel, `AlertDeliveryFailing` cannot
  reach it; it is still visible in Grafana and the Alertmanager UI.

## Tests

`prometheus/rules/tests/server_health_test.yml`. Run all rule tests:

```bash
docker run --rm -v "$PWD/prometheus:/p" --entrypoint sh prom/prometheus:v3.13.2 \
  -c 'cd /p && promtool check rules rules/*.yml && promtool test rules rules/tests/*.yml'
```
