"""/system/resource + /system/routerboard -> version / board / uptime.

Feeds ``routeros_system_info`` (a 1-valued info metric carrying version and
board as labels) and ``routeros_system_uptime_seconds``. The fleet
version-drift alert (config-backup.yml) keys off the ``version`` label.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from .ppp import parse_duration


@dataclasses.dataclass(frozen=True)
class SystemInfo:
    version: str
    board_name: str
    architecture: str
    uptime_seconds: float
    cpu_load_percent: float
    free_memory_bytes: float
    total_memory_bytes: float


def parse_system(resource_rows: list[dict[str, Any]], routerboard_rows: list[dict[str, Any]] | None = None) -> SystemInfo:
    res = resource_rows[0] if resource_rows else {}
    rb = (routerboard_rows or [{}])[0]
    return SystemInfo(
        version=str(res.get("version", "")).split(" ")[0],
        board_name=str(res.get("board-name") or rb.get("model", "")),
        architecture=str(res.get("architecture-name", "")),
        uptime_seconds=parse_duration(res.get("uptime")),
        cpu_load_percent=float(res.get("cpu-load", 0) or 0),
        free_memory_bytes=float(res.get("free-memory", 0) or 0),
        total_memory_bytes=float(res.get("total-memory", 0) or 0),
    )
