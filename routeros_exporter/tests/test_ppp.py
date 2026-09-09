from routeros_exporter.collectors import ppp

from .conftest import load


def test_parse_duration():
    assert ppp.parse_duration("6w3d10h11m35s") == 3924695
    assert ppp.parse_duration("1h3m") == 3780
    assert ppp.parse_duration("22s") == 22
    assert ppp.parse_duration("") == 0
    assert ppp.parse_duration(None) == 0


def test_normalize_mac_variants():
    assert ppp.normalize_mac("48:8F:5A:2B:1C:0D") == "48:8f:5a:2b:1c:0d"
    assert ppp.normalize_mac("e4-8d-8c-11-22-33") == "e4:8d:8c:11:22:33"
    assert ppp.normalize_mac("AABBCCDDEEFF") == "aa:bb:cc:dd:ee:ff"
    assert ppp.normalize_mac("") == ""


def test_parse_active_sessions():
    sessions = ppp.parse_active(load("ppp_active.json"))
    by_user = {s.user: s for s in sessions}
    assert by_user["adebayo-01"].caller_id == "48:8f:5a:2b:1c:0d"
    assert by_user["adebayo-01"].service == "pppoe"
    assert by_user["adebayo-01"].uptime_seconds == 6 * 86400 + 4 * 3600 + 11 * 60 + 2
    # non-pppoe caller-id is left as-is (it's a source IP, not a MAC)
    assert by_user["site-vpn-base"].caller_id == "41.58.10.2"


def test_parse_secrets_disabled_flag():
    secrets = ppp.parse_secrets(load("ppp_secret.json"))
    by_user = {s.user: s for s in secrets}
    assert by_user["old-tenant"].disabled is True
    assert by_user["adebayo-01"].disabled is False
    assert sum(1 for s in secrets if not s.disabled) == 5


def test_log_event_classification():
    events = ppp.parse_log_events(load("log_pppoe.json"))
    kinds = [(e.kind, e.user) for e in events]
    assert ("auth-failure", "okoro-shop") in kinds
    assert ("disconnect", "chinedu-house") in kinds
    assert ("disconnect", "adaeze-flat") in kinds
    assert ("connect", "adebayo-01") in kinds
    # the winbox login line (topics: system,account) must NOT be picked up
    assert all(u != "admin" for _, u in kinds)


def test_disconnect_reason_bucketing():
    events = ppp.parse_log_events(load("log_pppoe.json"))
    summary = ppp.summarize_disconnect_reasons(events)
    assert summary[("chinedu-house", "timeout")] == 1
    assert summary[("adaeze-flat", "peer-disconnect")] == 1
    assert ppp.bucket_reason("peer is not responding") == "peer-disconnect"
    assert ppp.bucket_reason("") == "unspecified"
    assert ppp.bucket_reason("some novel wording") == "other"
