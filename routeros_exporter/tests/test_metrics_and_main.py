from prometheus_client import CollectorRegistry, generate_latest

from routeros_exporter import config as cfg
from routeros_exporter.collectors import ppp as ppp_mod
from routeros_exporter.collectors.system import parse_system
from routeros_exporter.metrics import Metrics

from .conftest import load


def _text(m: Metrics) -> str:
    return generate_latest(m.registry).decode()


def test_update_ppp_emits_expected_series():
    m = Metrics(CollectorRegistry())
    active = ppp_mod.parse_active(load("ppp_active.json"))
    secrets = ppp_mod.parse_secrets(load("ppp_secret.json"))
    rows = load("log_pppoe.json")
    m.update_ppp("pop1", active, secrets, ppp_mod.parse_log_events(rows), rows)

    out = _text(m)
    assert 'routeros_ppp_active_total{router="pop1"} 4.0' in out
    assert 'routeros_ppp_secret_total{router="pop1"} 5.0' in out
    assert 'routeros_ppp_active_caller_id_info{address="100.64.12.7",caller_id="48:8f:5a:2b:1c:0d",router="pop1",user="adebayo-01"} 1.0' in out
    assert 'routeros_ppp_auth_failure_total{router="pop1"} 1.0' in out
    assert 'reason="timeout"' in out


def test_update_ppp_counter_dedupes_on_repeat_poll():
    m = Metrics(CollectorRegistry())
    rows = load("log_pppoe.json")
    for _ in range(3):  # same log window scraped 3x
        m.update_ppp("pop1", [], [], ppp_mod.parse_log_events(rows), rows)
    out = _text(m)
    # still 1, not 3 - de-duped by log .id
    assert 'routeros_ppp_auth_failure_total{router="pop1"} 1.0' in out


def test_stale_session_series_removed():
    m = Metrics(CollectorRegistry())
    active = ppp_mod.parse_active(load("ppp_active.json"))
    m.update_ppp("pop1", active, [], [], [])
    assert 'user="adebayo-01"' in _text(m)
    m.update_ppp("pop1", [], [], [], [])  # everyone disconnected
    assert 'routeros_ppp_active{' not in _text(m)


def test_system_info_metric():
    m = Metrics(CollectorRegistry())
    m.update_system("pop1", parse_system(load("system_resource.json")))
    out = _text(m)
    assert 'version="7.15.3"' in out
    assert 'board="RB1100AHx4"' in out


def test_config_load_from_example(tmp_path):
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(
        "options:\n  listen_port: 9999\n"
        "routers:\n  - name: pop1\n    host: 192.168.10.1\n    pop: HOME\n"
    )
    creds = tmp_path / "creds.json"
    creds.write_text('{"pop1": {"username": "ro", "password": "x"}}')
    conf = cfg.load(str(cfg_path), str(creds))
    assert conf.listen_port == 9999
    assert conf.routers[0].pop == "HOME"
    assert conf.routers[0].username == "ro"
    assert conf.routers[0].labels == {"router": "pop1", "pop": "HOME", "role": "core-router"}
