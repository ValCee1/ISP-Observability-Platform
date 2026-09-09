"""Prometheus metric definitions + the per-poll update logic.

The collectors (``collectors/*.py``) do the parsing; this module owns the
metric objects and translates parsed structures into gauge/counter updates.
Kept separate so the parsing stays network- and Prometheus-free and the
metric surface is reviewable in one file.

Metric surface (all prefixed ``routeros_``):

  ppp_active{router,user,service}                     1 while connected
  ppp_active_uptime_seconds{router,user,service}      current session uptime
  ppp_active_caller_id_info{router,user,caller_id,address}  1  (join helper:
        pppoe caller_id is the client MAC -> maps to a sector CPE)
  ppp_active_total{router}                            gauge
  ppp_secret{router,user,profile,service}             1, or 0 if disabled
  ppp_secret_total{router}                            gauge
  ppp_disconnect_total{router,user,reason}            counter
  ppp_auth_failure_total{router}                      counter
  ppp_connect_total{router,user}                      counter

  config_last_backup_timestamp{router}               gauge (unix seconds)
  config_last_change_timestamp{router}               gauge
  config_changed{router}                             1 for the cycle a diff landed
  config_export_lines{router}                        gauge
  config_backup_success{router}                      1/0

  system_info{router,version,board,architecture}     1
  system_uptime_seconds{router}                      gauge

  topology_edge{router,peer,type,via_interface}      1

  scrape_success{router}                             1/0 for the whole poll
  scrape_duration_seconds{router}                    gauge
  build_info{version}                                1
"""

from __future__ import annotations

import time
from typing import Iterable

from prometheus_client import CollectorRegistry, Counter, Gauge

from . import __version__
from .collectors import ppp as ppp_mod
from .collectors.system import SystemInfo
from .topology import Edge


class Metrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        r = registry or CollectorRegistry()
        self.registry = r

        self.ppp_active = Gauge(
            "routeros_ppp_active", "1 while a PPP session is connected",
            ["router", "user", "service"], registry=r,
        )
        self.ppp_active_uptime = Gauge(
            "routeros_ppp_active_uptime_seconds", "Current PPP session uptime",
            ["router", "user", "service"], registry=r,
        )
        self.ppp_caller_id = Gauge(
            "routeros_ppp_active_caller_id_info",
            "Join helper: pppoe caller_id (client MAC) + assigned address",
            ["router", "user", "caller_id", "address"], registry=r,
        )
        self.ppp_active_total = Gauge(
            "routeros_ppp_active_total", "Active PPP sessions on the router",
            ["router"], registry=r,
        )
        self.ppp_secret = Gauge(
            "routeros_ppp_secret", "1 if the account is enabled, 0 if disabled",
            ["router", "user", "profile", "service"], registry=r,
        )
        self.ppp_secret_total = Gauge(
            "routeros_ppp_secret_total", "Provisioned PPP accounts",
            ["router"], registry=r,
        )
        self.ppp_disconnect_total = Counter(
            "routeros_ppp_disconnect_total", "PPP disconnect events by reason",
            ["router", "user", "reason"], registry=r,
        )
        self.ppp_auth_failure_total = Counter(
            "routeros_ppp_auth_failure_total", "PPP authentication failures",
            ["router"], registry=r,
        )
        self.ppp_connect_total = Counter(
            "routeros_ppp_connect_total", "PPP connect events",
            ["router", "user"], registry=r,
        )

        self.config_last_backup_ts = Gauge(
            "routeros_config_last_backup_timestamp",
            "Unix time of the last successful config export", ["router"], registry=r,
        )
        self.config_last_change_ts = Gauge(
            "routeros_config_last_change_timestamp",
            "Unix time the config text last changed", ["router"], registry=r,
        )
        self.config_changed = Gauge(
            "routeros_config_changed",
            "1 for the poll cycle in which a config diff was committed",
            ["router"], registry=r,
        )
        self.config_export_lines = Gauge(
            "routeros_config_export_lines", "Line count of the last /export",
            ["router"], registry=r,
        )
        self.config_backup_success = Gauge(
            "routeros_config_backup_success", "1 if the last backup cycle succeeded",
            ["router"], registry=r,
        )

        self.router_info = Gauge(
            "routeros_router_info",
            "Static per-router labels (value always 1). Join target so the "
            "routeros_* series pick up pop/site/role without carrying them "
            "on every metric: `... * on(router) group_left(pop,site) routeros_router_info`.",
            ["router", "site", "pop", "role"], registry=r,
        )

        self.system_info = Gauge(
            "routeros_system_info", "RouterOS version / board (value always 1)",
            ["router", "version", "board", "architecture"], registry=r,
        )
        self.system_uptime = Gauge(
            "routeros_system_uptime_seconds", "Router uptime", ["router"], registry=r,
        )

        self.topology_edge = Gauge(
            "routeros_topology_edge", "Discovered topology edge (value always 1)",
            ["router", "peer", "type", "via_interface"], registry=r,
        )

        self.scrape_success = Gauge(
            "routeros_scrape_success", "1 if the last poll of this router succeeded",
            ["router"], registry=r,
        )
        self.scrape_duration = Gauge(
            "routeros_scrape_duration_seconds", "Duration of the last poll",
            ["router"], registry=r,
        )
        Gauge(
            "routeros_exporter_build_info", "Exporter build info (value always 1)",
            ["version"], registry=r,
        ).labels(version=__version__).set(1)

        # De-dupe state for the log-derived counters: RouterOS is re-read
        # every cycle over a sliding window, so we must only count each log
        # line once. Bounded per router.
        self._seen_log_ids: dict[str, set[str]] = {}

    # -- PPP -----------------------------------------------------------------
    def update_ppp(
        self,
        router: str,
        active: list[ppp_mod.ActiveSession],
        secrets: list[ppp_mod.Secret],
        events: list[ppp_mod.PppEvent],
        raw_log_rows: Iterable[dict] | None = None,
    ) -> None:
        # Clear then re-set the per-session gauges so a vanished session drops.
        self._clear_router(self.ppp_active, router)
        self._clear_router(self.ppp_active_uptime, router)
        self._clear_router(self.ppp_caller_id, router)
        for s in active:
            self.ppp_active.labels(router, s.user, s.service).set(1)
            self.ppp_active_uptime.labels(router, s.user, s.service).set(s.uptime_seconds)
            self.ppp_caller_id.labels(router, s.user, s.caller_id, s.address).set(1)
        self.ppp_active_total.labels(router).set(len(active))

        self._clear_router(self.ppp_secret, router)
        for sec in secrets:
            self.ppp_secret.labels(router, sec.user, sec.profile, sec.service).set(
                0 if sec.disabled else 1
            )
        self.ppp_secret_total.labels(router).set(
            sum(1 for s in secrets if not s.disabled)
        )

        # Counters: only advance for log ids not seen before.
        seen = self._seen_log_ids.setdefault(router, set())
        new_ids: list[str] = []
        rows = list(raw_log_rows or [])
        id_by_index = [str(r.get(".id", r.get("id", f"_{i}"))) for i, r in enumerate(rows)]
        # events[] is aligned to the ppp-topic subset, not rows[]; re-derive
        # per-row so we can key on .id. Simpler: recompute from rows here.
        for r, rid in zip(rows, id_by_index):
            if rid in seen:
                continue
            evs = ppp_mod.parse_log_events([r])
            for e in evs:
                if e.kind == "disconnect":
                    self.ppp_disconnect_total.labels(
                        router, e.user or "unknown", ppp_mod.bucket_reason(e.reason)
                    ).inc()
                elif e.kind == "auth-failure":
                    self.ppp_auth_failure_total.labels(router).inc()
                elif e.kind == "connect":
                    self.ppp_connect_total.labels(router, e.user or "unknown").inc()
            new_ids.append(rid)
        seen.update(new_ids)
        if len(seen) > 5000:  # bound memory - keep the newest half
            self._seen_log_ids[router] = set(list(seen)[-2500:])

    # -- config backup -----------------------------------------------------
    def update_backup(self, result, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        self.config_backup_success.labels(result.router).set(1 if result.success else 0)
        if result.success:
            self.config_last_backup_ts.labels(result.router).set(now)
            self.config_export_lines.labels(result.router).set(result.export_lines)
        self.config_changed.labels(result.router).set(1 if result.changed else 0)
        if result.changed:
            self.config_last_change_ts.labels(result.router).set(now)

    # -- system / topology ----------------------------------------------------
    def update_system(self, router: str, info: SystemInfo) -> None:
        self._clear_router(self.system_info, router)
        self.system_info.labels(router, info.version, info.board_name, info.architecture).set(1)
        self.system_uptime.labels(router).set(info.uptime_seconds)

    def update_topology(self, router: str, edges: list[Edge]) -> None:
        self._clear_router(self.topology_edge, router)
        for e in edges:
            self.topology_edge.labels(router, e.dst, e.type, e.via_interface).set(1)

    def set_router_info(self, router: str, site: str, pop: str, role: str) -> None:
        self.router_info.labels(router, site, pop, role).set(1)

    def update_scrape(self, router: str, ok: bool, duration: float) -> None:
        self.scrape_success.labels(router).set(1 if ok else 0)
        self.scrape_duration.labels(router).set(duration)

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _clear_router(metric, router: str) -> None:
        """Remove all child series of a labelled metric for one router, so
        stale (user disconnected / account deleted) series disappear."""
        to_drop = [
            lv for lv in list(getattr(metric, "_metrics", {}))
            if lv and lv[0] == router
        ]
        for lv in to_drop:
            try:
                metric.remove(*lv)
            except KeyError:
                pass
