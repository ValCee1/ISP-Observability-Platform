# ISP Network Observability Platform

A small observability stack for an ISP network: MikroTik core routers, PTP wireless
backhaul links (AirMAX, AirFiber, SAF), monitored via SNMP (routers + radios) and ICMP
(link reachability), plus a RouterOS **API** collector for what SNMP can't reach —
per-subscriber PPP/PPPoE sessions, config backup + drift, topology auto-discovery.
Visualized in Grafana, alerted on Telegram via Alertmanager.

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
  — the RouterOS API layer: subscriber intelligence, config backup + drift,
  topology auto-discovery + POP-isolation rollup.
- [routeros_exporter/README.md](routeros_exporter/README.md) — the API collector
  itself (metrics, the read-only RouterOS user, `UNVERIFIED` list).
- [docs/DOCKER.md](docs/DOCKER.md) — building and publishing the
  `isp-observability` Prometheus image (repo config baked in) to a private
  registry.
- [docs/failure-drills.md](docs/failure-drills.md) — end-to-end failure runbook
  (detect → alert → explain → recover → no storm).

## Running the stack

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
- **Subscribers + config + topology** (`routeros_exporter/`, job `routeros-api`): the
  RouterOS API collector. Per-subscriber PPP/PPPoE sessions from `/ppp/active` +
  `/ppp/secret` + `/log` (caller-id, real uptime, disconnect reason,
  provisioned-vs-online, mass-outage detection); config backup + drift (`/export`
  committed to a git repo, `routeros_config_changed` on every diff); topology
  auto-discovery (`/ip/route` + `/ip/neighbor`) that finally activates the `pop`
  dependency-inhibition machinery. Details:
  [docs/tier1-subscriber-config-topology.md](docs/tier1-subscriber-config-topology.md).
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
- **Config Audit** — last-backup age per router, config-change spikes, most-recent
  change timestamp, RouterOS versions across the fleet.
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
