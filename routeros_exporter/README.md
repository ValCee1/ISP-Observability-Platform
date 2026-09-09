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

## UNVERIFIED — check against a live router

- `/log` message wording for connect / disconnect / auth-failure (parser in
  `collectors/ppp.py` is deliberately permissive; confirm the ROS 7.x text).
- pppoe `caller-id` format vs. the SNMP `cpe_mac` label format — the join in
  `prometheus/rules/subscriber-sessions.yml` assumes both normalise to
  lowercase `aa:bb:cc:dd:ee:ff`.
- `/export` row shape from librouteros (single blob vs. per-line rows) —
  `backup.export_via_api` handles both, but confirm which this ROS returns.
- MNDP/LLDP enabled on sector/backhaul interfaces for neighbour discovery.
