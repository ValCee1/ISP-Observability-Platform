"""Topology auto-discovery.

``prometheus/topology.yml`` is currently hand-maintained and its whole point
is dependency-aware alert inhibition: if a POP's upstream backhaul or router
is down, every sector behind it is unreachable, so those downstream alerts
should be suppressed, not sent as separate incidents. Today the
``inhibit_rules`` keyed on ``pop`` in alertmanager.yml are inert because no
target carries a ``pop`` label.

This module derives the same model from the API so the ``pop`` labels (and a
generated topology file) stay correct as the network changes:

  /ip/route/print     - the default route's gateway = this router's upstream.
                        A router whose default route points at another polled
                        router's address (or at a backhaul-link subnet) is
                        downstream of that POP.
  /ip/neighbor/print  - MNDP/LLDP neighbours: which sector / radio hangs off
                        which interface, by identity + MAC + board.
  /interface/print    - running state, so a down backhaul port is visible.

Output:
  * ``routeros_topology_edge{from,to,type}`` metric (type = upstream|neighbor).
  * ``topology.generated.yml`` - same shape as the hand file, written next to
    it and documented as generated. The hand file remains the seed/override.

UNVERIFIED - needs live device: neighbour discovery depends on MNDP/LLDP
being enabled on the sector/backhaul interfaces; the default-route heuristic
assumes each downstream POP router genuinely has a single default route via
its backhaul (true for the described topology - the PPPoE client session or
a static route installs it).
"""

from __future__ import annotations

import dataclasses
import ipaddress
from typing import Any, Iterable

import yaml


@dataclasses.dataclass(frozen=True)
class Edge:
    src: str          # router name
    dst: str          # upstream router name / neighbour identity
    type: str         # "upstream" | "neighbor"
    via_interface: str = ""


@dataclasses.dataclass(frozen=True)
class Neighbor:
    interface: str
    identity: str
    address: str
    mac: str
    board: str


def parse_default_gateways(route_rows: Iterable[dict[str, Any]]) -> list[str]:
    """Gateway address(es) of the active default route(s)."""
    gws: list[str] = []
    for r in route_rows:
        if str(r.get("dst-address", "")) not in ("0.0.0.0/0", "::/0"):
            continue
        if str(r.get("active", "false")).lower() != "true":
            continue
        if str(r.get("disabled", "false")).lower() == "true":
            continue
        gw = str(r.get("gateway", "")).split("%")[0].strip()
        if gw:
            gws.append(gw)
    return gws


def parse_neighbors(rows: Iterable[dict[str, Any]]) -> list[Neighbor]:
    out: list[Neighbor] = []
    for r in rows:
        out.append(
            Neighbor(
                interface=str(r.get("interface", "")),
                identity=str(r.get("identity", "")),
                address=str(r.get("address", "")),
                mac=str(r.get("mac-address", "")).lower(),
                board=str(r.get("board") or r.get("platform", "")),
            )
        )
    return out


def running_interfaces(rows: Iterable[dict[str, Any]]) -> dict[str, bool]:
    return {
        str(r.get("name", "")): str(r.get("running", "false")).lower() == "true"
        for r in rows
    }


def _addr_in_networks(addr: str, networks: list[ipaddress._BaseNetwork]) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in n for n in networks)


def build_edges(
    router_name: str,
    default_gateways: list[str],
    neighbors: list[Neighbor],
    *,
    router_addresses: dict[str, list[str]],
    backhaul_networks: list[str] | None = None,
) -> list[Edge]:
    """Resolve this router's upstream + neighbour edges.

    ``router_addresses`` maps every *other* polled router name -> its known
    IPs (host list from config + any /ip/address we were handed). A default
    gateway that matches one of those, or that sits in a declared backhaul
    subnet, produces an ``upstream`` edge.
    """
    edges: list[Edge] = []
    nets = [ipaddress.ip_network(n, strict=False) for n in (backhaul_networks or [])]

    for gw in default_gateways:
        upstream = None
        for other, addrs in router_addresses.items():
            if other != router_name and gw in addrs:
                upstream = other
                break
        if upstream is None and _addr_in_networks(gw, nets):
            upstream = f"backhaul:{gw}"
        if upstream:
            edges.append(Edge(router_name, upstream, "upstream"))

    for nb in neighbors:
        label = nb.identity or nb.address or nb.mac
        if label:
            edges.append(Edge(router_name, label, "neighbor", nb.interface))
    return edges


def render_generated_yaml(
    edges_by_router: dict[str, list[Edge]],
    router_pops: dict[str, str],
) -> str:
    """Emit the same shape as prometheus/topology.yml (documentation +
    source of truth for `pop`), marked generated."""
    pops: dict[str, dict[str, Any]] = {}
    for router, edges in edges_by_router.items():
        pop = router_pops.get(router) or router
        node = pops.setdefault(
            pop, {"router": router, "upstream": [], "neighbors": []}
        )
        for e in edges:
            if e.type == "upstream":
                node["upstream"].append(e.dst)
            else:
                node["neighbors"].append(
                    {"via": e.via_interface, "peer": e.dst}
                )

    doc = {
        "_generated_by": "routeros_exporter/topology.py",
        "_note": (
            "Auto-discovered from /ip/route, /ip/neighbor, /interface. The "
            "hand-maintained prometheus/topology.yml stays authoritative for "
            "the `pop` label; this file is for review / drift-checking."
        ),
        "pops": pops,
    }
    return yaml.safe_dump(doc, sort_keys=True, default_flow_style=False)
