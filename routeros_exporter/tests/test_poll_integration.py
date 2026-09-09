"""End-to-end poll of one router with the network layer faked out."""

import contextlib

from prometheus_client import CollectorRegistry, generate_latest

from routeros_exporter import __main__ as main_mod
from routeros_exporter import client as roc
from routeros_exporter import config as cfg
from routeros_exporter.metrics import Metrics

from .conftest import FakeClient, load


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
            "/export": [{"ret": "/ip address\nadd address=100.64.0.1/24\n"}],
        }
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

    main_mod.poll_router(
        router, conf, m, do_backup=True, do_topology=True, repo=repo,
        router_addresses={"pop1": ["10.0.0.1"]},
    )

    out = generate_latest(m.registry).decode()
    assert 'routeros_scrape_success{router="pop1"} 1.0' in out
    assert 'routeros_ppp_active_total{router="pop1"} 4.0' in out
    assert 'routeros_ppp_secret_total{router="pop1"} 5.0' in out
    assert 'routeros_system_info{architecture="arm",board="RB1100AHx4",router="pop1",version="7.15.3"} 1.0' in out
    assert 'routeros_config_backup_success{router="pop1"} 1.0' in out
    assert 'routeros_config_export_lines{router="pop1"}' in out
    assert 'routeros_topology_edge{' in out
    assert 'routeros_router_info{pop="BASE",role="core-router",router="pop1",site="BASE"} 1.0' in out
