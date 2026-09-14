"""Entry point: poll every router on a timer, serve /metrics.

    python -m routeros_exporter            # run the exporter
    python -m routeros_exporter --check    # validate config + credentials, exit
    python -m routeros_exporter --once     # one poll cycle to stdout, exit

Design: a single background thread per concern is overkill for a handful of
routers, so one loop does everything, with independent "next run" clocks for
the fast PPP/system poll, the slower config backup, and the slowest topology
discovery.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
import time

from prometheus_client import CollectorRegistry, generate_latest, start_http_server

from . import client as roc
from . import config as cfg
from . import topology as topo
from .backup import BackupResult, back_up_router, ensure_repo, export_via_api
from .collectors import ppp as ppp_mod
from .collectors.system import parse_system
from .metrics import Metrics

log = logging.getLogger("routeros_exporter")


@dataclasses.dataclass(frozen=True)
class PollOutcome:
    """What actually happened this cycle, per concern - `run()` uses each
    field independently to decide whether that concern's schedule slot was
    consumed. Collapsing this to one bool was the bug CONFIRMED 2026-09-13:
    pop1-home's core poll (`ok`) can succeed while its backup step times out
    (the router being briefly slow/loaded, not a permissions problem) - if
    `next_backup` only checked `ok`, that one timeout burned the router's
    ENTIRE hourly backup window even though the very next 30s cycle would
    likely have succeeded. Each field defaults to True ("nothing to retry")
    so a concern that wasn't even attempted this cycle (`do_backup=False`,
    or `collect_backup=False`) doesn't look like a failure.
    """

    ok: bool                # core poll (PPP + system) - routeros_scrape_success
    backup_ok: bool = True
    topology_ok: bool = True


def poll_router(c: cfg.RouterConfig, conf: cfg.Config, m: Metrics, *, do_backup: bool, do_topology: bool, repo=None, router_addresses=None) -> PollOutcome:
    """One poll cycle for one router. See `PollOutcome` for what the return
    value means and why it isn't just one bool.

    `routeros_scrape_success` reflects only the CORE poll (PPP + system) -
    the signal RouterOSAPICollectorDown alerts on. Config backup and
    topology discovery are wrapped in their own try/except and never flip
    it: a `/export` hiccup or a missing `/ip/neighbor` table on a device
    that doesn't run MNDP/LLDP shouldn't make the subscriber pipeline look
    down. Backup failures still surface via `routeros_config_backup_success`
    (RouterBackupFailing).
    """
    started = time.monotonic()
    ok = True
    backup_ok = True
    topology_ok = True
    try:
        with roc.connect(c) as api:
            if c.collect_ppp:
                active = ppp_mod.parse_active(api.query("/ppp/active/print"))
                secrets = ppp_mod.parse_secrets(api.query("/ppp/secret/print"))
                log_rows = api.query("/log/print")[-conf.log_lookback:]
                events = ppp_mod.parse_log_events(log_rows)
                m.update_ppp(c.name, active, secrets, events, log_rows)

            info = parse_system(
                api.query("/system/resource/print"),
                _safe(api, "/system/routerboard/print"),
            )
            m.update_system(c.name, info)

            if do_backup and c.collect_backup and repo is not None:
                try:
                    result = back_up_router(
                        c.name,
                        export_via_api(api, c, ftp_timeout=conf.api_timeout_seconds),
                        repo,
                    )
                except roc.RouterOSError as exc:
                    # API-level failure (timeout, unsupported param, ...) -
                    # turn it into the same BackupResult shape a git-commit
                    # failure would produce, so there's exactly one log line
                    # below regardless of which step failed. Marks the
                    # attempt itself as not-consumed (see PollOutcome) so
                    # run() retries next cycle instead of waiting an hour.
                    result = BackupResult(c.name, False, False, 0, "", error=str(exc))
                    backup_ok = False
                m.update_backup(result)
                if result.error:
                    log.warning("backup %s: %s", c.name, result.error)

            if do_topology and c.collect_topology:
                try:
                    gws = topo.parse_default_gateways(api.query("/ip/route/print"))
                    neighbors = topo.parse_neighbors(_safe(api, "/ip/neighbor/print"))
                    edges = topo.build_edges(
                        c.name, gws, neighbors,
                        router_addresses=router_addresses or {},
                    )
                    m.update_topology(c.name, edges)
                except roc.RouterOSError as exc:
                    log.warning("topology %s: %s", c.name, exc)
                    topology_ok = False
    except roc.RouterOSError as exc:
        ok = False
        log.warning("poll %s failed: %s", c.name, exc)
    finally:
        m.update_scrape(c.name, ok, time.monotonic() - started)
    return PollOutcome(ok=ok, backup_ok=backup_ok, topology_ok=topology_ok)


def _safe(api, path: str):
    try:
        return api.query(path)
    except roc.RouterOSError:
        return []


# After this many consecutive failed attempts at one concern (backup or
# topology) on one router, stop retrying every poll cycle and fall back to
# the full hourly interval - see run()'s docstring-comment for why.
BACKOFF_AFTER_FAILURES = 2


def _schedule_after_attempt(streak: int, succeeded: bool) -> tuple[bool, int]:
    """Pure scheduling decision for one concern on one router, given its
    previous consecutive-failure streak and whether this attempt succeeded.

    Returns (advance_schedule, new_streak). `advance_schedule=True` means
    the caller should push that concern's next-attempt clock a full
    interval out; False means leave it alone so the very next poll cycle
    retries - the fast path for a genuinely transient miss, not a
    persistently broken command.
    """
    if succeeded:
        return True, 0
    streak += 1
    return streak >= BACKOFF_AFTER_FAILURES, streak


def run(conf: cfg.Config) -> None:
    registry = CollectorRegistry()
    m = Metrics(registry)
    repo = ensure_repo(conf.backup_repo_path)
    router_addresses = {r.name: [r.host] for r in conf.routers}
    for c in conf.routers:
        m.set_router_info(c.name, c.site, c.pop, c.role)

    start_http_server(conf.listen_port, registry=registry)
    log.info("listening on :%d, %d routers", conf.listen_port, len(conf.routers))

    # Per-router clocks, not one shared clock: a router that's briefly
    # unreachable exactly when its backup/topology window comes up retries
    # on its own next 30s poll cycle instead of silently missing the whole
    # hour (see poll_router's docstring). Every router starts at 0.0 so the
    # first cycle after startup always attempts both, same as before.
    next_backup: dict[str, float] = {}
    next_topology: dict[str, float] = {}
    # CONFIRMED 2026-09-14, live, against a hAP AC Lite: retrying a failed
    # backup every cycle is only safe for a TRANSIENT miss. A command that
    # fails the same way every single time (that router's `/export
    # file=...` hung the full timeout on every attempt, healthy or not) got
    # retried every ~30s and kept a hung API connection in flight almost
    # continuously - which exhausted the router's API connections badly
    # enough to break its routine PPP/system polling too. After a couple of
    # consecutive misses, back off to the full hourly interval instead of
    # hammering it - still surfaces promptly via RouterBackupFailing/Stale,
    # just without making things worse. Resets to fast-retry the moment a
    # backup actually succeeds, so a genuine transient blip still recovers
    # quickly.
    backup_fail_streak: dict[str, int] = {}
    topology_fail_streak: dict[str, int] = {}

    while True:
        now = time.time()
        for c in conf.routers:
            do_backup = now >= next_backup.get(c.name, 0.0)
            do_topology = now >= next_topology.get(c.name, 0.0)
            outcome = poll_router(
                c, conf, m,
                do_backup=do_backup, do_topology=do_topology,
                repo=repo, router_addresses=router_addresses,
            )
            if do_backup and outcome.ok:
                advance, backup_fail_streak[c.name] = _schedule_after_attempt(
                    backup_fail_streak.get(c.name, 0), outcome.backup_ok
                )
                if advance:
                    next_backup[c.name] = now + conf.backup_interval_seconds
            if do_topology and outcome.ok:
                advance, topology_fail_streak[c.name] = _schedule_after_attempt(
                    topology_fail_streak.get(c.name, 0), outcome.topology_ok
                )
                if advance:
                    next_topology[c.name] = now + conf.topology_interval_seconds
        time.sleep(conf.poll_interval_seconds)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="routeros_exporter")
    ap.add_argument("--check", action="store_true", help="validate config, exit")
    ap.add_argument("--once", action="store_true", help="one poll cycle to stdout, exit")
    ap.add_argument("--config", default=None)
    ap.add_argument("--credentials", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    conf = cfg.load(args.config, args.credentials)
    if args.check:
        for c in conf.routers:
            status = "creds ok" if (c.username and c.password) else "MISSING creds"
            print(f"  {c.name:20s} {c.host:16s} pop={c.pop or '-':8s} {status}")
        print(f"{len(conf.routers)} routers, listen :{conf.listen_port}")
        return 0

    if args.once:
        registry = CollectorRegistry()
        m = Metrics(registry)
        repo = ensure_repo(conf.backup_repo_path)
        for c in conf.routers:
            m.set_router_info(c.name, c.site, c.pop, c.role)
            poll_router(c, conf, m, do_backup=True, do_topology=True, repo=repo,
                        router_addresses={r.name: [r.host] for r in conf.routers})
        sys.stdout.write(generate_latest(registry).decode())
        return 0

    run(conf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
