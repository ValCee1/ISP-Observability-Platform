# ISP Network Observability Platform

A small observability stack for an ISP network: MikroTik core routers, PTP wireless
backhaul links (AirMAX, AirFiber, SAF), monitored via SNMP (routers + radios) and ICMP
(link reachability), visualized in Grafana, alerted on Telegram via Alertmanager.

```
Routers/Radios --SNMP--> snmp-exporter --\
Links          --ICMP--> blackbox-exporter -> Prometheus -> Grafana
                                             -> Alertmanager -> Telegram
```

## Running the stack

1. Fill in the two secret files (both gitignored, not committed):
   - `secrets/grafana_admin_password.txt` — Grafana admin password
   - `secrets/telegram_bot_token.txt` — Telegram bot token used by Alertmanager
2. Start everything:

   ```bash
   docker compose up -d
   ```

- Grafana: http://localhost:3000 (user: `admin`, password from the secret file above)
- Prometheus: http://localhost:9090
- Alertmanager: http://localhost:9093
- SNMP exporter: http://localhost:9116/metrics
- Blackbox exporter: http://localhost:9115/metrics

## What's monitored

- **Core routers** (`prometheus/targets/mikrotik-routers.yml`): CPU, memory/storage,
  uptime, interface state/traffic/errors via the generic `mikrotik` SNMP module.
- **PTP links** (`prometheus/targets/links.yml`): each link is two SNMP+ICMP-monitored
  radio endpoints (see `prometheus/rules/link-schema-notes.md` for the normalized
  `link_*` metric schema shared across vendors — AirMAX, AirFiber, SAF, MikroTik radios).
- Recording rules (`prometheus/rules/link-normalization.yml`) turn raw per-vendor SNMP
  metrics into vendor-agnostic `link_*` series and a single `link_health_score` per link.
- Alerting rules (`prometheus/rules/link-alerts.yml`, `mikrotik-alerts.yml`) cover both
  hard state (link/router down) and soft degradation (weak signal, rising errors,
  "up but unhealthy"), routed and grouped per-entity in `alertmanager/alertmanager.yml`
  to avoid Telegram notification spam, with inhibition rules so a big outage doesn't
  also fire all its symptom alerts separately.

## Dashboards (`grafana/dashboards/`, auto-provisioned)

- **NOC Overview** — the front door: link counts by state + a color-coded table of
  every link's health score, linking through to...
- **PTP Link Monitoring** — drill-down for one link: status, latency, packet loss,
  RF signal/noise/quality, capacity/utilization, vendor-specific diagnostics.
- **MikroTik Router Overview** — reachability, CPU, memory, storage, interface traffic
  and state for core routers.
- **WAN/LAN Interfaces** — traffic and up/down state grouped by interface role
  (WAN, LAN, PPP, wireless backhaul), using textbox variables holding per-router
  ifName regexes — check these against `wan_ifname` in
  `prometheus/targets/mikrotik-routers.yml` when adding a new router, since ifName
  conventions aren't guaranteed to match across devices.

## Notes

- Alerting is handled by Prometheus + Alertmanager, not Grafana's own alerting engine
  (see the `.gitkeep` comments under `grafana/provisioning/alerting/`).
- `snmp_exporter/snmp.yml` also defines a `mikrotik_wireless_link` module for sector/CPE
  wireless telemetry, but it isn't wired into any job/target yet — add a targets file
  and job pointing at the relevant IPs to start collecting it.
