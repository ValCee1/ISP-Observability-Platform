"""Operator CLI for a sector frequency migration.

    python -m routeros_exporter.freq_migration <sector-name>

Walks the five-step process with three approval gates, in order - nothing
past step 1 (discovery, read-only) runs without an explicit "yes" typed at
the terminal for that specific step:

    1. discover CPEs on the sector                     (no gate - read-only)
    2. GATE -> spectral scan -> choose frequencies
    3. GATE -> push the scan-list to every CPE
    4. GATE -> change the sector frequency, wait, report who came back

Each gate defaults to "no" - an empty Enter or anything but exactly "yes"
aborts the whole run, leaving nothing partially applied beyond whatever
gate already completed. There is no flag to skip a gate; that's the point.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable

from ..client import RouterOSError, connect
from . import config as fmc
from . import discovery as disc
from . import migrate as mig
from . import scanlist as sl
from . import spectrum as spec

Confirm = Callable[[str], bool]


def _terminal_confirm(prompt: str) -> bool:
    try:
        answer = input(f"{prompt} [type 'yes' to proceed, anything else aborts]: ")
    except EOFError:
        return False
    return answer.strip().lower() == "yes"


def run_migration(
    sector_name: str,
    *,
    sectors_path: str | None = None,
    credentials_path: str | None = None,
    confirm: Confirm = _terminal_confirm,
    out: Callable[[str], None] = print,
) -> int:
    sectors, cpe_cred = fmc.load(sectors_path, credentials_path)
    sector = fmc.find_sector(sectors, sector_name)

    if not sector.username or not sector.password:
        out(f"no credentials found for sector {sector_name!r} - check the credentials file")
        return 2
    if not cpe_cred.username or not cpe_cred.password:
        out("no shared CPE credential found (cpe_shared) - check the credentials file")
        return 2

    try:
        with connect(sector, timeout=30.0) as sector_client:
            return _run(sector, sector_client, cpe_cred, confirm=confirm, out=out)
    except RouterOSError as exc:
        out(f"ABORTED: {exc}")
        return 1


def _run(sector, sector_client, cpe_cred, *, confirm: Confirm, out) -> int:
    # --- Step 1: discovery (read-only, no gate) ----------------------
    out(f"Discovering CPEs on sector {sector.name!r}...")
    cpes = disc.discover_cpes(sector_client, sector.wireless_interface)
    if not cpes:
        out("No CPEs currently registered on this sector - nothing to migrate.")
        return 0

    out(f"Found {len(cpes)} registered CPE(s):")
    for cpe in cpes:
        ip = cpe.management_ip or "NO MANAGEMENT IP FOUND"
        out(f"  {cpe.mac}  {cpe.identity or '(no identity)'}  signal={cpe.signal_dbm}dBm  mgmt={ip}")

    reachable, unreachable = sl.targets_from_discovery(
        cpes, username=cpe_cred.username, password=cpe_cred.password
    )
    if unreachable:
        out("")
        out(f"WARNING: {len(unreachable)} CPE(s) have no resolvable management IP "
            f"and cannot be preconfigured - they may not find the sector again "
            f"after the frequency change:")
        for cpe in unreachable:
            out(f"  {cpe.mac}  {cpe.identity or '(no identity)'}")

    # --- Gate 1: the spectral scan ------------------------------------
    out("")
    if not confirm(
        f"Run a spectral scan on {sector.name!r}? This will disconnect all "
        f"{len(cpes)} currently-registered CPE(s) for the scan's duration."
    ):
        out("Aborted before scanning - nothing changed.")
        return 3

    out("Scanning...")
    readings = spec.run_spectral_scan(sector_client, sector.wireless_interface)
    current_freq_rows = sector_client.query(
        "/interface/wireless/print", name=sector.wireless_interface
    )
    current_frequency = int(current_freq_rows[0]["frequency"])

    plan = spec.choose_frequencies(
        readings,
        current_frequency_mhz=current_frequency,
        freq_min_mhz=sector.freq_min_mhz,
        freq_max_mhz=sector.freq_max_mhz,
        channel_width_mhz=sector.channel_width_mhz,
        fallback_count=sector.fallback_count,
    )

    out(f"Current frequency: {plan.old_frequency_mhz} MHz")
    if plan.no_change_recommended:
        out(f"Scan says the current frequency is already the cleanest option in range - "
            f"no change recommended. Migration scan-list would still be pushed as "
            f"{plan.scan_list} MHz if you continue.")
    out(f"Recommended new frequency: {plan.new_frequency_mhz} MHz")
    out(f"Fallback frequencies: {plan.fallback_frequencies_mhz} MHz")

    # --- Gate 2: preconfigure CPE scan-lists ---------------------------
    out("")
    if not confirm(
        f"Push scan-list {plan.scan_list} MHz to all {len(reachable)} reachable "
        f"CPE(s)? This writes to live CPE configuration."
    ):
        out("Aborted before touching CPE config - nothing changed.")
        return 3

    out("Pushing scan-lists...")
    results = sl.push_scan_list_to_all(
        reachable,
        cpe_wireless_interface=sector.cpe_wireless_interface,
        frequencies_mhz=plan.scan_list,
    )
    failed = [r for r in results if not r.success]
    for r in results:
        status = "ok" if r.success else f"FAILED: {r.error}"
        out(f"  {r.mac}  {status}")

    if failed or unreachable:
        out("")
        out(f"{len(failed)} scan-list push(es) failed and {len(unreachable)} CPE(s) were "
            f"unreachable - those CPEs may not find the sector after the change.")

    # --- Gate 3: change the sector frequency ---------------------------
    out("")
    if not confirm(
        f"Change sector {sector.name!r} from {plan.old_frequency_mhz} MHz to "
        f"{plan.new_frequency_mhz} MHz now? Every CPE on this sector will drop."
    ):
        out("Aborted before changing the sector - CPE scan-lists were already "
            "updated above; the sector itself is untouched.")
        return 3

    out("Changing sector frequency...")
    mig.change_sector_frequency(sector_client, sector.wireless_interface, plan.new_frequency_mhz)

    out("Waiting for CPEs to reconnect...")
    report = mig.wait_for_reconnection(
        cpes, lambda: mig.registered_macs(sector_client, sector.wireless_interface)
    )

    out("")
    out(f"Done after {report.elapsed_seconds:.0f}s: {report.reconnected_count}/"
        f"{len(report.expected)} CPE(s) reconnected.")
    if report.missing:
        out("Did NOT reconnect:")
        for mac, identity in report.missing:
            out(f"  {mac}  {identity}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sector", help="sector name, as listed in the sector inventory")
    parser.add_argument("--sectors", dest="sectors_path", default=None)
    parser.add_argument("--credentials", dest="credentials_path", default=None)
    args = parser.parse_args(argv)
    return run_migration(args.sector, sectors_path=args.sectors_path, credentials_path=args.credentials_path)


if __name__ == "__main__":
    sys.exit(main())
