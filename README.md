# ISP Network Observability Platform

A small observability stack for an ISP network: MikroTik core routers, PTP wireless
backhaul links (AirMAX, AirFiber, SAF), monitored via SNMP (routers + radios) and ICMP
(link reachability), plus a RouterOS **API** collector for what SNMP can't reach —
per-subscriber PPP/PPPoE sessions and topology auto-discovery.
Visualized in Grafana, alerted on Telegram (and optionally WhatsApp, criticals only) via
Alertmanager. Hosted locally on one Linux server in the ISP's management network; it
also monitors itself.

```
Routers/Radios --SNMP--> snmp-exporter -----\
Links          --ICMP--> blackbox-exporter --> Prometheus -> Grafana
Routers        --API---> routeros-exporter -/            -> Alertmanager -> Telegram
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, container/network
  layout, monitored topology, metric-normalization and alert pipelines, with
  diagrams.
- [docs/tier1-subscriber-config-topology.md](docs/tier1-subscriber-config-topology.md)
  — the RouterOS API layer: subscriber intelligence,
  topology auto-discovery + POP-isolation rollup.
- [routeros_exporter/README.md](routeros_exporter/README.md) — the API collector
  itself (metrics, the read-only RouterOS user, `UNVERIFIED` list).
- [docs/tier2-anomaly-detection.md](docs/tier2-anomaly-detection.md) —
  baseline-deviation anomaly detection (subscriber counts, router CPU/memory)
  that catches slow bleeds the threshold alerts can't.
- [docs/GO-LIVE.md](docs/GO-LIVE.md) — production go-live checklist: what to replace,
  secure, verify and accept before cut-over.
- [docs/SETUP-INPUTS.md](docs/SETUP-INPUTS.md) — **every secret/file/ID you must
  supply on a fresh install, and where it goes** (`scripts/init.sh` scaffolds them).
- [docs/ISP Network Observability Platform - Operations Manual.docx](<docs/ISP Network Observability Platform - Operations Manual.docx>) — engineer
  edition (internal): daily routine, alerts, routine changes incl. SNMPv3, troubleshooting, backup,
  security, command cheat sheet, go-live status, server sizing.
- [docs/ISP Network Observability Platform - Management Overview.docx](<docs/ISP Network Observability Platform - Management Overview.docx>) — for
  management: what is monitored, alerting, security, costs, rollout status. No install or backend detail.
- [docs/ISP Network Observability Platform Pilot Proposal.pptx](<docs/ISP Network Observability Platform Pilot Proposal.pptx>) — the pilot proposal deck.
- [docs/SECURITY.md](docs/SECURITY.md) — security model: locally hosted, outbound-only,
  read-only access, why management-network placement adds no attack surface, hardening checklist.
- [docs/deadman.md](docs/deadman.md) — dead-man's switch: an outside heartbeat
  check that alerts when the whole stack goes silent.
- [docs/snmpv3.md](docs/snmpv3.md) — moving devices from SNMPv2c to encrypted
  SNMPv3 one at a time (credentials in a gitignored secret).
- [docs/ANSIBLE.md](docs/ANSIBLE.md) — **production deploys**: one command
  (`scripts/deploy.sh`) prepares an Ubuntu 24.04 server, writes secrets from an
  encrypted Ansible vault, checks everything, and reloads only what changed;
  previews, rollback, OS updates.
- [docs/INSTALL.md](docs/INSTALL.md) — manual install on another Linux machine
  (or Windows via Docker Desktop/WSL2), offline install, upgrades.
- [docs/server-monitoring.md](docs/server-monitoring.md) — monitoring the
  server itself (node-exporter, server-health alerts, Server Health dashboard).
- [docs/whatsapp.md](docs/whatsapp.md) — cost-controlled WhatsApp critical
  alerts (dry-run by default, hard caps, go-live checklist).
- [docs/DOCKER.md](docs/DOCKER.md) — building and publishing the
  `isp-observability` Prometheus image (repo config baked in) to a private
  registry.
- [docs/failure-drills.md](docs/failure-drills.md) — end-to-end failure runbook
  (detect → alert → explain → recover → no storm).

## Running the stack

On the production server, deploy with Ansible instead:
[docs/ANSIBLE.md](docs/ANSIBLE.md) (`scripts/deploy.sh`). For a local/dev run:

1. Fill in the secret files (all gitignored, not committed):
   - `secrets/grafana_admin_password.txt` — Grafana admin password
   - `secrets/telegram_bot_token.txt` — Telegram bot token used by Alertmanager
   - `secrets/routeros_api_credentials.json` — RouterOS API logins, keyed by
     router name (`cp routeros_exporter/routeros_api_credentials.example.json
     secrets/routeros_api_credentials.json`). Required for `docker compose up`
     even if you're not using the API collector yet.
   - `routeros_exporter/config.yml` — the router list for the API collector
     (`cp routeros_exporter/config.example.yml routeros_exporter/config.yml`).
2. Start everything:

   ```bash
   docker compose up -d
   ```

- Grafana: http://localhost:3000 (user: `admin`, password from the secret file above)
- Prometheus: http://localhost:9090
- Alertmanager: http://localhost:9093
- SNMP exporter: http://localhost:9116/metrics
- Blackbox exporter: http://localhost:9115/metrics
- Node exporter (host metrics): http://localhost:9100/metrics
- RouterOS API exporter: http://localhost:9436/metrics (needs
  `routeros_exporter/config.yml` + `secrets/routeros_api_credentials.json` — see
  [docs/tier1-subscriber-config-topology.md](docs/tier1-subscriber-config-topology.md))

To run Prometheus from the version-pinned image (config baked in) instead of the
local bind mounts, add the override file — see [docs/DOCKER.md](docs/DOCKER.md):

```bash
docker compose -f docker-compose.yml -f docker-compose.image.yml up -d
```

## What's monitored

- **Core routers** (`prometheus/targets/mikrotik-routers.yml`): CPU, memory/storage,
  uptime, interface state/traffic/errors via the generic `mikrotik` SNMP module.
- **PTP links** (`prometheus/targets/links.yml`): each link is two SNMP+ICMP-monitored
  radio endpoints (see `prometheus/rules/link-schema-notes.md` for the normalized
  `link_*` metric schema shared across vendors — AirMAX, AirFiber, SAF, MikroTik radios).
- **PtMP sectors** (`prometheus/targets/mikrotik-sectors.yml`): each MikroTik sector AP,
  via the `mikrotik_sector` SNMP module — the wireless registration table (per-CPE
  signal, SNR, per-chain strength, PHY rate, traffic, session uptime keyed by client
  name), AP-level noise floor / client count / frequency, and the sector device's own
  interface counters and CPU/memory. The ~20 CPEs per sector are covered by the AP's
  registration table, so they need no SNMP of their own. Capacity thresholds
  (clients / Mbps) live in `prometheus/rules/sector-thresholds.yml`. See
  `sector-normalization.yml` for the derived `sector_*` / `sector:*` series
  (RF throughput, per-CPE chain imbalance / drift, alignment score, 24h rollups).
- **Subscribers + topology** (`routeros_exporter/`, job `routeros-api`): the
  RouterOS API collector. Per-subscriber PPP/PPPoE sessions from `/ppp/active` +
  `/ppp/secret` + `/log` (caller-id, real uptime, disconnect reason,
  provisioned-vs-online, mass-outage detection); topology
  auto-discovery (`/ip/route` + `/ip/neighbor`) that finally activates the `pop`
  dependency-inhibition machinery. Details:
  [docs/tier1-subscriber-config-topology.md](docs/tier1-subscriber-config-topology.md).
- **Anomaly detection** (`prometheus/rules/anomaly-detection.yml`): a metric compared
  to its own recent baseline (1h vs 6h average) rather than a fixed threshold — catches
  a slow subscriber-count bleed or a CPU/memory creep the sharp threshold alerts can't.
  Details: [docs/tier2-anomaly-detection.md](docs/tier2-anomaly-detection.md).
- Recording rules (`prometheus/rules/link-normalization.yml`) turn raw per-vendor SNMP
  metrics into vendor-agnostic `link_*` series and a single `link_health_score` per link.
- Alerting rules (`prometheus/rules/link-alerts.yml`, `mikrotik-alerts.yml`) cover both
  hard state (link/router down) and soft degradation (weak signal, rising errors,
  "up but unhealthy"), routed and grouped per-entity in `alertmanager/alertmanager.yml`
  to avoid Telegram notification spam, with inhibition rules so a big outage doesn't
  also fire all its symptom alerts separately.

## Dashboards (`grafana/dashboards/`, auto-provisioned)

- **NOC Overview** — the front door: an **Active Incidents** table (every firing
  alert, one row per object, longest-running first), link/router/sector state
  counts, then color-coded per-object health tables for links and sectors, each
  linking through to its detail view.
- **PTP Link Monitoring** — drill-down for one link: status, latency, packet loss,
  RF signal/noise/quality, capacity/utilization, vendor-specific diagnostics.
- **MikroTik Router Overview** — reachability, CPU, memory, storage, interface traffic
  and state for core routers.
- **Sector Fleet Overview** — one row per PtMP sector: clients / throughput vs
  capacity, noise floor, worst-CPE signal, misaligned + flapping CPE counts, CPU.
  Click through to...
- **Sector Detail** — one sector: overview stats, clients/throughput/noise timeseries,
  a per-CPE table (raw signal / SNR / per-chain / drift alongside the computed
  alignment score, sorted worst-first), per-client signal+SNR graphs, 24h peak/budget
  stats, device health.
- **Capacity Planning** — 24h peak / p95 (busy-hour) / average traffic for every PTP
  link, core-router interface, and sector, in one place.
- **Subscriber Overview** — active vs provisioned sessions, active-by-POP timeline
  (mass-outage cliff), top flapping subscribers, disconnect reasons, auth-failure
  rate, per-session table.
- **Server Health** — the monitoring server itself and the stack's own health:
  host CPU/RAM/disk/network/clock, component up/down, notification failures,
  rule evaluation, WhatsApp budget.
- **WAN/LAN Interfaces** — traffic and up/down state grouped by interface role
  (WAN, LAN, PPP, wireless backhaul), using textbox variables holding per-router
  ifName regexes — check these against `wan_ifname` in
  `prometheus/targets/mikrotik-routers.yml` when adding a new router, since ifName
  conventions aren't guaranteed to match across devices.

## Notes

- Alerting is handled by Prometheus + Alertmanager, not Grafana's own alerting engine
  (see the `.gitkeep` comments under `grafana/provisioning/alerting/`).
- Sector capacity alerts (`SectorOverClientCapacity`, `SectorOverThroughputBudget`)
  don't page in real time — Alertmanager holds them and delivers one batched digest
  daily in the 08:00–08:15 Africa/Lagos window (`time_intervals` +
  `active_time_intervals` in `alertmanager/alertmanager.yml`). Real-time sector
  alerting is limited to RF/state/device problems.
- Every alert carries `severity` (critical/warning/info), `category`
  (state/health/capacity/degrading — drives routing + inhibition), `scope`
  (link/router/sector/cpe/subscriber/pop/fleet) and `alert_type` (kebab-case specific
  symptom). The NOC "Active Incidents" panel and the `incident:*` recording rules
  (`prometheus/rules/incidents.yml`) key off these.
- `POPIsolated` (`prometheus/rules/topology-rollup.yml`) fires when every backhaul
  into a POP is down and, via an `equal: [pop]` inhibit rule, silences every
  downstream sector / link / subscriber alert for that POP — one Telegram message
  naming the subscriber impact instead of a storm. `SubscriberMassOutagePOP` /
  `…Router` catch the same shape from the session side.
- `SectorWideCPEDegradation` fires when 4+ CPEs on one sector drop below alignment
  score 60, and inhibits the individual per-CPE RF alerts for that sector — a
  shared cause is one incident, not N. `CPESignalDegradedVsBaseline` / `...Rising`
  gate on `sector:cpe:baseline_ready` (≈17h+ of history) so they don't fire off a
  half-formed baseline in a sector's first day.
- Anti-storm machinery: `for:` swallows transients, `keep_firing_for:` stops
  resolve/refire flap pairs on state + flappy health alerts,
  `group_by`+`group_interval: 10m` batches per object, `inhibit_rules` suppress
  symptoms under a parent (incl. `prometheus/topology.yml` `pop:` dependency —
  a POP's backhaul/router down silences its downstream sectors). `repeat_interval`
  2 h critical / 12 h warning. `prometheus/rules/tests/alerts_test.yml`
  (`promtool test rules`) and `docs/failure-drills.md` prove it.
