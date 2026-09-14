"""Config backup + drift detection.

Per router, per cycle:
  1. Trigger ``/export file=...`` over the API, then fetch + delete that
     file over FTP (see ``export_via_api`` - RouterOS 6.x's API has no way
     to read a file's contents back, only newer 7.x builds do).
  2. Write it to ``<repo>/<router>.rsc`` and ``git commit`` if it changed.
  3. Emit metrics so Prometheus can alert:
       routeros_config_last_backup_timestamp{router}
       routeros_config_last_change_timestamp{router}
       routeros_config_changed{router}         1 for the cycle a diff landed
       routeros_config_export_lines{router}
       routeros_config_backup_success{router}

The git repo is a plain ``git init`` directory on its own volume - restoring
is ``git show <rev>:<router>.rsc``. This is what drift-detection and the
change diff work from.

CORRECTION, CONFIRMED 2026-09-14 against a real router's real export: on
RouterOS 6.x, ``/export`` does NOT mask passwords/secrets by default - a
live export came back with a WiFi PSK in plain text. (The earlier
assumption here was wrong; it likely conflated this with `show-sensitive`,
which only *exists* on 7.x and, per its name, is for *showing* sensitive
values that 7.x hides by default - 6.x apparently never hid them at all.)
**Treat the backup git repo/volume as containing real secrets, not just
structural config** - it needs the same handling as the credentials file
it's adjacent to: never commit it, don't expose the volume, restrict who
can read it. A true DR restore image (binary ``/system/backup/save``) is
still a separate, not-implemented follow-up - see routeros_exporter/README.md.

RouterOS ``/export`` output contains a first-line timestamp comment that
changes every run; it's stripped before diffing so an unchanged config
doesn't look changed every cycle.

REQUIRES a RouterOS user with more than read-only access - CONFIRMED
2026-09-13 (see the README's permissions history): a bare ``/export`` over
the API hangs indefinitely for a read-only user, and ``/export file=...``
(the only variant that replies) needs the ``write`` policy to create the
file. Chosen tradeoff (2026-09-13, by the user, over the alternative of a
router-side scheduler + a permanently read-only credential): widen the one
monitoring credential to a custom group scoped to exactly
``api, read, write, ftp`` - never ``password``, ``sensitive``, ``policy``,
``reboot``, or ``sniff`` - so it can create and fetch its own export file
but nothing more dangerous. Exact RouterOS commands in the README.
"""

from __future__ import annotations

import contextlib
import dataclasses
import ftplib
import io
import pathlib
import re
import subprocess
import time
from typing import Callable

from .client import RouterOSError

# Fixed name so every cycle overwrites the same file rather than
# accumulating one per poll - RouterOS appends .rsc to whatever `file=`
# names (CONFIRMED 2026-09-13 against a live 6.49 router).
_BACKUP_EXPORT_FILENAME = "routeros-exporter-backup"

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


def export_via_api(client, router, *, ftp_timeout: float = 90.0) -> str:
    """Trigger ``/export file=...`` over the API, then fetch + delete that
    file over FTP.

    Two round trips against two protocols, both needed:
      1. API ``/export file=<name>`` - CONFIRMED 2026-09-14: takes ~52s to
         complete on a hAP AC Lite once the credential actually has
         ``write`` - not a hang, just slow (a bare `/export` over the API
         hangs indefinitely for a read-only user instead of erroring - see
         the module docstring). An earlier "replies in under 2s" reading
         was wrong: that was RouterOS failing the *permission check*
         quickly, before ever attempting the real export - it told us
         nothing about how long a successful one takes. Caller should pass
         a generous timeout (`Config.backup_timeout_seconds`, not the
         faster `api_timeout_seconds` routine polling uses).
      2. FTP RETR of ``<name>.rsc`` - RouterOS 6.x's API has no "read a
         file's contents" call; only 7.x builds added that. FTP is what
         every RouterOS version has always supported for this. Needs the
         ``ftp`` policy on top of ``write``.

    ``show-sensitive`` is deliberately not passed: it only exists on
    RouterOS 7.x (CONFIRMED 2026-09-12: a 6.49 router rejects it as an
    "unknown parameter"). CORRECTION 2026-09-14: `/export` does NOT mask
    passwords/secrets by default on 6.x either - a live export came back
    with a WiFi PSK in plain text. This is a real credential-bearing
    artifact; see the module docstring's note on treating the backup repo
    accordingly.
    """
    client.command("/export", file=_BACKUP_EXPORT_FILENAME)
    remote_name = f"{_BACKUP_EXPORT_FILENAME}.rsc"
    return _ftp_fetch_and_delete(
        router.host, router.username, router.password, remote_name,
        port=router.ftp_port, timeout=ftp_timeout,
    )


def _ftp_fetch_and_delete(
    host: str, username: str, password: str, remote_name: str,
    *, port: int = 21, timeout: float = 15.0,
) -> str:
    buf = io.BytesIO()
    ftp = ftplib.FTP()
    try:
        ftp.connect(host, port, timeout=timeout)
        ftp.login(username, password)
        ftp.retrbinary(f"RETR {remote_name}", buf.write)
        with contextlib.suppress(ftplib.all_errors):
            # Best-effort: the fixed filename is overwritten next cycle
            # regardless, so a failed delete here isn't fatal - don't let
            # router-side cleanup failure sink an otherwise-good backup.
            ftp.delete(remote_name)
    except ftplib.all_errors as exc:
        raise RouterOSError(f"{host}: FTP fetch of {remote_name} failed: {exc}") from exc
    finally:
        with contextlib.suppress(Exception):
            ftp.quit()
    return buf.getvalue().decode("utf-8", errors="replace")
