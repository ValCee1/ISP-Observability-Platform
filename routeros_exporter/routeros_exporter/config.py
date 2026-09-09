"""Configuration loading for the RouterOS exporter.

Two inputs, both mounted read-only into the container (same pattern as every
other service in docker-compose.yml):

  * ``ROUTEROS_EXPORTER_CONFIG``  - non-secret settings + the router list
    (poll intervals, backup repo path, per-router host/port/site/pop labels).
    Committed to the repo as ``routeros_exporter/config.example.yml``.

  * ``ROUTEROS_CREDENTIALS_FILE``  - a JSON object mapping router name ->
    ``{"username": ..., "password": ...}``. Lives in ``secrets/`` (gitignored),
    mounted as a Docker secret at ``/run/secrets/routeros_api_credentials``.
    Kept separate from the config file so the router inventory can be
    version-controlled without the passwords.
"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = "/etc/routeros-exporter/config.yml"
DEFAULT_CREDENTIALS_PATH = "/run/secrets/routeros_api_credentials"


@dataclasses.dataclass(frozen=True)
class RouterConfig:
    """One core router to poll over the API."""

    name: str                 # stable id; becomes the `router` metric label
    host: str
    port: int = 8728          # 8728 plain API, 8729 api-ssl
    use_tls: bool = False
    username: str = ""         # filled in from the credentials file
    password: str = ""
    # Label passthrough - these land on every metric for this router so the
    # exporter's series line up with the SNMP jobs' `site` / `pop` / `role`.
    site: str = ""
    pop: str = ""
    role: str = "core-router"
    # Per-router opt-outs (a repeater with no PPPoE server, say).
    collect_ppp: bool = True
    collect_backup: bool = True
    collect_topology: bool = True

    @property
    def labels(self) -> dict[str, str]:
        out = {"router": self.name}
        for key in ("site", "pop", "role"):
            val = getattr(self, key)
            if val:
                out[key] = val
        return out


@dataclasses.dataclass(frozen=True)
class Config:
    routers: tuple[RouterConfig, ...]
    listen_port: int = 9436
    # PPP/system metrics refresh - RouterOS API is cheap, but there's no
    # reason to hammer it faster than Prometheus scrapes (default 30s for
    # the slow SNMP jobs).
    poll_interval_seconds: int = 30
    api_timeout_seconds: float = 10.0
    # Config backup cadence + destination. The repo is a plain `git init`
    # directory on its own volume; the exporter commits into it.
    backup_interval_seconds: int = 3600
    backup_repo_path: str = "/var/lib/routeros-exporter/backups"
    # Topology re-discovery cadence - routing/neighbour tables change rarely.
    topology_interval_seconds: int = 900
    topology_output_path: str = "/var/lib/routeros-exporter/topology.generated.yml"
    log_lookback: int = 200   # how many /log lines to scan for ppp events


def _load_yaml(path: str) -> dict[str, Any]:
    text = pathlib.Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return data


def _load_credentials(path: str) -> dict[str, dict[str, str]]:
    if not pathlib.Path(path).exists():
        # Not fatal at import time - useful for `--check` / unit tests. The
        # client raises a clear error later if it actually needs a password.
        return {}
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def load(
    config_path: str | None = None,
    credentials_path: str | None = None,
) -> Config:
    config_path = (
        config_path
        or os.environ.get("ROUTEROS_EXPORTER_CONFIG")
        or DEFAULT_CONFIG_PATH
    )
    credentials_path = (
        credentials_path
        or os.environ.get("ROUTEROS_CREDENTIALS_FILE")
        or DEFAULT_CREDENTIALS_PATH
    )

    raw = _load_yaml(config_path)
    creds = _load_credentials(credentials_path)

    routers: list[RouterConfig] = []
    for entry in raw.get("routers", []):
        name = entry["name"]
        cred = creds.get(name, {})
        routers.append(
            RouterConfig(
                name=name,
                host=entry["host"],
                port=int(entry.get("port", 8728)),
                use_tls=bool(entry.get("use_tls", False)),
                username=cred.get("username", entry.get("username", "")),
                password=cred.get("password", entry.get("password", "")),
                site=entry.get("site", ""),
                pop=entry.get("pop", ""),
                role=entry.get("role", "core-router"),
                collect_ppp=bool(entry.get("collect_ppp", True)),
                collect_backup=bool(entry.get("collect_backup", True)),
                collect_topology=bool(entry.get("collect_topology", True)),
            )
        )

    if not routers:
        raise ValueError(f"{config_path}: no routers defined")

    opts = raw.get("options", {})
    return Config(
        routers=tuple(routers),
        listen_port=int(opts.get("listen_port", 9436)),
        poll_interval_seconds=int(opts.get("poll_interval_seconds", 30)),
        api_timeout_seconds=float(opts.get("api_timeout_seconds", 10.0)),
        backup_interval_seconds=int(opts.get("backup_interval_seconds", 3600)),
        backup_repo_path=opts.get(
            "backup_repo_path", "/var/lib/routeros-exporter/backups"
        ),
        topology_interval_seconds=int(
            opts.get("topology_interval_seconds", 900)
        ),
        topology_output_path=opts.get(
            "topology_output_path",
            "/var/lib/routeros-exporter/topology.generated.yml",
        ),
        log_lookback=int(opts.get("log_lookback", 200)),
    )
