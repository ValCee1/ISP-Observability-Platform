"""Entry point: poll every router on a timer, serve /metrics.

    python -m routeros_exporter            # run the exporter
    python -m routeros_exporter --check    # validate config + credentials, exit
    python -m routeros_exporter --once     # one poll cycle to stdout, exit

Design: a single background thread per concern is overkill for a handful of
routers, so one loop does everything, with independent "next run" clocks for
the fast PPP/system poll and the slower topology discovery.
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
from .collectors import ppp as ppp_mod
from .collectors.system import parse_system
from .metrics import Metrics

log = logging.getLogger("routeros_exporter")


@dataclasses.dataclass(frozen=True)
class PollOutcome:
    """What actually happened this cycle, per concern - `run()` uses each
    field independently to decide whether that concern's schedule slot was
    consumed. `topology_ok` defaults to True ("nothing to retry") so a
    concern that wasn't attempted this cycle doesn't look like a failure.
    """

    ok: bool                # core poll (PPP + system) - routeros_scrape_success
    topology_ok: bool = True


def poll_router(c: cfg.RouterConfig, conf: cfg.Config, m: Metrics, *, do_topology: bool, router_addresses=None) -> PollOutcome:
    """One poll cycle for one router. See `PollOutcome` for the return value.

    `routeros_scrape_success` reflects only the CORE poll (PPP + system) -
    the signal RouterOSAPICollectorDown alerts on. Topology discovery is
    wrapped in its own try/except and never flips it: a missing
    `/ip/neighbor` table on a device that doesn't run MNDP/LLDP shouldn't
    make the subscriber pipeline look down.
    """
    started = time.monotonic()
    ok = True
    topology_ok = True
    try:
        with roc.connect(c, timeout=conf.api_timeout_seconds) as api:
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
    return PollOutcome(ok=ok, topology_ok=topology_ok)


def _safe(api, path: str):
    try:
        return api.query(path)
    except roc.RouterOSError:
        return []


# After this many consecutive failed topology attempts on one router, stop
# retrying every poll cycle and fall back to the full topology interval -
# a command that fails the same way every time shouldn't be hammered.
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
    router_addresses = {r.name: [r.host] for r in conf.routers}
    for c in conf.routers:
        m.set_router_info(c.name, c.site, c.pop, c.role)

    start_http_server(conf.listen_port, registry=registry)
    log.info("listening on :%d, %d routers", conf.listen_port, len(conf.routers))

    # Per-router topology clocks: a router that's briefly unreachable when
    # its window comes up retries on its own next poll cycle instead of
    # missing the whole interval. Every router starts at 0.0 so the first
    # cycle after startup always attempts discovery.
    next_topology: dict[str, float] = {}
    topology_fail_streak: dict[str, int] = {}

    while True:
        now = time.time()
        for c in conf.routers:
            do_topology = now >= next_topology.get(c.name, 0.0)
            outcome = poll_router(
                c, conf, m,
                do_topology=do_topology, router_addresses=router_addresses,
            )
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
        for c in conf.routers:
            m.set_router_info(c.name, c.site, c.pop, c.role)
            poll_router(c, conf, m, do_topology=True,
                        router_addresses={r.name: [r.host] for r in conf.routers})
        sys.stdout.write(generate_latest(registry).decode())
        return 0

    run(conf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
