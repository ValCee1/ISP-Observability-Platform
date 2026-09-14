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
/ip service set ftp address=<prometheus-host>/32
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
- `show-sensitive` (explicit sensitive-value masking) only exists on
  RouterOS 7.x - a 6.49 router rejects it as an "unknown parameter", so
  `export_via_api` doesn't pass it; `/export`'s default masking still
  applies either way.
- **`export_via_api` (2026-09-13) uses the widened `api, read, write, ftp`
  group above: `/export file=...` over the API, then FTP fetch + delete.**
  This is real and correctly implemented, but on this specific test
  hardware (a **hAP AC Lite** - a modest single-core device) it does not
  actually complete:
  - **CONFIRMED 2026-09-14, at 2am with the router otherwise idle and
    healthy (2ms ping, 0% loss)**: `/export file=...` still hung for the
    full timeout (tested up to 90s) even with `write` granted. This isn't
    a permissions problem or ordinary load - it looks like this ROS
    6.49/hAP-AC-Lite combination just can't complete `/export` over the
    API at all, at least not with `file=` either. (An earlier quick test
    that appeared to "reply in under 2s" was actually RouterOS failing the
    *permission check* fast, before ever attempting the export - it told
    us nothing about whether the export itself would complete once
    permission was granted.)
  - **CONFIRMED 2026-09-13/14**: retrying that every ~30s poll cycle (safe
    for a *transient* miss) is actively harmful against a command that
    fails the same way *every* time - it keeps a hung API connection in
    flight almost continuously, which exhausted this router's API
    connections badly enough to break its routine PPP/system polling too.
    Fixed in `__main__.py` (`BACKOFF_AFTER_FAILURES`,
    `_schedule_after_attempt`): after 2 consecutive failures, back off to
    the full hourly interval instead of hammering it. A router that
    recovers still gets picked up fast (2 quick tries before backing off);
    a router where the command is simply unsupported settles into one
    gentle attempt an hour, forever - `RouterBackupFailing` reflects that
    correctly, without making the router worse.
  - Config-backup failures stay isolated in `__main__.poll_router` either
    way and never affect `routeros_scrape_success` / PPP data.
  - **Open question, not yet answered**: does `/export` (with or without
    `file=`) work over the API on stronger hardware, or on RouterOS 7.x?
    The user's guidance (2026-09-13): production routers/radios are more
    robust than this test box, so treat this as unresolved for THIS device
    rather than a verdict on the whole design - re-test on real production
    hardware before concluding Option A doesn't work at all. If it turns
    out this is a broader RouterOS 6.x/API limitation, Option B (a
    RouterOS-side scheduler running `/export` locally, no live API call
    involved) sidesteps it entirely - see the section above.

## Still UNVERIFIED

- `/log` message wording for connect / disconnect / auth-failure (parser in
  `collectors/ppp.py` is deliberately permissive; no PPP sessions were active
  on the test router, so this hasn't been exercised against real log lines).
- pppoe `caller-id` format vs. the SNMP `cpe_mac` label format — the join in
  `prometheus/rules/subscriber-sessions.yml` assumes both normalise to
  lowercase `aa:bb:cc:dd:ee:ff`.
