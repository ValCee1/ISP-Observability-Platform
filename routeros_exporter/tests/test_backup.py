import subprocess

import pytest

from routeros_exporter import backup
from routeros_exporter.client import RouterOSError

from .conftest import FIXTURES


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


def test_export_via_api_row_shapes():
    class C:
        def __init__(self, rows):
            self.rows = rows

        def command(self, path, **kw):
            return self.rows

    assert backup.export_via_api(C([{"ret": "/ip address\nadd address=1.2.3.4/24"}])).startswith("/ip address")
    joined = backup.export_via_api(C([{"line": "/ppp profile"}, {"line": "add name=x"}]))
    assert joined == "/ppp profile\nadd name=x"


def test_export_via_api_falls_back_on_routeros_6x():
    """RouterOS 6.49 rejects `show-sensitive` ("unknown parameter") -
    CONFIRMED against a live router 2026-09-12. export_via_api must retry
    without it rather than propagate the error."""

    class C:
        def __init__(self):
            self.calls = []

        def command(self, path, **kw):
            self.calls.append(kw)
            if kw:
                raise RouterOSError("192.168.10.1: command /export failed: unknown parameter")
            return [{"ret": "/ip address\nadd address=1.2.3.4/24"}]

    c = C()
    assert backup.export_via_api(c).startswith("/ip address")
    assert c.calls == [{"show-sensitive": "no"}, {}]


def test_export_via_api_reraises_unrelated_errors():
    class C:
        def command(self, path, **kw):
            raise RouterOSError("192.168.10.1: connection refused")

    with pytest.raises(RouterOSError, match="connection refused"):
        backup.export_via_api(C())
