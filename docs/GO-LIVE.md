# Go-live checklist (production)

The platform is feature-complete and tested. What remains is **site data and
credentials** that only the ISP can supply. Work top to bottom; tick each box.

## A. Server

- [ ] Dedicated Linux machine on the **management network only** (no leg in
      the customer network) - [SECURITY.md](SECURITY.md).
- [ ] Docker Engine + Compose plugin installed and enabled at boot.
- [ ] Repo cloned; `bash scripts/init.sh` run - [INSTALL.md](INSTALL.md),
      [SETUP-INPUTS.md](SETUP-INPUTS.md).
- [ ] Firewall: Grafana (3000) only from NOC workstations; 9090/9093 only from
      engineers; 9100/9115/9116/9436 closed to everyone but the server.

## B. Replace the lab data (tracked files still hold lab values)

- [ ] `prometheus/targets/mikrotik-routers.yml` - real routers.
- [ ] `prometheus/targets/mikrotik-sectors.yml` - real sectors.
- [ ] `prometheus/targets/links.yml` - real PTP links (lab links such as
      `litebeam-a` must go).
- [ ] `prometheus/targets/vantage-points.yml` - the production server's
      default gateway (the file still says `lab-gateway`) and optionally a
      public IP such as 1.1.1.1.
- [ ] `routeros_exporter/config.yml` + `secrets/routeros_api_credentials.json`
      - routers and their **read-only** API users (names identical everywhere).
- [ ] `prometheus/topology.yml` - POP/dependency layout.
- [ ] `snmp_exporter/snmp.yml` - SNMP communities (and the same on devices,
      restricted to the server's IP).
- [ ] Sector management routes in place (proposal deck: "one route on each side").

## C. Secrets and identities

- [ ] `secrets/grafana_admin_password.txt` - strong password (not the
      `change-me-now` placeholder); create Viewer accounts for NOC staff.
- [ ] Telegram: production bot token in `secrets/telegram_bot_token.txt`, and
      the **production NOC group id** as `chat_id` in
      `alertmanager/alertmanager.yml` (both `telegram` and
      `telegram-warnings`). The file currently points at the lab/dev group.
- [ ] Anything ever pasted in chat/email is rotated (e.g. WhatsApp tokens).

## D. WhatsApp (optional; keep `dry_run` until the end)

- [ ] Production phone number registered in Meta, business verification done,
      payment method attached (billing is per message).
- [ ] System User token (non-expiring) in `secrets/whatsapp_access_token.txt`;
      token's user has Full control on the WhatsApp account.
- [ ] Template `isp_critical_alert` approved and still **Utility** category.
- [ ] `.env`: `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_RECIPIENTS` (on-call
      numbers; start with one), caps sized to budget.
- [ ] Rate looked up on Meta's pricing page: worst case = monthly cap x rate.
- [ ] Verified in dry-run, then `WHATSAPP_MODE=live`; one real test received.
- [ ] Paging window agreed: `LinkDownProlonged` `for: 30m` in
      `prometheus/rules/link-alerts.yml` (longest normal weather fade).

## E. Validate before cut-over

```bash
docker run --rm -v "$PWD/prometheus:/p" --entrypoint sh prom/prometheus:v3.13.2 \
  -c 'cd /p && promtool check rules rules/*.yml && promtool test rules rules/tests/*.yml'
docker compose config -q
docker compose up -d --build && docker compose ps
```

- [ ] http://<server>:9090/targets: every job UP (a DOWN device = wrong IP,
      community or firewall).
- [ ] NOC Overview and Server Health dashboards show real data.
- [ ] Trigger one harmless test alert; confirm it reaches the NOC Telegram group.
- [ ] Unplug/silence drill from [failure-drills.md](failure-drills.md) passes.
- [ ] Settle-in period: run **one week** with `WHATSAPP_MODE=dry_run` and
      review Telegram volume; tune thresholds before enabling WhatsApp.

## F. Operate

- [ ] Backups: git remote for config; encrypted copy of `secrets/` and `.env`;
      optional volume archive ([Operations Manual](<ISP Network Observability Platform - Operations Manual.docx>) section 7).
- [ ] Image: publish a tag that includes the current rules (v1.3+) before using
      `docker-compose.image.yml` ([DOCKER.md](DOCKER.md)).
- [ ] A named owner for tuning and upgrades; NOC trained on the Operations Manual.
- [ ] Rollback plan: `git revert` the config change and `docker compose up -d`;
      WhatsApp can be switched off instantly with `WHATSAPP_MODE=dry_run`.

## Known limits to accept

- Prometheus cannot alert about its own death (no external dead-man's switch).
- SNMPv2c is cleartext on the management network; the RouterOS API exporter's
  TLS mode encrypts but does not verify certificates.
- WhatsApp test numbers allow only a few verified recipients; production needs
  a registered number.
