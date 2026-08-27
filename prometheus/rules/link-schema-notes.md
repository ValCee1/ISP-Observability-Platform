# Canonical link_* schema - Phase 1 (LiteBeam M5 lab pair)

## Active now
| Metric | Category | Source (LiteBeam M5 / airMAX) |
|---|---|---|
| link_up | Availability | blackbox ICMP (recording rule) |
| link_latency_seconds | Availability | blackbox ICMP (recording rule) |
| link_probe_failure_ratio | Availability | derived, windowed (recording rule) |
| link_latency_variability_seconds | Availability | derived, windowed (recording rule) |
| link_interface_state | Interface health | standard IF-MIB ifOperStatus |
| link_interface_in/out_errors | Interface health | standard IF-MIB ifInErrors/ifOutErrors |
| link_tx_bps / link_rx_bps | Interface health | derived from IF-MIB HC octet counters |
| link_signal_dbm | Radio health | ubntWlStatSignal (1.3.6.1.4.1.41112.1.4.5.1.5) |
| link_noise_dbm | Radio health | ubntWlStatNoiseFloor (.1.4.5.1.8) |
| link_quality_percent | Radio health | ubntWlStatCcq (.1.4.5.1.7) |
| link_channel_width_mhz | Radio health | ubntWlStatChanWidth (.1.4.5.1.14) |
| link_frequency_mhz | Radio health | ubntRadioFreq (.1.4.1.1.4) |

## Vendor extension (airMAX-only, not forced into the core schema)
| Metric | Source |
|---|---|
| ubiquiti_airmax_quality_percent | ubntAirMaxQuality (.1.4.6.1.3) |
| ubiquiti_airmax_capacity_percent | ubntAirMaxCapacity (.1.4.6.1.4) |

## Deployed (2026-08-23) - vendor raw units differ, normalized centrally
- **AirFiber & SAF capacity**: both vendors expose "link capacity" but in
  different raw units (AirFiber: bits/sec already; SAF: kb/s). Rather than
  force both onto one metric name at scrape time (which silently produced
  a ~6000% utilization bug when SAF's raw kb/s value was read as if it
  were bps), each vendor's SNMP module now emits its own raw-named metric
  (`airfiber_rx_capacity_bps`, `saf_modem_capacity_kbps`) and
  `prometheus/rules/link-normalization.yml` builds the single
  vendor-agnostic `link_capacity_bps` from both. Same pattern for SAF's
  MSE value (`saf_modem_mse_raw`, raw x10 per its MIB) -> `link_mse_db`.

## Confirmed working against live devices (2026-08-23)
- **SAF Integra** (`saf_link` module): signal, MSE, capacity, modem lock,
  and port state/octets all verified against a real unit (10.10.6.90/91) -
  the OID tree originally marked "genuinely blocked" above turned out to
  be correct once actually queried live. No longer deferred.
- **airMAX** (`ubiquiti_airmax_link` + `link_interface_health` modules):
  confirmed against a real LiteBeam pair. Two real limitations found: (1)
  ifName comes back as an empty string for every interface on this device
  - see the topk-based link_tx_bps/link_rx_bps workaround in
  link-normalization.yml; (2) this device doesn't expose the 64-bit HC
  octet counters at all, so link_tx_bps/link_rx_bps have genuinely no data
  for airMAX links, workaround or not - traffic volume just isn't
  observable on this hardware over SNMP.

## BROKEN - needs real investigation, not deployed as data
- **AirFiber** (`ubiquiti_airfiber_link` module, OID base
  1.3.6.1.4.1.41112.1.3.2.1): confirmed LIVE (2026-08-23) that this OID
  subtree returns **zero PDUs** from the real unit (10.10.6.82, a genuine
  "airFiber 5XHD") - despite this repo's earlier notes claiming these OIDs
  were "fully resolved from real MIB text". They were evidently resolved
  against MIB documentation for a different AirFiber model/firmware, not
  this one. Result: link_signal_dbm/airfiber_rx_capacity_bps/
  link_interface_state have never actually been collected for AirFiber
  links, despite the module being wired into prometheus.yml/links.yml as
  if they were.

  A live snmpwalk of `1.3.6.1.4.1.41112.1.10` on this unit DOES return
  real, changing data - RX power-like values (-52, -50 dBm), a pair of
  ~355-358 Mbps figures that read like current/max modulation rate, a
  Counter64 uptime, and the peer's IP (10.10.6.83) - but which numbered
  field means what is a guess without the real UBNT-AirFiber-5X MIB text
  (net-snmp's default MIB set doesn't include Ubiquiti's proprietary
  MIBs). Deliberately NOT wired into snmp.yml as a guess: an alert firing
  on a wrong RF number is worse than a link with admittedly-missing RF
  data. To finish this: get the actual AF-5XHD MIB from Ubiquiti (or a
  firmware dump / the UBNT Discovery Tool's OID list) and match field
  numbers against the sample walk above, then add the module the same way
  saf_link was added.
- ~~**MikroTik wireless sector radios**~~ DONE (2026-08-27). The stale,
  unverified `mikrotik_wireless_link` module was replaced by
  `mikrotik_sector`, decoded from a live `snmpwalk` of
  `1.3.6.1.4.1.14988.1.1.1` on an Nv2 / 802.11ac sector (10.10.13.1). It is
  wired into the `snmp-sectors` job (targets:
  `prometheus/targets/mikrotik-sectors.yml`). Sector metrics use their own
  `sector_*` namespace, NOT the `link_*` schema - a PtMP sector is a
  different shape from a PTP link (one AP, N CPEs) and forcing it into
  link_* would break the a/b-endpoint assumptions baked into
  link-normalization.yml. See `sector-normalization.yml`,
  `sector-thresholds.yml`, `sector-alerts.yml`.

## MikroTik sector schema (`sector_*`) - live-decoded 2026-08-27

Registration table `1.3.6.1.4.1.14988.1.1.1.2.1.<col>.<cpe_mac>.<wlanIfIndex>`:

| col | metric | notes |
|---|---|---|
| .3  | sector_cpe_signal_dbm | combined signal |
| .4/.5 | sector_cpe_tx/rx_bytes | Counter32 - sector RF throughput is summed from these |
| .6/.7 | sector_cpe_tx/rx_packets | |
| .8/.9 | sector_cpe_tx/rx_rate_bps | PHY/modulation rate, not traffic |
| .11 | sector_cpe_session_uptime_seconds | resets on re-registration -> `resets()` = flap count |
| .12 | sector_cpe_snr_db | primary quality metric (CCQ is dead under Nv2) |
| .13/.14 | sector_cpe_tx/rx_strength_ch0_dbm | per-chain, physical alignment |
| .15/.16 | sector_cpe_tx/rx_strength_ch1_dbm | |
| .19 | sector_cpe_tx_signal_dbm | reverse-path combined |
| .20 | (cpe_name label) | client name, e.g. "OFT_JULIO TANYI_RADIO" |

AP table `1.3.6.1.4.1.14988.1.1.1.3.1.<col>.<wlanIfIndex>`:

| col | metric / label | notes |
|---|---|---|
| .4 | (ssid label) | |
| .6 | sector_registered_clients | |
| .7 | sector_frequency_mhz | |
| .8 | (channel label) | e.g. "5525/20-eeeC/ac" |
| .9 | sector_noise_floor_dbm | best "interference appeared" signal |
| .11 | sector_auth_clients | |

- `sector_total_registered_clients` = scalar `...14988.1.1.1.4.0`.
- **CCQ (`...3.1.10`) reads 0 under Nv2 - deliberately not mapped.**
- **Airtime %** is not exposed by RouterOS on the old `wireless` package
  (Nv2) via SNMP or the API - `SectorLikelyCongested` infers it from
  simultaneous PHY-rate collapse + throughput over budget.
