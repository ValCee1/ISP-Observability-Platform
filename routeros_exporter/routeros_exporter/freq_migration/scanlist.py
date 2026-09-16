"""Step 3: push a short migration scan-list to every discovered CPE, while
they're still on the old frequency and reachable. Gated - the CLI must get
an explicit go-ahead before calling ``push_scan_list`` for any CPE, since
this writes to live customer equipment.

Deliberately narrow: old + new + up to `fallback_count` fallbacks (4ish
entries), never a broad sweep. A wide scan-list makes a CPE slower to
reconnect after the sector moves, more likely to lock onto the wrong
sector's beacon on a shared frequency, and more likely to include a
frequency that's illegal or unsupported on that CPE's radio - see
freq_migration/__init__.py and the design discussion in
docs/tier1-subscriber-config-topology.md.
"""

from __future__ import annotations

import dataclasses

from ..client import RouterOSError, connect
from .discovery import DiscoveredCPE


@dataclasses.dataclass(frozen=True)
class CPETarget:
    """Enough to open an API connection to one CPE - matches the attributes
    client.connect() expects from a router-like object."""

    mac: str
    host: str            # the CPE's management IP, resolved in step 1
    username: str
    password: str
    port: int = 8728
    use_tls: bool = False


@dataclasses.dataclass(frozen=True)
class ScanListResult:
    mac: str
    success: bool
    error: str = ""


def targets_from_discovery(
    cpes: list[DiscoveredCPE], *, username: str, password: str, port: int = 8728
) -> tuple[list[CPETarget], list[DiscoveredCPE]]:
    """Split discovered CPEs into ones we can actually reach (have a
    management IP) and ones we can't - the latter must be surfaced to the
    operator, never silently dropped, since a CPE we can't preconfigure is
    a CPE that may not find the sector again after the frequency changes."""
    reachable, unreachable = [], []
    for cpe in cpes:
        if cpe.management_ip:
            reachable.append(
                CPETarget(
                    mac=cpe.mac, host=cpe.management_ip,
                    username=username, password=password, port=port,
                )
            )
        else:
            unreachable.append(cpe)
    return reachable, unreachable


def _wireless_interface_id(client, interface_name: str) -> str:
    rows = client.query("/interface/wireless/print", name=interface_name)
    if not rows:
        raise RouterOSError(f"no wireless interface named {interface_name!r} on this CPE")
    return str(rows[0][".id"])


def push_scan_list(
    target: CPETarget,
    *,
    cpe_wireless_interface: str,
    frequencies_mhz: list[int],
    timeout: float = 15.0,
) -> ScanListResult:
    """Set one CPE's wireless scan-list. RouterOS wants the list as a
    comma-separated string of MHz values."""
    scan_list = ",".join(str(f) for f in frequencies_mhz)
    try:
        with connect(target, timeout=timeout) as client:
            iface_id = _wireless_interface_id(client, cpe_wireless_interface)
            client.command(
                "/interface/wireless/set",
                **{"numbers": iface_id, "scan-list": scan_list},
            )
        return ScanListResult(mac=target.mac, success=True)
    except RouterOSError as exc:
        return ScanListResult(mac=target.mac, success=False, error=str(exc))


def push_scan_list_to_all(
    targets: list[CPETarget], *, cpe_wireless_interface: str, frequencies_mhz: list[int],
) -> list[ScanListResult]:
    """Best-effort across every reachable CPE - one CPE's failure must not
    stop the others from getting preconfigured. The CLI reports every
    failure before asking for the next gate's approval."""
    return [
        push_scan_list(
            t, cpe_wireless_interface=cpe_wireless_interface, frequencies_mhz=frequencies_mhz
        )
        for t in targets
    ]
