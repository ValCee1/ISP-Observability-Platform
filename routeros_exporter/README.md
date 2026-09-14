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

## RouterOS side — one user, scoped to exactly what it needs

Every read path (`/ppp/*/print`, `/log/print`, `/system/*/print`,
`/ip/route/print`, `/ip/neighbor/print`, `/interface/print`) only ever
needs `read`. Config backup is the one exception: RouterOS 6.x's API can't
stream `/export` output to a read-only user at all (see CONFIRMED below),
and even with `write` it can't hand back a file's contents on that ROS
version - only FTP can. So the user gets a **custom group**, not the
built-in `write` group, scoped to exactly `api, read, write, ftp` - never
`password`, `sensitive`, `policy`, `reboot`, or `sniff`:

```
/user group add name=routeros-exporter policy=api,read,write,ftp
/user add name=prom-ro group=routeros-exporter password=<strong> comment="routeros-exporter"
/ip service set api address=<prometheus-host>/32      # or api-ssl (8729)
/ip service enable ftp                                 # CONFIRMED 2026-09-14:
/ip service set ftp address=<prometheus-host>/32       # `set address=` alone does NOT
                                                        # enable a disabled service
```

(If `prom-ro` already exists from before this policy existed:
`/user set prom-ro group=routeros-exporter` - same username/password, no
credentials-file change needed.)

This means a compromised exporter (or a bug in it) could create, overwrite,
or delete files on your routers - it cannot change passwords, reboot,
touch firewall/routing policy, or read other users' saved passwords. If
you'd rather keep it strictly read-only instead, the alternative considered
and explicitly not chosen (2026-09-13) was a RouterOS-side scheduler
running `/export` locally on each router (no network credential needed to
create the file) paired with an FTP-only, permanently read-only fetch
credential. Revisit that if the wider policy above becomes a concern.

**The backup content itself is sensitive.** CONFIRMED 2026-09-14: RouterOS
6.x's `/export` does not mask passwords/secrets by default - a live export
came back with a WiFi PSK in plain text. Treat the backup git repo
(`routeros_exporter_data` volume) like `secrets/`: never commit it, don't
expose the volume, restrict who can read it.

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
- A bare `/export` over the API does not return AT ALL on this ROS 6.x
  device with a read-only user (no console/pager over the API - it just
  hangs).
- RouterOS 6.x's API has no way to read a file's contents back (only newer
  7.x builds added that) - fetching the export text needs FTP, hence the
  `ftp` policy above.
- `show-sensitive` only exists on RouterOS 7.x - a 6.49 router rejects it
  as an "unknown parameter", so `export_via_api` doesn't pass it.
  **CORRECTION 2026-09-14**: this does NOT mean 6.x masks secrets some
  other way - a live export came back with a WiFi PSK in **plain text**.
  RouterOS 6.x's `/export` doesn't hide sensitive values by default at
  all. **Treat the backup git repo/volume as holding real credentials**,
  not just structural config - same handling as `secrets/`: never commit
  it, don't expose the volume, restrict who can read it.
- **`export_via_api` (2026-09-13) uses the widened `api, read, write, ftp`
  group above: `/export file=...` over the API, then FTP fetch + delete.
  CONFIRMED WORKING END-TO-END 2026-09-14** against pop1-home (a hAP AC
  Lite) - `routeros_config_backup_success=1`, a real 110-line export
  committed to git. Getting there took two more fixes than expected:
  - **A real bug, not a hardware ceiling**: `client.connect()` never
    accepted or passed through a `timeout` at all - every connection
    silently got librouteros's 10s default no matter what
    `Config.api_timeout_seconds` said, so `/export file=...` (which
    genuinely takes ~52s on this device) was doomed regardless of
    permissions. An initial test that looked like "replies in under 2s
    with `write`" was misleading: that was RouterOS failing the
    *permission check* fast, before ever attempting the real export.
    Fixed: `connect()` now takes `timeout=`, and backup gets its own
    connection on a separate, longer `Config.backup_timeout_seconds`
    (90s default) so a slow backup never delays routine polling's
    dead-router detection.
  - **FTP has to actually be enabled** (`/ip ip service enable ftp` +
    address restriction) - obvious in hindsight, easy to miss since the
    API-side symptom (a hang) looked unrelated to FTP entirely.
  - **CONFIRMED 2026-09-13/14, separately**: retrying a failed backup
    every ~30s poll cycle (safe for a *transient* miss) is actively harmful
    against a command that keeps failing the same way - it kept a hung API
    connection in flight almost continuously and was enough, on this
    device, to break routine PPP/system polling too. Fixed in
    `__main__.py` (`BACKOFF_AFTER_FAILURES`, `_schedule_after_attempt`):
    after 2 consecutive misses, back off to the full hourly interval
    instead of hammering. Verified live: exactly 2 attempts, then silence.
  - Config-backup failures stay isolated in `__main__.poll_router` and
    never affect `routeros_scrape_success` / PPP data, success or not.

## Still UNVERIFIED

- `/log` message wording for connect / disconnect / auth-failure (parser in
  `collectors/ppp.py` is deliberately permissive; no PPP sessions were active
  on the test router, so this hasn't been exercised against real log lines).
- pppoe `caller-id` format vs. the SNMP `cpe_mac` label format — the join in
  `prometheus/rules/subscriber-sessions.yml` assumes both normalise to
  lowercase `aa:bb:cc:dd:ee:ff`.
