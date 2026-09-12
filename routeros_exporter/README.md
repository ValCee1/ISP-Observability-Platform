# routeros-exporter

RouterOS API collector for the ISP Observability Platform. Covers the three
things RouterOS **SNMP cannot** (spelled out in
`prometheus/rules/ppp-sessions.yml` and `prometheus/topology.yml`):

| Gap | Source | Metrics |
|---|---|---|
| Per-subscriber PPP/PPPoE sessions | `/ppp/active`, `/ppp/secret`, `/log` | `routeros_ppp_active*`, `routeros_ppp_secret*`, `routeros_ppp_disconnect_total`, `routeros_ppp_auth_failure_total` |
| Config backup + drift | `/export` → git | `routeros_config_changed`, `routeros_config_last_backup_timestamp`, `routeros_config_export_lines` |
| Topology auto-discovery | `/ip/route`, `/ip/neighbor`, `/interface` | `routeros_topology_edge`, `topology.generated.yml` |

Prometheus scrapes it on **:9436** (job `routeros-api`). It augments the SNMP
`ppp-sessions.yml` path, it does not replace it — if the API is unreachable
the SNMP-derived `ppp:session:*` series still work.

## RouterOS side — one read-only user

```
/user add name=prom-ro group=read password=<strong> comment="routeros-exporter (read-only)"
/ip service set api address=<prometheus-host>/32          # or api-ssl (8729)
```

The `read` group cannot change config; every API path used is read-only
(`/ppp/*/print`, `/log/print`, `/system/*/print`, `/ip/route/print`,
`/ip/neighbor/print`, `/interface/print`, `/export show-sensitive=no`).

## Configure

1. `cp config.example.yml config.yml` — router list + labels (`pop`, `site`,
   `role` should match `prometheus/targets/*.yml`).
2. `cp routeros_api_credentials.example.json secrets/routeros_api_credentials.json`
   (the `secrets/` dir is gitignored) — `{ "<router-name>": {"username", "password"} }`.
3. `docker compose up -d routeros-exporter`

`docker compose run --rm routeros-exporter --check` validates config +
credentials without polling. `--once` prints one scrape to stdout.

## Develop

```
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest
```

Collectors (`routeros_exporter/collectors/`) are pure functions over the
list-of-dicts the API returns, tested against `tests/fixtures/*.json`
(captured API output). Anything that could only be confirmed on real
hardware is marked `UNVERIFIED - needs live device`.

## Confirmed against a live router (2026-09-12, RouterOS 6.49, hAP lite ×2)

- PPP/secret/system polling, and topology discovery via `/ip/route` +
  `/ip/neighbor`, all work as designed against real hardware.
- `/export` needs the `show-sensitive` fallback (`export_via_api` retries
  without it - ROS 6.x rejects the parameter, ROS 7.x accepts it).
- **Config backup needs a write-capable RouterOS user, contradicting the
  "read-only API user" this component was designed around.** A bare
  `/export` over the API does not return on this ROS 6.x device (times out
  - no console/pager over the API); `/export file=<name>` replies
  immediately but requires the `write` policy to create the file, which the
  `read` group's user correctly does not have (`not enough permissions`).
  `routeros_scrape_success`/PPP data are unaffected (config-backup failures
  are isolated in `__main__.poll_router` and only surface as
  `routeros_config_backup_success=0` / `RouterBackupFailing`) - **until this
  is redesigned, `RouterBackupFailing`/`RouterBackupStale` will fire for
  every router and should be treated as a known gap, not a real incident.**
  Options going forward (not yet decided): a second, `write`-scoped
  credential used only for backups; or fetch the exported file over FTP
  instead of the API; or drop the read-only requirement for this one
  feature and document the tradeoff explicitly.

## Still UNVERIFIED

- `/log` message wording for connect / disconnect / auth-failure (parser in
  `collectors/ppp.py` is deliberately permissive; no PPP sessions were active
  on the test router, so this hasn't been exercised against real log lines).
- pppoe `caller-id` format vs. the SNMP `cpe_mac` label format — the join in
  `prometheus/rules/subscriber-sessions.yml` assumes both normalise to
  lowercase `aa:bb:cc:dd:ee:ff`.
