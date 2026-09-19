"""Sector frequency migration tool: scan a sector's RF environment, pick a
cleaner frequency, prepare every connected CPE to find it, then move the
sector - with a human approval gate before each of the three live-hardware
steps (see cli.py). This is an operator-run CLI, not a background daemon:
frequency changes are rare, judgement-heavy, and briefly disrupt every
customer on the sector, so nothing here executes unattended.

The five-step process (agreed 2026-09-16, see docs/tier1-subscriber-config-topology.md
for the fuller design discussion this was scoped down from):

  1. discovery.py  - list every CPE currently registered on the sector
                      (MAC, name, signal, CCQ) and resolve each one's
                      management IP via the sector's own /ip/neighbor
                      (MNDP) table - the same discovery protocol Winbox
                      uses to connect by MAC, already parsed by
                      routeros_exporter.topology for the topology feature.
  2. spectrum.py   - run a spectral scan on the sector's radio and choose
                      old/new/fallback frequencies from the cleanest part
                      of the allowed band.
  3. scanlist.py   - push that small (old + new + fallbacks) scan-list to
                      every CPE found in step 1, while they're still on the
                      old frequency and reachable. A deliberately narrow
                      list, not a wide sweep - see scanlist.py's docstring.
  4. migrate.py    - change the sector's frequency over its own connection
                      (independent of the wireless link that's about to
                      drop), then wait through staged retry windows and
                      diff the sector's registration table before vs.
                      after to report which CPEs did not come back.

Gate placement (explicit, per the user 2026-09-16): approval is required
before step 2 (the scan), before step 3 (editing CPE config), and before
step 4 (changing the sector) - never automatic. See cli.py.
"""
