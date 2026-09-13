# Tier 2, part 1 — statistical anomaly detection

No LLM, no new service: `prometheus/rules/anomaly-detection.yml` extends the
"compare a metric to its own recent baseline" idiom already used for CPE
signal drift (`sector-normalization.yml`'s `sector:cpe:signal_drift_db` /
`baseline_ready`) to metrics the existing threshold alerts structurally
can't catch a slow version of.

## Why this is separate from the existing threshold alerts

`SubscriberMassOutagePOP`, `HighCPU`, `HighMemory`, etc. catch **fast,
sharp** problems by design — mass-outage needs a ≥30% session drop inside 5
minutes. None of them catch a **slow bleed**: a POP losing subscribers 2%
an hour, or CPU creeping from a normal 20% to a sustained 65% without ever
crossing the hard 80% ceiling. This file adds that: "is this far from its
own normal", independent of any fixed threshold.

## Window choice — v1, with a documented upgrade path

The CPE baseline compares to a **7-day** average because CPE signal is
stable week to week. Subscriber counts and router load aren't that settled
yet on this deployment — CONFIRMED 2026-09-13, `count_over_time(up[30d])`
on pop1-home returned ~2400 samples (≈10h), not 30 days of history (the
data volume was recreated repeatedly during development). So v1 compares a
**1h** average against a **6h** average instead of hour-of-day-of-week.

This has a real consequence, found while writing the promtool tests: a
1h-vs-6h *ratio* is inherently **transient** for a sustained step change —
the 6h average catches up to the new level over time, so the ratio only
stays past its threshold for roughly 20–30 minutes even when the underlying
problem persists indefinitely. That's why `RouterCPUAnomalyHigh` /
`RouterMemoryAnomalyHigh` use `for: 15m`, not the `for: 30m` first tried
(see `prometheus/rules/tests/anomaly_test.yml`'s comments for the exact
simulated crossing points that drove that number).

**Upgrade path**: once weeks of continuous history exist, move to
hour-of-day-of-week baselines — the same 24-explicit-recording-rules
pattern `sector-normalization.yml`'s `sector:*:hod` series already use (one
rule per hour because PromQL can't derive an hour label from a timestamp
inline). That removes the transient-ratio problem entirely, since it
compares like-for-like time-of-day rather than a fast clock against a slow
one.

## What it covers

| Alert | Catches | Gate |
|---|---|---|
| `SubscriberCountAnomalyLowPOP` / `...Router` | 1h avg active sessions ≤60% of the 6h avg, sustained 30m | `baseline_ready` (~5h+ of history) |
| `RouterCPUAnomalyHigh` | 1h avg CPU ≥1.5× the 6h avg, still under HighCPU's 80% | ditto, `for: 15m` |
| `RouterMemoryAnomalyHigh` | 1h avg memory ≥1.3× the 6h avg, still under HighMemory's 75% | ditto, `for: 15m` |

All four route through the existing `scope: pop|subscriber|router`
Alertmanager routes (subscriber-alerts.yml / config-backup.yml already
wired these) — no new routing needed. `category: degrading` matches the
convention used for other slow-symptom alerts (`SectorWideCPEDegradation`,
`LinkUpButDegrading`).

## Out of scope for this pass

Incident summarization and natural-language-to-PromQL (the rest of the
original Tier 2 sketch) need an LLM API key — a new secret and an ongoing
per-call cost — and are deferred pending that decision.
