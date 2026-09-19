"""Step 1: discover every CPE currently registered on a sector.

This is a read-only step (no approval gate needed) and it's also the
baseline the final report diffs against - a CPE has to be identified here,
by MAC, before "did it come back" means anything.

Two API calls on the sector's own connection:
  /interface/wireless/registration-table/print  - who's associated right
      now: MAC, RouterOS's "radio name" identity string, signal, CCQ.
      CCQ reads 0 under Nv2 (see routeros_exporter/README.md /
      snmp_exporter/snmp.yml's note on this fleet) - captured anyway since
      not every sector necessarily runs Nv2.
  /ip/neighbor/print (MNDP)  - the same neighbour-discovery protocol Winbox
      uses to connect by MAC, already parsed by routeros_exporter.topology.
      Confirmed 2026-09-16: every CPE has a static management IP but is
      normally reached in Winbox by MAC, not IP - MNDP is what resolves
      that MAC to an IP an API client can actually dial.

A CPE that's registered but has no MNDP entry (protocol disabled, or a
non-MikroTik CPE) can't be reached for step 3 - it's reported as
"no management IP found" rather than silently skipped, since that's exactly
the kind of CPE an operator needs to know about before pulling the trigger
on a sector-wide change.
"""

from __future__ import annotations

import dataclasses

from .. import topology as topo


@dataclasses.dataclass(frozen=True)
class DiscoveredCPE:
    mac: str
    identity: str            # registration-table "radio name"
    signal_dbm: float | None
    ccq_percent: float | None
    management_ip: str | None    # None if no MNDP entry matched this MAC


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_registration_table(rows) -> list[DiscoveredCPE]:
    """Pure parse of `/interface/wireless/registration-table/print` rows -
    no management IP yet, that's filled in by `discover_cpes` once the
    neighbour table is available."""
    out = []
    for r in rows:
        out.append(
            DiscoveredCPE(
                mac=str(r.get("mac-address", "")).lower(),
                identity=str(r.get("radio-name") or r.get("last-ip") or ""),
                signal_dbm=_to_float(r.get("signal-strength")),
                ccq_percent=_to_float(r.get("tx-ccq")),
                management_ip=None,
            )
        )
    return out


def discover_cpes(client, wireless_interface: str) -> list[DiscoveredCPE]:
    """Full step-1 discovery against a connected sector API client:
    registration table + MNDP, joined by MAC."""
    reg_rows = client.query(
        "/interface/wireless/registration-table/print", interface=wireless_interface
    )
    cpes = parse_registration_table(reg_rows)

    neighbor_rows = client.query("/ip/neighbor/print")
    neighbors = topo.parse_neighbors(neighbor_rows)
    ip_by_mac = {n.mac: n.address for n in neighbors if n.mac and n.address}

    return [
        dataclasses.replace(cpe, management_ip=ip_by_mac.get(cpe.mac))
        for cpe in cpes
    ]
