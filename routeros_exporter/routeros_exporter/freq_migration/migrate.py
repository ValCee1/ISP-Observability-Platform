"""Step 4: change the sector's frequency, then find out who came back.

The frequency change goes over the sector's own API connection (independent
of the wireless link it's about to take down) - the sector stays reachable
through ether1 the whole time even though every CPE on it just dropped.

UNVERIFIED - needs live device, same caveat as config backup before its
timeout bug was found (see routeros_exporter/README.md): the poll interval
and total timeout below are a starting guess, not a measured value. Per the
user (2026-09-16): "the exact timeout should be determined through lab
testing" - tune `poll_interval_seconds` / `total_timeout_seconds` once this
has run against a real sector.

This step deliberately does NOT do the identity-verification (same MAC,
same sector, same frequency, same SSID, ...) considered in the earlier,
larger design - see docs/tier1-subscriber-config-topology.md. In this
scoped-down version, "CPE came back" means "its MAC reappeared in this
sector's own registration table within the timeout" - simpler, and enough
for the operator to see who to go check on physically.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Callable

from ..client import RouterOSError
from .discovery import DiscoveredCPE


@dataclasses.dataclass(frozen=True)
class MigrationReport:
    expected: dict[str, str]     # mac -> identity, from the pre-change discovery
    missing_macs: set[str]       # macs never seen again within the timeout
    elapsed_seconds: float

    @property
    def reconnected_count(self) -> int:
        return len(self.expected) - len(self.missing_macs)

    @property
    def missing(self) -> list[tuple[str, str]]:
        """(mac, identity) pairs, for a human-readable report - identity
        falls back to the mac itself if the pre-change registration table
        had no radio-name for it."""
        return [(mac, self.expected.get(mac, mac)) for mac in sorted(self.missing_macs)]


def _wireless_interface_id(client, interface_name: str) -> str:
    rows = client.query("/interface/wireless/print", name=interface_name)
    if not rows:
        raise RouterOSError(f"no wireless interface named {interface_name!r} on this sector")
    return str(rows[0][".id"])


def change_sector_frequency(client, wireless_interface: str, new_frequency_mhz: int) -> None:
    iface_id = _wireless_interface_id(client, wireless_interface)
    client.command(
        "/interface/wireless/set",
        **{"numbers": iface_id, "frequency": str(new_frequency_mhz)},
    )


def registered_macs(client, wireless_interface: str) -> set[str]:
    rows = client.query(
        "/interface/wireless/registration-table/print", interface=wireless_interface
    )
    return {str(r.get("mac-address", "")).lower() for r in rows if r.get("mac-address")}


def wait_for_reconnection(
    before: list[DiscoveredCPE],
    poll_fn: Callable[[], set[str]],
    *,
    total_timeout_seconds: float = 180.0,
    poll_interval_seconds: float = 15.0,
    sleep_fn: Callable[[float], None] | None = None,
    now_fn: Callable[[], float] | None = None,
) -> MigrationReport:
    """Poll `poll_fn` (the sector's own registration table) until every
    pre-change CPE has reappeared or `total_timeout_seconds` elapses -
    the "staged retry window" from the design: keep checking, don't declare
    failure on the first empty poll.

    `sleep_fn`/`now_fn` default to `None` and are resolved to `time.sleep`/
    `time.time` inside the function body, not as parameter defaults - a
    parameter default is evaluated once, at import time, so tests
    monkeypatching `time.sleep` would silently miss an already-bound
    default and this would really sleep for the full timeout every run."""
    sleep_fn = sleep_fn or time.sleep
    now_fn = now_fn or time.time

    expected = {cpe.mac: (cpe.identity or cpe.mac) for cpe in before}
    remaining = set(expected)
    start = now_fn()

    while remaining and (now_fn() - start) < total_timeout_seconds:
        sleep_fn(poll_interval_seconds)
        remaining -= poll_fn()

    return MigrationReport(
        expected=expected, missing_macs=remaining, elapsed_seconds=now_fn() - start
    )
