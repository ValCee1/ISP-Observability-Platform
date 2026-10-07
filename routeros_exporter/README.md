# routeros-exporter

RouterOS API collector for the ISP Observability Platform. Covers the two
things RouterOS **SNMP cannot** (spelled out in
`prometheus/rules/ppp-sessions.yml` and `prometheus/topology.yml`):

| Gap | Source | Metrics |
|---|---|---|
| Per-subscriber PPP/PPPoE sessions | `/ppp/active`, `/ppp/secret`, `/log` | `routeros_ppp_active*`, `routeros_ppp_secret*`, `routeros_ppp_disconnect_total`, `routeros_ppp_auth_failure_total` |
| Topology auto-discovery | `/ip/route`, `/ip/neighbor`, `/interface` | `routeros_topology_edge`, `topology.generated.yml` |

Prometheus scrapes it on **:9436** (job `routeros-api`). It augments the SNMP
`ppp-sessions.yml` path, it does not replace it — if the API is unreachable
the SNMP-derived `ppp:session:*` series still work.

Config backup (`/export` → git) was removed on 2026-10-07 to keep this
stack purely about monitoring. It lives in git history (commits 0cbb618,
4071f1d, 96b7922) if it's ever wanted again as a separate tool.

## RouterOS side — one read-only user

Every path this exporter uses (`/ppp/*/print`, `/log/print`,
`/system/*/print`, `/ip/route/print`, `/ip/neighbor/print`,
`/interface/print`) only needs `read`:

```
/user group add name=routeros-exporter policy=api,read
/user add name=prom-ro group=routeros-exporter password=<strong> comment="routeros-exporter"
/ip service set api address=<prometheus-host>/32      # or api-ssl (8729)
```

If `prom-ro` already exists with the old `api,read,write,ftp` group (from
when config backup existed), narrow it and turn FTP back off:

```
/user group set routeros-exporter policy=api,read
/ip service disable ftp
```

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

## Confirmed against a live router (2026-09-12 through 14, RouterOS 6.49, hAP lite ×2 / hAP AC Lite)

- PPP/secret/system polling, and topology discovery via `/ip/route` +
  `/ip/neighbor`, all work as designed against real hardware.
- `client.connect()` used to ignore its timeout entirely (every connection
  silently got librouteros's 10s default). Fixed: it now takes `timeout=`,
  driven by `Config.api_timeout_seconds`.
- Retrying a command that keeps failing every ~30s poll cycle can tie up the
  router's API connections. `__main__.py` (`BACKOFF_AFTER_FAILURES`,
  `_schedule_after_attempt`) backs topology discovery off to its full
  interval after 2 consecutive misses.

## Still UNVERIFIED

- `/log` message wording for connect / disconnect / auth-failure (parser in
  `collectors/ppp.py` is deliberately permissive; no PPP sessions were active
  on the test router, so this hasn't been exercised against real log lines).
- pppoe `caller-id` format vs. the SNMP `cpe_mac` label format — the join in
  `prometheus/rules/subscriber-sessions.yml` assumes both normalise to
  lowercase `aa:bb:cc:dd:ee:ff`.
