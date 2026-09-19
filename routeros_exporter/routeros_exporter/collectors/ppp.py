"""PPP/PPPoE subscriber session parsing.

Three RouterOS sources, none of which SNMP can give you:

  /ppp/active/print  - who is connected right now: name, service (pppoe/
                       pptp/l2tp/sstp/ovpn), caller-id (the client's MAC for
                       pppoe - our join key to the sector registration
                       table), assigned address, session uptime.
  /ppp/secret/print  - the provisioned account book: name, profile,
                       disabled flag. active vs secret = "provisioned but
                       offline".
  /log/print         - connect / disconnect / auth-failure events. This is
                       the ONLY place a disconnect *reason* is available
                       (ppp-sessions.yml notes SNMP has none).

RouterOS returns durations as strings like ``6w3d10h11m35s`` and booleans as
``"true"``/``"false"`` - normalised here.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Iterable

_DURATION_RE = re.compile(
    r"(?:(?P<w>\d+)w)?(?:(?P<d>\d+)d)?(?:(?P<h>\d+)h)?"
    r"(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s)?"
)
_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


def parse_duration(value: str | None) -> float:
    """RouterOS uptime string -> seconds. ``None``/``""`` -> 0."""
    if not value:
        return 0.0
    m = _DURATION_RE.fullmatch(value.strip())
    if not m:
        return 0.0
    parts = {k: int(v) for k, v in m.groupdict(default="0").items()}
    return (
        parts["w"] * 604800
        + parts["d"] * 86400
        + parts["h"] * 3600
        + parts["m"] * 60
        + parts["s"]
    )


def normalize_mac(value: str | None) -> str:
    """Lowercase colon-separated, so a pppoe caller-id lines up with the
    ``cpe_mac`` label on ``sector_cpe_*``.

    UNVERIFIED - needs live device: the SNMP side renders ``cpe_mac`` from a
    6-octet table index; confirm its exact format against a live sector walk
    and, if it differs (e.g. no separators / uppercase), add the matching
    normalisation as a recording rule in subscriber-sessions.yml rather than
    changing this - the exporter should always emit the canonical form.
    """
    if not value:
        return ""
    v = value.strip().lower().replace("-", ":")
    if _MAC_RE.match(v):
        return v
    hexonly = re.sub(r"[^0-9a-f]", "", v)
    if len(hexonly) == 12:
        return ":".join(hexonly[i : i + 2] for i in range(0, 12, 2))
    return value.strip().lower()


@dataclasses.dataclass(frozen=True)
class ActiveSession:
    user: str
    service: str          # pppoe / pptp / l2tp / sstp / ovpn
    caller_id: str         # normalised MAC for pppoe; tunnel source IP otherwise
    address: str           # address handed to the client
    uptime_seconds: float


@dataclasses.dataclass(frozen=True)
class Secret:
    user: str
    profile: str
    service: str
    disabled: bool


@dataclasses.dataclass(frozen=True)
class PppEvent:
    kind: str              # "disconnect" | "auth-failure" | "connect"
    user: str
    reason: str            # free-text tail, "" when none


def parse_active(rows: Iterable[dict[str, Any]]) -> list[ActiveSession]:
    out: list[ActiveSession] = []
    for r in rows:
        service = str(r.get("service", "")).lower()
        caller = str(r.get("caller-id", ""))
        out.append(
            ActiveSession(
                user=str(r.get("name", "")),
                service=service,
                caller_id=normalize_mac(caller) if service == "pppoe" else caller,
                address=str(r.get("address", "")),
                uptime_seconds=parse_duration(r.get("uptime")),
            )
        )
    return out


def parse_secrets(rows: Iterable[dict[str, Any]]) -> list[Secret]:
    out: list[Secret] = []
    for r in rows:
        out.append(
            Secret(
                user=str(r.get("name", "")),
                profile=str(r.get("profile", "")),
                service=str(r.get("service", "any")).lower(),
                disabled=str(r.get("disabled", "false")).lower() == "true",
            )
        )
    return out


# Log line shapes seen on RouterOS PPPoE servers. UNVERIFIED - needs live
# device: confirm the exact wording on the target ROS version; the parser is
# deliberately permissive (substring + a couple of capture groups) so minor
# wording changes still classify correctly.
_DISCONNECT_RE = re.compile(
    r"<?pppoe-(?P<user>[^>: ]+)>?[: ].*?(?:disconnected|terminating|closing)"
    r"(?:[:,] *(?P<reason>.+))?",
    re.IGNORECASE,
)
_AUTH_FAIL_RE = re.compile(
    r"(?:<?pppoe-(?P<user>[^>: ]+)>?[: ].*?)?"
    r"(?:authentication failed|login failed|invalid user(?:name)? or password)"
    r"(?:.*?for +(?P<user2>[^\s,]+))?",
    re.IGNORECASE,
)
_CONNECT_RE = re.compile(
    r"<?pppoe-(?P<user>[^>: ]+)>?[: ].*?(?:connected|authenticated)",
    re.IGNORECASE,
)


def parse_log_events(rows: Iterable[dict[str, Any]]) -> list[PppEvent]:
    """Classify the ppp-related lines in a ``/log/print`` slice.

    Only lines whose ``topics`` mention ppp/pppoe are considered, so an
    unrelated 'authentication failed' (e.g. a login attempt on the router
    itself) is ignored.
    """
    events: list[PppEvent] = []
    for r in rows:
        topics = str(r.get("topics", "")).lower()
        if "ppp" not in topics and "pppoe" not in topics:
            continue
        msg = str(r.get("message", ""))

        m = _AUTH_FAIL_RE.search(msg)
        if m and ("fail" in msg.lower() or "invalid" in msg.lower()):
            user = m.group("user") or m.group("user2") or ""
            events.append(PppEvent("auth-failure", user, ""))
            continue

        m = _DISCONNECT_RE.search(msg)
        if m:
            events.append(
                PppEvent("disconnect", m.group("user") or "", (m.group("reason") or "").strip())
            )
            continue

        m = _CONNECT_RE.search(msg)
        if m:
            events.append(PppEvent("connect", m.group("user") or "", ""))
    return events


def summarize_disconnect_reasons(events: Iterable[PppEvent]) -> dict[tuple[str, str], int]:
    """(user, reason) -> count, for the ``routeros_ppp_disconnect_total`` metric.

    Reason is bucketed to a short slug so label cardinality stays bounded
    regardless of how chatty RouterOS is.
    """
    counts: dict[tuple[str, str], int] = {}
    for e in events:
        if e.kind != "disconnect":
            continue
        counts[(e.user, bucket_reason(e.reason))] = counts.get(
            (e.user, bucket_reason(e.reason)), 0
        ) + 1
    return counts


_REASON_BUCKETS = (
    ("timeout", ("timeout", "no activity", "keepalive")),
    ("peer-disconnect", ("closed by peer", "hangup", "peer", "user request")),
    ("auth", ("auth", "password", "chap", "pap")),
    ("link", ("interface", "link", "lcp", "ncp", "terminated by")),
    ("admin", ("removed", "disabled", "by admin", "kill")),
)


def bucket_reason(reason: str) -> str:
    r = (reason or "").lower()
    if not r:
        return "unspecified"
    for slug, needles in _REASON_BUCKETS:
        if any(n in r for n in needles):
            return slug
    return "other"
