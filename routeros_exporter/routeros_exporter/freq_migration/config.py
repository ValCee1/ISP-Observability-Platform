"""Configuration for the frequency migration tool.

Deliberately separate from ``routeros_exporter/config.yml`` and its
credentials file: that config is for the always-on polling daemon (read-only
API access, plus the narrowly-scoped backup credential); this tool needs
*write* access to every CPE's wireless config and a sector's frequency, on a
tool an operator runs by hand a few times a year. Keeping the inventories
and credential files apart means a bug or leak in one doesn't imply the
other, and the CPE-write blast radius is easy to audit on its own.

Two inputs, same pattern as routeros_exporter/config.py:

  * ``FREQ_MIGRATION_SECTORS`` - non-secret sector inventory (host, wireless
    interface name, allowed frequency band, channel width). Committed as
    ``routeros_exporter/freq_migration_sectors.example.yml``.

  * ``FREQ_MIGRATION_CREDENTIALS_FILE`` - JSON, gitignored, two parts:
      - ``cpe_shared``: one username/password used for every CPE (confirmed
        2026-09-16: all CPEs share one login today).
      - ``sectors``: per-sector username/password (confirmed 2026-09-16:
        each sector has its own, like the core routers already do).
"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
from typing import Any

import yaml

DEFAULT_SECTORS_PATH = "/etc/routeros-exporter/freq_migration_sectors.yml"
DEFAULT_CREDENTIALS_PATH = "/run/secrets/freq_migration_credentials"


@dataclasses.dataclass(frozen=True)
class SectorConfig:
    """One PtMP sector AP that can be frequency-migrated."""

    name: str                  # matches the `sector` label used elsewhere
    host: str
    port: int = 8728
    use_tls: bool = False
    username: str = ""         # filled in from the credentials file
    password: str = ""
    wireless_interface: str = "wlan1"   # the sector radio's interface name
    cpe_wireless_interface: str = "wlan1"  # same, on each CPE (station mode)
    freq_min_mhz: int = 5745   # allowed band for candidate frequencies -
    freq_max_mhz: int = 5825   # keep this inside the country/regulatory
                                # config already set on the radio; this is
                                # a second, explicit guard, not a substitute
                                # for it.
    channel_width_mhz: int = 20
    fallback_count: int = 2    # candidate fallbacks beyond old+new


@dataclasses.dataclass(frozen=True)
class CPECredential:
    username: str
    password: str


def _load_yaml(path: str) -> dict[str, Any]:
    text = pathlib.Path(path).read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


def _load_credentials(path: str) -> tuple[CPECredential, dict[str, dict[str, str]]]:
    raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    cpe_raw = raw.get("cpe_shared", {})
    cpe = CPECredential(
        username=str(cpe_raw.get("username", "")),
        password=str(cpe_raw.get("password", "")),
    )
    sectors_raw = {k: v for k, v in raw.get("sectors", {}).items()}
    return cpe, sectors_raw


def load(
    sectors_path: str | None = None,
    credentials_path: str | None = None,
) -> tuple[list[SectorConfig], CPECredential]:
    sectors_path = sectors_path or os.environ.get("FREQ_MIGRATION_SECTORS", DEFAULT_SECTORS_PATH)
    credentials_path = credentials_path or os.environ.get(
        "FREQ_MIGRATION_CREDENTIALS_FILE", DEFAULT_CREDENTIALS_PATH
    )

    doc = _load_yaml(sectors_path)
    cpe_cred, sector_creds = _load_credentials(credentials_path)

    sectors = []
    for s in doc.get("sectors", []):
        name = s["name"]
        creds = sector_creds.get(name, {})
        sectors.append(
            SectorConfig(
                name=name,
                host=s["host"],
                port=int(s.get("port", 8728)),
                use_tls=bool(s.get("use_tls", False)),
                username=str(creds.get("username", "")),
                password=str(creds.get("password", "")),
                wireless_interface=s.get("wireless_interface", "wlan1"),
                cpe_wireless_interface=s.get("cpe_wireless_interface", "wlan1"),
                freq_min_mhz=int(s.get("freq_min_mhz", 5745)),
                freq_max_mhz=int(s.get("freq_max_mhz", 5825)),
                channel_width_mhz=int(s.get("channel_width_mhz", 20)),
                fallback_count=int(s.get("fallback_count", 2)),
            )
        )
    return sectors, cpe_cred


def find_sector(sectors: list[SectorConfig], name: str) -> SectorConfig:
    for s in sectors:
        if s.name == name:
            return s
    raise KeyError(f"no sector named {name!r} in the sector inventory")
