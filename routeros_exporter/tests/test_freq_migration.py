from routeros_exporter.freq_migration import config as fmc
from routeros_exporter.freq_migration import discovery as disc
from routeros_exporter.freq_migration import migrate as mig
from routeros_exporter.freq_migration import scanlist as sl
from routeros_exporter.freq_migration import spectrum as spec
from routeros_exporter.freq_migration.cli import run_migration


def test_config_load_joins_sector_inventory_with_credentials(tmp_path):
    sectors_yml = tmp_path / "sectors.yml"
    sectors_yml.write_text(
        "sectors:\n"
        "  - name: sector1-tower\n"
        "    host: 192.168.20.1\n"
        "    freq_min_mhz: 5745\n"
        "    freq_max_mhz: 5825\n"
    )
    creds_json = tmp_path / "creds.json"
    creds_json.write_text(
        '{"cpe_shared": {"username": "cpeuser", "password": "cpepass"}, '
        '"sectors": {"sector1-tower": {"username": "sectoruser", "password": "sectorpass"}}}'
    )

    sectors, cpe_cred = fmc.load(str(sectors_yml), str(creds_json))

    assert cpe_cred.username == "cpeuser" and cpe_cred.password == "cpepass"
    sector = fmc.find_sector(sectors, "sector1-tower")
    assert sector.host == "192.168.20.1"
    assert sector.username == "sectoruser" and sector.password == "sectorpass"
    assert sector.wireless_interface == "wlan1"  # default


def test_find_sector_raises_on_unknown_name():
    import pytest

    with pytest.raises(KeyError):
        fmc.find_sector([], "nope")


class FakeClient:
    """Same shape as the conftest.py FakeClient - maps API path -> rows,
    plus records every `command()` call for assertions."""

    def __init__(self, mapping: dict):
        self._mapping = mapping
        self.commands = []

    def query(self, path, **where):
        return list(self._mapping.get(path.rstrip("/"), []))

    def command(self, path, **params):
        self.commands.append((path.rstrip("/"), params))
        return list(self._mapping.get(path.rstrip("/"), []))

    def close(self):
        pass


# --------------------------------------------------------------------------
# Step 1: discovery
# --------------------------------------------------------------------------

REG_TABLE = [
    {"mac-address": "AA:BB:CC:00:00:01", "radio-name": "cpe-1", "signal-strength": "-60", "tx-ccq": "0"},
    {"mac-address": "AA:BB:CC:00:00:02", "radio-name": "cpe-2", "signal-strength": "-70", "tx-ccq": "0"},
]

NEIGHBORS = [
    {"interface": "wlan1", "identity": "cpe-1", "address": "192.168.20.11", "mac-address": "aa:bb:cc:00:00:01"},
    # cpe-2 deliberately has no MNDP entry - simulates an unreachable CPE.
]


def test_discover_cpes_joins_registration_table_with_mndp_for_ip():
    client = FakeClient(
        {
            "/interface/wireless/registration-table/print": REG_TABLE,
            "/ip/neighbor/print": NEIGHBORS,
        }
    )
    cpes = disc.discover_cpes(client, "wlan1")

    assert len(cpes) == 2
    by_mac = {c.mac: c for c in cpes}
    assert by_mac["aa:bb:cc:00:00:01"].management_ip == "192.168.20.11"
    assert by_mac["aa:bb:cc:00:00:02"].management_ip is None
    assert by_mac["aa:bb:cc:00:00:01"].signal_dbm == -60.0


# --------------------------------------------------------------------------
# Step 2: spectrum
# --------------------------------------------------------------------------

def test_parse_spectral_scan_uses_first_known_power_key():
    rows = [{"freq": "5745", "range0": "-90"}, {"freq": "5765", "power": "-95"}]
    readings = spec.parse_spectral_scan(rows)
    assert readings[0].frequency_mhz == 5745 and readings[0].power_dbm == -90.0
    assert readings[1].frequency_mhz == 5765 and readings[1].power_dbm == -95.0


def test_parse_spectral_scan_raises_on_unknown_schema():
    import pytest

    with pytest.raises(ValueError, match="power keys"):
        spec.parse_spectral_scan([{"freq": "5745", "mystery-column": "-90"}])


def test_choose_frequencies_picks_cleanest_and_avoids_overlap():
    readings = [
        spec.FrequencyReading(5745, -70),   # noisiest
        spec.FrequencyReading(5765, -95),   # cleanest
        spec.FrequencyReading(5775, -94),   # close to 5765, within width -> skipped
        spec.FrequencyReading(5805, -90),   # next-best, far enough from 5765
        spec.FrequencyReading(5825, -60),
    ]
    plan = spec.choose_frequencies(
        readings,
        current_frequency_mhz=5745,
        freq_min_mhz=5745,
        freq_max_mhz=5825,
        channel_width_mhz=20,
        fallback_count=1,
    )
    assert plan.old_frequency_mhz == 5745
    assert plan.new_frequency_mhz == 5765
    assert plan.fallback_frequencies_mhz == [5805]
    assert plan.no_change_recommended is False
    assert plan.scan_list == [5745, 5765, 5805]


def test_choose_frequencies_flags_when_current_is_already_cleanest():
    readings = [spec.FrequencyReading(5745, -95), spec.FrequencyReading(5805, -70)]
    plan = spec.choose_frequencies(
        readings, current_frequency_mhz=5745, freq_min_mhz=5745, freq_max_mhz=5825,
        channel_width_mhz=20, fallback_count=0,
    )
    assert plan.new_frequency_mhz == 5745
    assert plan.no_change_recommended is True


def test_choose_frequencies_rejects_readings_outside_band():
    import pytest

    readings = [spec.FrequencyReading(2412, -90)]
    with pytest.raises(ValueError, match="allowed band"):
        spec.choose_frequencies(
            readings, current_frequency_mhz=5745, freq_min_mhz=5745, freq_max_mhz=5825,
            channel_width_mhz=20, fallback_count=0,
        )


# --------------------------------------------------------------------------
# Step 3: scanlist
# --------------------------------------------------------------------------

def test_targets_from_discovery_splits_reachable_and_unreachable():
    cpes = disc.discover_cpes(
        FakeClient(
            {"/interface/wireless/registration-table/print": REG_TABLE, "/ip/neighbor/print": NEIGHBORS}
        ),
        "wlan1",
    )
    reachable, unreachable = sl.targets_from_discovery(cpes, username="u", password="p")
    assert [t.host for t in reachable] == ["192.168.20.11"]
    assert [c.mac for c in unreachable] == ["aa:bb:cc:00:00:02"]


def test_push_scan_list_sets_the_wireless_interface_by_id(monkeypatch):
    fake = FakeClient({"/interface/wireless/print": [{".id": "*3", "name": "wlan1"}]})
    monkeypatch.setattr("routeros_exporter.freq_migration.scanlist.connect", lambda target, timeout: _ctx(fake))

    target = sl.CPETarget(mac="aa:bb:cc:00:00:01", host="192.168.20.11", username="u", password="p")
    result = sl.push_scan_list(target, cpe_wireless_interface="wlan1", frequencies_mhz=[5745, 5765, 5805])

    assert result.success is True
    assert ("/interface/wireless/set", {"numbers": "*3", "scan-list": "5745,5765,5805"}) in fake.commands


class _ctx:
    """Minimal context manager stand-in for client.connect()."""

    def __init__(self, client):
        self._client = client

    def __enter__(self):
        return self._client

    def __exit__(self, *exc):
        return False


def test_push_scan_list_reports_failure_without_raising(monkeypatch):
    from routeros_exporter.client import RouterOSError

    def _boom(target, timeout):
        raise RouterOSError("connect failed")

    monkeypatch.setattr("routeros_exporter.freq_migration.scanlist.connect", _boom)
    target = sl.CPETarget(mac="aa:bb:cc:00:00:01", host="192.168.20.11", username="u", password="p")
    result = sl.push_scan_list(target, cpe_wireless_interface="wlan1", frequencies_mhz=[5745])
    assert result.success is False and "connect failed" in result.error


# --------------------------------------------------------------------------
# Step 4: migrate
# --------------------------------------------------------------------------

def test_change_sector_frequency_sets_by_interface_id():
    fake = FakeClient({"/interface/wireless/print": [{".id": "*1", "name": "wlan1"}]})
    mig.change_sector_frequency(fake, "wlan1", 5765)
    assert ("/interface/wireless/set", {"numbers": "*1", "frequency": "5765"}) in fake.commands


def test_wait_for_reconnection_stops_early_once_everyone_is_back():
    cpes = [
        disc.DiscoveredCPE(mac="aa", identity="cpe-a", signal_dbm=None, ccq_percent=None, management_ip=None),
        disc.DiscoveredCPE(mac="bb", identity="cpe-b", signal_dbm=None, ccq_percent=None, management_ip=None),
    ]
    polls = iter([set(), {"aa", "bb"}])  # nothing back yet, then everyone
    sleeps = []
    clock = iter([0.0, 10.0, 20.0, 20.0])

    report = mig.wait_for_reconnection(
        cpes,
        lambda: next(polls),
        total_timeout_seconds=180.0,
        poll_interval_seconds=10.0,
        sleep_fn=sleeps.append,
        now_fn=lambda: next(clock),
    )

    assert report.reconnected_count == 2
    assert report.missing == []
    assert sleeps == [10.0, 10.0]


def test_wait_for_reconnection_reports_who_never_came_back():
    cpes = [
        disc.DiscoveredCPE(mac="aa", identity="cpe-a", signal_dbm=None, ccq_percent=None, management_ip=None),
        disc.DiscoveredCPE(mac="bb", identity="cpe-b", signal_dbm=None, ccq_percent=None, management_ip=None),
    ]
    # start=0.0; one poll happens at 10.0 (aa comes back); the next check
    # at 200.0 is past total_timeout so the loop stops with bb still
    # missing; 200.0 again for the final elapsed-time calculation.
    clock = iter([0.0, 10.0, 200.0, 200.0])

    report = mig.wait_for_reconnection(
        cpes,
        lambda: {"aa"},  # bb never comes back
        total_timeout_seconds=180.0,
        poll_interval_seconds=10.0,
        sleep_fn=lambda s: None,
        now_fn=lambda: next(clock),
    )

    assert report.reconnected_count == 1
    assert report.missing == [("bb", "cpe-b")]


# --------------------------------------------------------------------------
# CLI orchestration + the three approval gates
# --------------------------------------------------------------------------

def _sector(**overrides):
    return fmc.SectorConfig(
        name="sector1-tower", host="192.168.20.1", username="u", password="p", **overrides
    )


def test_cli_aborts_before_scan_when_first_gate_declined(monkeypatch):
    client = FakeClient(
        {"/interface/wireless/registration-table/print": REG_TABLE, "/ip/neighbor/print": NEIGHBORS}
    )
    out = []
    code = _run_cli_with(client, confirms=[False], out=out.append)
    assert code == 3
    assert not client.commands  # never scanned, never touched anything
    assert any("Aborted before scanning" in line for line in out)


def test_cli_aborts_before_scanlist_push_when_second_gate_declined(monkeypatch):
    client = FakeClient(
        {
            "/interface/wireless/registration-table/print": REG_TABLE,
            "/ip/neighbor/print": NEIGHBORS,
            "/interface/wireless/spectral-scan": [{"freq": "5765", "range0": "-95"}],
            "/interface/wireless/print": [{".id": "*1", "name": "wlan1", "frequency": "5745"}],
        }
    )
    out = []
    code = _run_cli_with(client, confirms=[True, False], out=out.append)
    assert code == 3
    assert ("/interface/wireless/spectral-scan", {"interface": "wlan1", "duration": "20"}) in client.commands
    assert not any(cmd[0] == "/interface/wireless/set" for cmd in client.commands)


def test_cli_completes_full_migration_when_all_gates_approved(monkeypatch):
    client = FakeClient(
        {
            "/interface/wireless/registration-table/print": REG_TABLE,
            "/ip/neighbor/print": NEIGHBORS,
            "/interface/wireless/spectral-scan": [{"freq": "5765", "range0": "-95"}],
            "/interface/wireless/print": [{".id": "*1", "name": "wlan1", "frequency": "5745"}],
        }
    )
    monkeypatch.setattr(
        "routeros_exporter.freq_migration.scanlist.connect",
        lambda target, timeout: _ctx(client),
    )
    # wait_for_reconnection's own timing/polling behaviour is covered by
    # the two tests above it - here we only need the CLI to call it and
    # act on whatever MigrationReport comes back, so stub it directly
    # rather than burning real wall-clock time on the default 180s window.
    def fake_wait(before, poll_fn, **kw):
        return mig.MigrationReport(
            expected={c.mac: c.identity for c in before},
            missing_macs={"aa:bb:cc:00:00:02"},
            elapsed_seconds=42.0,
        )

    monkeypatch.setattr("routeros_exporter.freq_migration.cli.mig.wait_for_reconnection", fake_wait)

    out = []
    code = _run_cli_with(client, confirms=[True, True, True], out=out.append)

    assert code == 0
    assert ("/interface/wireless/set", {"numbers": "*1", "frequency": "5765"}) in client.commands
    assert any("1/2 CPE(s) reconnected" in line for line in out)
    assert any("aa:bb:cc:00:00:02" in line for line in out)  # the missing one is named


def _run_cli_with(client, *, confirms, out):
    from routeros_exporter.freq_migration import cli as cli_mod

    answers = iter(confirms)
    sector = _sector()

    monkeypatch_connect = _ctx(client)
    import contextlib

    @contextlib.contextmanager
    def fake_connect(router, timeout):
        yield client

    orig_connect = cli_mod.connect
    cli_mod.connect = fake_connect
    try:
        return cli_mod._run(sector, client, fmc.CPECredential(username="u", password="p"),
                             confirm=lambda prompt: next(answers), out=out)
    finally:
        cli_mod.connect = orig_connect
