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
- **MikroTik wireless sector radios** (`mikrotik_wireless_link` module):
  OIDs from an earlier session are still in snmp.yml, unverified against a
  live sector radio, and NOT referenced by any target file/job yet - see
  the comment on that module in snmp_exporter/snmp.yml. This is the actual
  gap against the "monitor MikroTik radios connected to sectors" goal from
  the project brief - the router-level `mikrotik` module only covers the
  router itself, not its sector AP radios or the CPEs attached to them.
