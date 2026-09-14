# Failure drills

Lab-scale proof that the pipeline works end to end: **detect → alert →
explain → recover → no storm**. Run these deliberately, tick the boxes,
keep the notes. The reproducible slice of this lives in
`prometheus/rules/tests/alerts_test.yml` (`promtool test rules`).

For each drill: what to break, what should light up in Prometheus, what
Telegram should say (one message, not ten), which dashboard panel tells
the story, and what "recovered" looks like.

---

## Drill 1 — PTP link down

**Break it:** power off one backhaul radio, or block its ICMP
(`/ip firewall filter add chain=input protocol=icmp action=drop` on the
far end), or just unplug the antenna feedline.

| Stage | Expect |
|---|---|
| Detect | `link_up` (blackbox ICMP) → 0 within ~15 s. `link_health_score` drops to 0. |
| Alert | After `for: 5m` → **`LinkDown`** (critical). Nothing else about that link — `WeakSignal` / `PacketLoss` / `RisingInterfaceErrors` / `LinkUpButDegrading` are **inhibited** (`inhibit_rules`, `equal: [link_id]`). |
| Telegram | **One** message: `🔴 Link LINK-002 — CRITICAL (1 alert)` + the description. Not a burst. |
| Dashboard | NOC Overview → **Active Incidents** shows `CRITICAL / LINK-002 / LinkDown` with a running duration. `All Links` row goes red. Click through → PTP Link Monitoring, latency/loss panels flatline. |
| Recover | Radio back → `link_up` → 1. `LinkDown` keeps firing for `keep_firing_for: 10m` (rides out a flapping recovery), then resolves. Telegram: `✅ Link LINK-002 — RESOLVED`, one message. |
| Storm check | Total Telegram messages for the whole event: **2** (firing + resolved). |

## Drill 2 — Sector AP down

**Break it:** power off a sector radio, or point its target at a dead IP
in `prometheus/targets/mikrotik-sectors.yml` and wait 1 m for the file_sd
reload. (Sector `4E` / `10.10.13.129` is currently unreachable — a live
example.)

| Stage | Expect |
|---|---|
| Detect | `sector_up` → 0. All `sector_cpe_*` series for that sector go stale. |
| Alert | After `for: 3m` → **`SectorDown`** (critical). Every `cpe-*` alert and `SectorOverClientCapacity` / `...ThroughputBudget` for that sector are inhibited (`equal: [sector]`). |
| Telegram | One message: `🔴 Sector 4E — CRITICAL`. |
| Dashboard | NOC → Active Incidents + `All Sectors` row red + `Sectors Down` tile = 1. Sector Detail for `4E` → Status DOWN, everything else NO DATA. |
| Recover | Radio back → `SectorDown` rides `keep_firing_for: 10m`, then resolves. |
| Storm check | 2 messages. |

## Drill 3 — POP backhaul down (dependency)

**Break it:** down the backhaul link feeding a POP (Drill 1 on `LINK-002`
**and** `LINK003`, the two `pop: BASE` links) so the sectors behind it
(`4B`, `4E`) are unreachable.

| Stage | Expect |
|---|---|
| Detect | `LinkDown` for the backhaul(s); `sector_up` for `4B`/`4E` → 0 shortly after. |
| Alert | `LinkDown` fires. `SectorDown` for `4B`/`4E` is **inhibited** by the POP rule (`source LinkDown{pop=~".+"}` → `target scope=~"sector\|router"`, `equal: [pop]`). |
| Telegram | Messages about the **backhaul**, not about every sector behind it. |
| Dashboard | Active Incidents shows the `LINK-*` rows; the sector rows are absent (suppressed) even though `Sectors Down` counts them — the count and the incident list deliberately differ (count = reality, incidents = what needs a human). |
| Recover | Backhaul back → sectors re-register → everything resolves together. |
| Storm check | ~2 messages per backhaul link, **0** extra for the sectors. |

## Drill 3b — POP isolated / subscriber mass outage (RouterOS API)

**Break it:** same as Drill 3 — down both `pop: BASE` backhauls — with
routeros-exporter polling a router behind BASE.

| Stage | Expect |
|---|---|
| Detect | `topology:pop:isolated{pop="BASE"}` → 1 (every backhaul `link_up` for BASE is 0). `subscriber:active:by_pop{pop="BASE"}` collapses. `routeros_scrape_success` for the BASE router → 0. |
| Alert | **`POPIsolated`** (critical, `scope: pop`) after `for: 3m`. `SubscriberMassOutagePOP` and per-router `SubscriberMassOutageRouter`, `RouterOSAPICollectorDown`, `LinkDown` ×2, `SectorDown` ×2 all fire in Prometheus but are **inhibited** by the `alertname: POPIsolated` → `scope=~"sector\|subscriber\|link\|router"`, `equal: [pop]` rule. |
| Telegram | **One** message: `🔴 POP BASE — CRITICAL` → "Every backhaul into POP BASE is unreachable. ~N subscribers…". |
| Dashboard | Subscriber Overview → *Active sessions by POP* shows the cliff; *POPs isolated* stat = 1. NOC Active Incidents → one `BASE` row. |
| Recover | Backhaul back → `topology:pop:isolated` clears → sessions re-establish → everything resolves together. |
| Storm check | 1 message, not ~10. |

## Drill 3c — Unplanned config change

**Break it:** on a polled router, `/ip firewall filter add chain=forward
action=drop` (or any real change). Wait one `backup_interval_seconds`.

| Stage | Expect |
|---|---|
| Detect | routeros-exporter commits the new `/export`; `routeros_config_changed{router}` → 1 for that cycle; `routeros_config_last_change_timestamp` updates. |
| Alert | **`RouterConfigChanged`** (warning, `scope: router`), `keep_firing_for: 20m`. |
| Telegram | One message: `🟠 Router <name> — degraded` → "config changed (now N lines)… review the diff". |
| Investigate | `git -C <backup-repo> log -p -- <router>.rsc` shows exactly what changed and when. |
| Recover | Revert the change (or accept it); next cycle `routeros_config_changed` → 0, alert resolves after `keep_firing_for`. |

## Drill 4 — Widespread CPE degradation (derived intelligence)

**Break it:** hard to fake cleanly — wait for weather, or nudge the sector
onto a noisier channel for 20 min, or drop TX power so several CPEs lose
margin at once.

| Stage | Expect |
|---|---|
| Detect | `sector:cpe:alignment_score` drops below 60 for ≥4 CPEs → `sector:degraded_cpe_count` ≥ 4. |
| Alert | After `for: 15m` → **`SectorWideCPEDegradation`** (one, per sector). The individual `CPEWeakSignal` / `CPESignalDegradedVsBaseline` / `CPEChainImbalance` / `CPELowSNR` for that sector are **inhibited**. `CPEFlapping` / `CPERateCollapse` are **not** — different failure mode. |
| Telegram | One message: `🟠 Sector 4B — degraded (1 alert)` → "N CPEs below alignment 60…". |
| Dashboard | Sector Detail → `Clients (worst alignment first)` table: the degraded CPEs cluster at the top, red Align cells. `Sector signal (average/worst)` panel: the **average** line drops (whole-sector cause), not just one CPE. NOC `Sectors Degrading` tile = 1. |
| Recover | Channel/weather clears → scores recover → resolves. |
| Storm check | 1 message, not one-per-CPE. |

## Drill 5 — Backhaul saturation

**Break it:** run an iperf flood across the sector's ether1, or drop its
policer, until `sector:backhaul_utilization_percent` > 80.

| Stage | Expect |
|---|---|
| Detect | `sector:backhaul_utilization_percent` climbs past 80. |
| Alert | After `for: 15m` → **`SectorBackhaulSaturated`** (warning). |
| Telegram | One message naming the sector and the %. |
| Dashboard | Sector Detail → `Backhaul (ether1) traffic` panel near the port ceiling; NOC `All Sectors` → Backhaul % cell red. Capacity Planning → the sector's row. |
| Recover | Load drops → resolves after it clears + `keep_firing_for`. |

## Drill 6 — Transient blip (must NOT alert)

**Break it:** bounce a radio for 60–90 s (reboot), or a single dropped
ICMP.

| Stage | Expect |
|---|---|
| Detect | `link_up` / `sector_up` dips for < the `for:` window. |
| Alert | **Nothing.** `for: 3–5m` swallows it. If it had already been firing, `keep_firing_for` means no resolve+refire pair. |
| Telegram | Silent. |

---

## What "no storm" means here

The machinery that keeps one fault = one message:

- **`for:`** (2–5 m) — transients never reach Telegram.
- **`keep_firing_for:`** (5–10 m on state + flappy health alerts) — a
  fault that recovers and re-breaks is one continuous alert, not a
  resolve/refire pair each cycle.
- **`group_by: [link_id | sector | instance]`** + `group_interval: 10m` —
  every alert about one object lands in one message.
- **`inhibit_rules`** — a parent incident (LinkDown, SectorDown,
  SectorEther1Down, SectorWideCPEDegradation, POP backhaul/router down,
  **POPIsolated**, **SubscriberMassOutagePOP**) silences its symptoms.
- **Two-tier collapse** — `PacketLoss` / `HighLatency` warn+critical pairs:
  the critical inhibits the warning.
- **Capacity digest** — `SectorOverClientCapacity` /
  `...ThroughputBudget` never page; they batch into the 08:00 Africa/Lagos
  message.
- **`repeat_interval`** 2 h critical / 12 h warning — an unfixed fault
  reminds you, it doesn't nag.
