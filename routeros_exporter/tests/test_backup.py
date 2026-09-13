import subprocess

import pytest

from routeros_exporter import backup, config as cfg
from routeros_exporter.client import RouterOSError

from .conftest import FIXTURES


class FakeFTP:
    """Stands in for ftplib.FTP. `calls` records what happened, in order,
    for assertions; `content` / `fail_retr` / `fail_delete` steer behaviour."""

    def __init__(self, content: bytes = b"", fail_retr: bool = False, fail_delete: bool = False):
        self.calls = []
        self._content = content
        self._fail_retr = fail_retr
        self._fail_delete = fail_delete

    def connect(self, host, port, timeout):
        self.calls.append(("connect", host, port, timeout))

    def login(self, username, password):
        self.calls.append(("login", username, password))

    def retrbinary(self, cmd, write_fn):
        self.calls.append(("retrbinary", cmd))
        if self._fail_retr:
            import ftplib
            raise ftplib.error_perm("550 No such file")
        write_fn(self._content)

    def delete(self, name):
        self.calls.append(("delete", name))
        if self._fail_delete:
            import ftplib
            raise ftplib.error_perm("550 permission denied")

    def quit(self):
        self.calls.append(("quit",))


def _log(repo):
    return subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline"],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()


def test_first_backup_then_idempotent_then_change(tmp_path):
    repo = backup.ensure_repo(str(tmp_path / "backups"))
    export_v1 = (FIXTURES / "export.rsc").read_text()

    r1 = backup.back_up_router("pop1", export_v1, repo)
    assert r1.success and r1.changed is False  # first backup: not an alertable change
    assert (repo / "pop1.rsc").exists()
    assert len(_log(repo)) == 2  # init + first backup

    # Same config, only the volatile timestamp header differs -> no commit.
    export_same = export_v1.replace("01:00:00", "02:00:00")
    r2 = backup.back_up_router("pop1", export_same, repo)
    assert r2.success and r2.changed is False
    assert len(_log(repo)) == 2

    # A real change -> commit + changed=True + a diff summary.
    export_v2 = export_v1 + '\nadd action=drop chain=forward comment="block p2p"\n'
    r3 = backup.back_up_router("pop1", export_v2, repo)
    assert r3.success and r3.changed is True
    assert "+" in r3.diff_summary
    assert len(_log(repo)) == 3
    assert "pop1: config changed" in _log(repo)[0]


class _RecordingClient:
    """Stands in for client.APIClient - only `command` is used by
    export_via_api, to trigger `/export file=...`."""

    def __init__(self):
        self.calls = []

    def command(self, path, **kw):
        self.calls.append((path, kw))
        return []  # /export file=... returns nothing useful over the API


def _router(**overrides):
    return cfg.RouterConfig(
        name="pop1", host="192.168.10.1", username="prom-ro", password="x", **overrides
    )


def test_export_via_api_triggers_file_export_then_ftp_fetch(monkeypatch):
    """CONFIRMED 2026-09-13 against a live RouterOS 6.49 router: the API
    can trigger `/export file=...` (needs `write`) but cannot read a file's
    contents back on this ROS version - only FTP (needs `ftp`) can."""
    fake = FakeFTP(content=b"/ip address\nadd address=1.2.3.4/24\n")
    monkeypatch.setattr(backup.ftplib, "FTP", lambda: fake)

    client = _RecordingClient()
    text = backup.export_via_api(client, _router())

    assert text == "/ip address\nadd address=1.2.3.4/24\n"
    assert client.calls == [("/export", {"file": "routeros-exporter-backup"})]
    assert ("connect", "192.168.10.1", 21, 15.0) in fake.calls
    assert ("login", "prom-ro", "x") in fake.calls
    assert ("retrbinary", "RETR routeros-exporter-backup.rsc") in fake.calls
    assert ("delete", "routeros-exporter-backup.rsc") in fake.calls
    assert ("quit",) in fake.calls


def test_export_via_api_uses_configured_ftp_port_and_timeout(monkeypatch):
    fake = FakeFTP(content=b"ok")
    monkeypatch.setattr(backup.ftplib, "FTP", lambda: fake)

    backup.export_via_api(_RecordingClient(), _router(ftp_port=2121), ftp_timeout=5.0)

    assert ("connect", "192.168.10.1", 2121, 5.0) in fake.calls


def test_export_via_api_survives_a_failed_cleanup_delete(monkeypatch):
    """A failed best-effort delete must not sink an otherwise-good fetch -
    the fixed filename is overwritten next cycle regardless."""
    fake = FakeFTP(content=b"/ip address\n", fail_delete=True)
    monkeypatch.setattr(backup.ftplib, "FTP", lambda: fake)

    text = backup.export_via_api(_RecordingClient(), _router())

    assert text == "/ip address\n"


def test_export_via_api_raises_routeros_error_on_ftp_failure(monkeypatch):
    fake = FakeFTP(fail_retr=True)
    monkeypatch.setattr(backup.ftplib, "FTP", lambda: fake)

    with pytest.raises(RouterOSError, match="FTP fetch"):
        backup.export_via_api(_RecordingClient(), _router())
