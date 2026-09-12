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


def poll_router(c: cfg.RouterConfig, conf: cfg.Config, m: Metrics, *, do_backup: bool, do_topology: bool, repo=None, router_addresses=None) -> None:
    """One poll cycle for one router.

    `routeros_scrape_success` reflects only the CORE poll (PPP + system) -
    the signal RouterOSAPICollectorDown alerts on. Config backup and
    topology discovery are wrapped in their own try/except and never flip
    it: a `/export` hiccup (CONFIRMED 2026-09-12: RouterOS 6.x rejects the
    `show-sensitive` parameter 7.x accepts - see backup.export_via_api) or a
    missing `/ip/neighbor` table on a device that doesn't run MNDP/LLDP
    shouldn't make the subscriber pipeline look down. Backup failures still
    surface via `routeros_config_backup_success` (RouterBackupFailing).
    """
    started = time.monotonic()
    ok = True
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
                    result = back_up_router(c.name, export_via_api(api), repo)
                except roc.RouterOSError as exc:
                    # API-level failure (timeout, unsupported param, ...) -
                    # turn it into the same BackupResult shape a git-commit
                    # failure would produce, so there's exactly one log line
                    # below regardless of which step failed.
                    result = BackupResult(c.name, False, False, 0, "", error=str(exc))
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
    except roc.RouterOSError as exc:
        ok = False
        log.warning("poll %s failed: %s", c.name, exc)
    finally:
        m.update_scrape(c.name, ok, time.monotonic() - started)


def _safe(api, path: str):
    try:
        return api.query(path)
    except roc.RouterOSError:
        return []


def run(conf: cfg.Config) -> None:
    registry = CollectorRegistry()
    m = Metrics(registry)
    repo = ensure_repo(conf.backup_repo_path)
    router_addresses = {r.name: [r.host] for r in conf.routers}
    for c in conf.routers:
        m.set_router_info(c.name, c.site, c.pop, c.role)

    start_http_server(conf.listen_port, registry=registry)
    log.info("listening on :%d, %d routers", conf.listen_port, len(conf.routers))

    next_backup = 0.0
    next_topology = 0.0
    while True:
        now = time.time()
        do_backup = now >= next_backup
        do_topology = now >= next_topology
        for c in conf.routers:
            poll_router(
                c, conf, m,
                do_backup=do_backup, do_topology=do_topology,
                repo=repo, router_addresses=router_addresses,
            )
        if do_backup:
            next_backup = now + conf.backup_interval_seconds
        if do_topology:
            next_topology = now + conf.topology_interval_seconds
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
