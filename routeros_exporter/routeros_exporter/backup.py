"""Config backup + drift detection.

Per router, per cycle:
  1. Pull the full config text via ``/export`` (sensitive values hidden).
  2. Write it to ``<repo>/<router>.rsc`` and ``git commit`` if it changed.
  3. Emit metrics so Prometheus can alert:
       routeros_config_last_backup_timestamp{router}
       routeros_config_last_change_timestamp{router}
       routeros_config_changed{router}         1 for the cycle a diff landed
       routeros_config_export_lines{router}
       routeros_config_backup_success{router}

The git repo is a plain ``git init`` directory on its own volume - restoring
is ``git show <rev>:<router>.rsc``. This is what drift-detection and the
change diff work from; RouterOS masks passwords/secrets in ``/export`` by
default, so it is NOT a full credential-recovery image. A true restore
(binary ``/system/backup/save``, which does carry secrets) is a documented
follow-up, not implemented yet - see routeros_exporter/README.md.

RouterOS ``/export`` output contains a first-line timestamp comment that
changes every run; it's stripped before diffing so an unchanged config
doesn't look changed every cycle.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import subprocess
import time
from typing import Callable

from .client import RouterOSError

_TS_COMMENT_RE = re.compile(r"^# [0-9]{4}-[0-9]{2}-[0-9]{2} .*$", re.MULTILINE)
_ROS_VERSION_COMMENT_RE = re.compile(r"^# software id =.*$|^# model =.*$|^# serial number =.*$", re.MULTILINE)


def _canonical(export_text: str) -> str:
    """Strip the volatile header lines RouterOS stamps on every /export."""
    text = _TS_COMMENT_RE.sub("# <timestamp stripped>", export_text)
    return text.strip() + "\n"


@dataclasses.dataclass
class BackupResult:
    router: str
    success: bool
    changed: bool
    export_lines: int
    diff_summary: str      # short "+N/-M lines" or ""
    error: str = ""


def _run_git(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def ensure_repo(repo_path: str) -> pathlib.Path:
    repo = pathlib.Path(repo_path)
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        _run_git(repo, "init", "-q")
        _run_git(repo, "config", "user.email", "routeros-exporter@localhost")
        _run_git(repo, "config", "user.name", "routeros-exporter")
        (repo / "README.md").write_text(
            "# RouterOS config backups\n\nOne `<router>.rsc` per device, "
            "committed on every change by routeros_exporter. Restore a prior "
            "version with `git show <rev>:<router>.rsc`.\n",
            encoding="utf-8",
        )
        _run_git(repo, "add", "README.md")
        _run_git(repo, "commit", "-q", "-m", "init backup repo")
    return repo


def _diff_summary(repo: pathlib.Path, rel: str) -> str:
    p = _run_git(repo, "diff", "--cached", "--numstat", "--", rel)
    line = (p.stdout or "").strip().splitlines()
    if not line:
        return ""
    added, removed, *_ = (line[0].split("\t") + ["0", "0"])[:2]
    return f"+{added}/-{removed} lines"


def back_up_router(
    router_name: str,
    export_text: str,
    repo: pathlib.Path,
    *,
    now: Callable[[], float] = time.time,
) -> BackupResult:
    """Write + commit one router's export. Pure enough to unit-test: pass the
    export text and a temp repo, assert on the returned BackupResult and the
    git log."""
    rel = f"{router_name}.rsc"
    target = repo / rel
    canonical = _canonical(export_text)
    export_lines = canonical.count("\n")

    previous = target.read_text(encoding="utf-8") if target.exists() else None
    if previous == canonical:
        return BackupResult(router_name, True, False, export_lines, "")

    target.write_text(canonical, encoding="utf-8")
    _run_git(repo, "add", rel)
    summary = _diff_summary(repo, rel) if previous is not None else "new file"
    msg = (
        f"{router_name}: config {'changed' if previous is not None else 'first backup'}"
        f"{' (' + summary + ')' if summary else ''}"
    )
    commit = _run_git(repo, "commit", "-q", "-m", msg)
    if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr):
        return BackupResult(
            router_name, False, False, export_lines, "", error=commit.stderr.strip()
        )
    return BackupResult(
        router_name,
        True,
        previous is not None,   # first-ever backup is not a "change" to alert on
        export_lines,
        summary,
    )


def export_via_api(client) -> str:
    """Run ``/export`` over the API and join the returned rows into text.

    ``show-sensitive`` (explicitly mask passwords/secrets) only exists on
    RouterOS 7.x - CONFIRMED (2026-09-12) a 6.49 router rejects it with
    "unknown parameter". Rather than branch on version, try it first and
    fall back to a bare ``/export`` when the parameter is unknown, so this
    works unmodified across the 6.x/7.x fleet. Either way the output keeps
    RouterOS's own default sensitive-value masking (passwords render as
    ``***``) - this backup is for structural diffing/audit, not credential
    recovery; see routeros_exporter/README.md.

    CONFIRMED (2026-09-12), also against a live 6.49 router: the bare
    fallback does not actually work with a read-only API user either - it
    hangs for the full connection timeout (no reply at all, not even an
    error) rather than raising quickly. A read-only ``read``-group user can
    only get `/export` to reply promptly by adding ``file=<name>``, which
    then needs the ``write`` policy to create the file - a real permissions
    tradeoff this repo hasn't resolved yet (see the README). Until it is,
    expect this call to cost up to the full ``api_timeout_seconds`` on every
    router that only has a read-only credential, once per backup cycle.
    """
    try:
        rows = client.command("/export", **{"show-sensitive": "no"})
    except RouterOSError as exc:
        if "unknown parameter" not in str(exc).lower():
            raise
        rows = client.command("/export")

    if len(rows) == 1 and "ret" in rows[0]:
        return str(rows[0]["ret"])
    return "\n".join(
        str(r.get("line", r.get("ret", ""))) for r in rows
    )
