"""End-to-end poll of one router with the network layer faked out."""

import contextlib

from prometheus_client import CollectorRegistry, generate_latest

from routeros_exporter import __main__ as main_mod
from routeros_exporter import backup
from routeros_exporter import client as roc
from routeros_exporter import config as cfg
from routeros_exporter.client import RouterOSError
from routeros_exporter.metrics import Metrics

from .conftest import FakeClient, load
from .test_backup import FakeFTP


def test_poll_router_populates_all_metric_families(tmp_path, monkeypatch):
    fake = FakeClient(
        {
            "/ppp/active/print": load("ppp_active.json"),
            "/ppp/secret/print": load("ppp_secret.json"),
            "/log/print": load("log_pppoe.json"),
            "/system/resource/print": load("system_resource.json"),
            "/system/routerboard/print": [],
            "/ip/route/print": load("ip_route.json"),
            "/ip/neighbor/print": load("ip_neighbor.json"),
        }
    )
    # export_via_api triggers /export over the API (FakeClient.command
    # ignores the file= kwarg and returns [], which is realistic - the
    # actual content comes back over FTP, faked here.
    monkeypatch.setattr(
        backup.ftplib, "FTP",
        lambda: FakeFTP(content=b"/ip address\nadd address=100.64.0.1/24\n"),
    )

    @contextlib.contextmanager
    def fake_connect(router):
        yield fake

    monkeypatch.setattr(roc, "connect", fake_connect)
    monkeypatch.setattr(main_mod.roc, "connect", fake_connect)

    router = cfg.RouterConfig(name="pop1", host="10.0.0.1", username="ro", password="x", pop="BASE", site="BASE")
    conf = cfg.Config(routers=(router,), backup_repo_path=str(tmp_path / "b"))
    m = Metrics(CollectorRegistry())
    m.set_router_info(router.name, router.site, router.pop, router.role)
    repo = main_mod.ensure_repo(conf.backup_repo_path)

    outcome = main_mod.poll_router(
        router, conf, m, do_backup=True, do_topology=True, repo=repo,
        router_addresses={"pop1": ["10.0.0.1"]},
    )

    assert outcome == main_mod.PollOutcome(ok=True, backup_ok=True, topology_ok=True)
    out = generate_latest(m.registry).decode()
    assert 'routeros_scrape_success{router="pop1"} 1.0' in out
    assert 'routeros_ppp_active_total{router="pop1"} 4.0' in out
    assert 'routeros_ppp_secret_total{router="pop1"} 5.0' in out
    assert 'routeros_system_info{architecture="arm",board="RB1100AHx4",router="pop1",version="7.15.3"} 1.0' in out
    assert 'routeros_config_backup_success{router="pop1"} 1.0' in out
    assert 'routeros_config_export_lines{router="pop1"}' in out
    assert 'routeros_topology_edge{' in out
    assert 'routeros_router_info{pop="BASE",role="core-router",router="pop1",site="BASE"} 1.0' in out


def test_backup_failure_does_not_sink_scrape_success(tmp_path, monkeypatch):
    """CONFIRMED against a live RouterOS 6.49 router (2026-09-12/13): /export
    can fail independently of PPP/system data being fine - both because the
    credential lacked write/ftp, and later because the router was simply
    slow to respond. Neither should make routeros_scrape_success (and
    therefore RouterOSAPICollectorDown) lie about the core poll being down,
    and the caller must be able to tell backup specifically failed (so it
    retries next cycle instead of waiting a full hour - see PollOutcome)."""

    class BrokenExportClient(FakeClient):
        def command(self, path, **params):
            raise RouterOSError("192.168.10.1: command /export failed: unknown parameter")

    fake = BrokenExportClient(
        {
            "/ppp/active/print": load("ppp_active.json"),
            "/ppp/secret/print": load("ppp_secret.json"),
            "/log/print": load("log_pppoe.json"),
            "/system/resource/print": load("system_resource.json"),
            "/system/routerboard/print": [],
            "/ip/route/print": load("ip_route.json"),
            "/ip/neighbor/print": load("ip_neighbor.json"),
        }
    )

    @contextlib.contextmanager
    def fake_connect(router):
        yield fake

    monkeypatch.setattr(main_mod.roc, "connect", fake_connect)

    router = cfg.RouterConfig(name="pop1", host="10.0.0.1", username="ro", password="x")
    conf = cfg.Config(routers=(router,), backup_repo_path=str(tmp_path / "b"))
    m = Metrics(CollectorRegistry())
    repo = main_mod.ensure_repo(conf.backup_repo_path)

    outcome = main_mod.poll_router(
        router, conf, m, do_backup=True, do_topology=True, repo=repo,
        router_addresses={"pop1": ["10.0.0.1"]},
    )

    assert outcome.ok is True          # core poll fine even though the backup sub-step failed
    assert outcome.backup_ok is False  # ... but the caller must know backup specifically failed
    assert outcome.topology_ok is True
    out = generate_latest(m.registry).decode()
    assert 'routeros_scrape_success{router="pop1"} 1.0' in out       # PPP/system still fine
    assert 'routeros_config_backup_success{router="pop1"} 0.0' in out  # backup alone failed
    assert 'routeros_ppp_active_total{router="pop1"} 4.0' in out


def test_poll_router_returns_false_on_connect_failure(tmp_path, monkeypatch):
    """CONFIRMED 2026-09-13: a router unreachable exactly when its shared
    backup/topology window came up silently missed the ENTIRE hour, because
    run() advanced the schedule regardless of whether the poll actually got
    anywhere. poll_router's return value is what run() now checks before
    consuming that router's slot - this pins the return value the fix
    depends on."""

    @contextlib.contextmanager
    def failing_connect(router):
        raise RouterOSError(f"{router.host}: API connect failed: connection refused")
        yield  # pragma: no cover - unreachable, contextmanager needs a yield

    monkeypatch.setattr(main_mod.roc, "connect", failing_connect)

    router = cfg.RouterConfig(name="pop1", host="10.0.0.1", username="ro", password="x")
    conf = cfg.Config(routers=(router,), backup_repo_path=str(tmp_path / "b"))
    m = Metrics(CollectorRegistry())
    repo = main_mod.ensure_repo(conf.backup_repo_path)

    outcome = main_mod.poll_router(
        router, conf, m, do_backup=True, do_topology=True, repo=repo,
        router_addresses={"pop1": ["10.0.0.1"]},
    )

    assert outcome.ok is False
    out = generate_latest(m.registry).decode()
    assert 'routeros_scrape_success{router="pop1"} 0.0' in out
    # Neither metric was even attempted this cycle - absent, not "0".
    assert 'routeros_config_backup_success{router="pop1"}' not in out
