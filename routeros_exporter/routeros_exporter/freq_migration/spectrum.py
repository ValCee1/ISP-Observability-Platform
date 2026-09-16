"""Step 2: scan the sector's RF environment and pick old/new/fallback
frequencies. Gated - the CLI must get an explicit go-ahead before calling
``run_spectral_scan`` (see cli.py), because putting the radio into scan mode
drops every currently-registered CPE for the scan's duration.

UNVERIFIED - needs live device: RouterOS's `/interface/wireless/spectral-scan`
row schema (which key holds the power reading) varies by RouterOS version
and wireless chipset, and hasn't been exercised against real hardware yet -
unlike `/export` and `/interface/wireless/registration-table`, which this
codebase has confirmed live. `parse_spectral_scan` tries a short list of
plausible key names and raises rather than silently misreading a scan if
none match, so a schema mismatch surfaces immediately the first time this
runs against a real sector instead of quietly picking a bad frequency.
"""

from __future__ import annotations

import dataclasses
import statistics
from typing import Any, Iterable

# Column names RouterOS has used for the power reading across versions /
# chipsets - first one present in a row wins. Extend this list rather than
# guessing once real hardware shows what this fleet's radios actually emit.
_POWER_KEYS = ("range0", "power", "signal-strength", "noise-floor")


@dataclasses.dataclass(frozen=True)
class FrequencyReading:
    frequency_mhz: int
    power_dbm: float


@dataclasses.dataclass(frozen=True)
class MigrationPlan:
    old_frequency_mhz: int
    new_frequency_mhz: int
    fallback_frequencies_mhz: list[int]
    no_change_recommended: bool   # True if `old` was already the cleanest
                                    # option in range - the plan still names
                                    # `new`/fallbacks for completeness, but
                                    # the CLI should say so plainly.

    @property
    def scan_list(self) -> list[int]:
        """The full migration scan-list to push to every CPE (step 3):
        old + new + fallbacks, deliberately short - see scanlist.py."""
        out = [self.old_frequency_mhz, self.new_frequency_mhz]
        out.extend(f for f in self.fallback_frequencies_mhz if f not in out)
        return out


def parse_spectral_scan(rows: Iterable[dict[str, Any]]) -> list[FrequencyReading]:
    out = []
    for r in rows:
        freq = r.get("freq") or r.get("frequency")
        if freq is None:
            continue
        power = None
        for key in _POWER_KEYS:
            if key in r:
                power = r[key]
                break
        if power is None:
            raise ValueError(
                f"spectral-scan row for {freq} MHz has none of the expected "
                f"power keys {_POWER_KEYS} - RouterOS version/chipset schema "
                f"mismatch; update _POWER_KEYS once you know what this "
                f"radio actually returns (row: {r!r})"
            )
        out.append(FrequencyReading(frequency_mhz=int(float(freq)), power_dbm=float(power)))
    return out


def run_spectral_scan(client, wireless_interface: str, *, duration_seconds: int = 20) -> list[FrequencyReading]:
    """Trigger the scan and parse its output. Disruptive - see module
    docstring; the caller is responsible for having gotten approval first."""
    rows = client.command(
        "/interface/wireless/spectral-scan",
        interface=wireless_interface,
        duration=str(duration_seconds),
    )
    return parse_spectral_scan(rows)


def average_by_frequency(readings: Iterable[FrequencyReading]) -> dict[int, float]:
    """A scan typically samples each frequency multiple times over its
    duration; average them so one noisy instant doesn't dominate."""
    by_freq: dict[int, list[float]] = {}
    for r in readings:
        by_freq.setdefault(r.frequency_mhz, []).append(r.power_dbm)
    return {f: statistics.mean(vals) for f, vals in by_freq.items()}


def choose_frequencies(
    readings: Iterable[FrequencyReading],
    *,
    current_frequency_mhz: int,
    freq_min_mhz: int,
    freq_max_mhz: int,
    channel_width_mhz: int,
    fallback_count: int = 2,
) -> MigrationPlan:
    """Pick the cleanest frequency in range, plus `fallback_count` next-best
    non-overlapping candidates. Lower measured power = cleaner (less RF
    energy already occupying that frequency)."""
    avg = average_by_frequency(readings)
    in_range = {f: p for f, p in avg.items() if freq_min_mhz <= f <= freq_max_mhz}
    if not in_range:
        raise ValueError(
            f"no spectral-scan readings fall inside the allowed band "
            f"{freq_min_mhz}-{freq_max_mhz} MHz - check the scan ran on the "
            f"right interface and the sector's configured band"
        )

    ranked = sorted(in_range.items(), key=lambda kv: kv[1])  # cleanest first

    chosen: list[int] = []
    for freq, _power in ranked:
        if any(abs(freq - c) < channel_width_mhz for c in chosen):
            continue  # too close to an already-chosen candidate - avoid
            # adjacent-channel overlap between "new" and its own fallbacks
        chosen.append(freq)
        if len(chosen) >= 1 + fallback_count:
            break

    if not chosen:
        raise ValueError("no non-overlapping candidate frequencies found in range")

    new_frequency = chosen[0]
    fallbacks = chosen[1:]
    no_change = new_frequency == current_frequency_mhz

    return MigrationPlan(
        old_frequency_mhz=current_frequency_mhz,
        new_frequency_mhz=new_frequency,
        fallback_frequencies_mhz=fallbacks,
        no_change_recommended=no_change,
    )
