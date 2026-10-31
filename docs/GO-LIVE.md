# Go-live checklist (production)

The platform is feature-complete and tested. What remains is **site data and
credentials** that only the ISP can supply. Work top to bottom; tick each box.

## A. Server

- [ ] Dedicated Linux machine on the **management network only** (no leg in
      the customer network) - [SECURITY.md](SECURITY.md).
- [ ] Ubuntu 24.04 LTS, time sync on, SSH key login with passwordless
      `sudo`, listed in the site folder's `hosts.ini` - step by step in
      [ANSIBLE.md](ANSIBLE.md#first-production-install-step-by-step).
- [ ] Outbound HTTPS from the server to Ubuntu, Docker, PyPI and grafana.com
      for the install ([ANSIBLE.md](ANSIBLE.md#what-you-need)).
- [ ] A site folder for this ISP (copied from `inventories/example`) in a
      private git repo, committed and pushed; its `group_vars/all/vault.yml`
      encrypted; the vault password in the Keychain
      (`isp-observability-vault-<isp_name>`) **and** in the password manager.
- [ ] Firewall ranges set in the site's `vars.yml` (`firewall_noc_cidrs`,
      `firewall_engineer_cidrs`, `firewall_ssh_cidrs`). The playbook installs
      Docker, enables it at boot, and enforces Grafana (3000) only from NOC
      workstations, 9090/9093 only from engineers. (9100/9115/9116/9436 are
      bound to `127.0.0.1` in `docker-compose.yml`, so they are already
      closed to the network.)
- [ ] Manual install instead (no Ansible): Docker Engine + Compose plugin
      enabled at boot, repo cloned, `bash scripts/init.sh` run, firewall by
      hand - [INSTALL.md](INSTALL.md), [SETUP-INPUTS.md](SETUP-INPUTS.md).

## B. Describe the network (the site's `group_vars/all/vars.yml`)

The product repo only holds a fictional example site; every device below is
an entry in the site folder, and the deploy generates the target files from it
([ANSIBLE.md](ANSIBLE.md#site-variables-varsyml-plain)). The site checks
refuse duplicate IPs/names, unknown POPs and missing API logins.

- [ ] `pops` - every POP, and its PPPoE router once (`name`, `ip`,
      `wan_ifname` = the real WAN uplink, `auth`). `upstream_links` per POP.
- [ ] A **read-only** RouterOS API user on each POP router, its login under
      `routeros_api_logins` in the vault (keyed by the router `name`). Only
      POP routers are polled over the API, never client routers.
- [ ] `snmp_only_routers` - any other MikroTik to watch over SNMP.
- [ ] `sectors` - real sectors, each with its `pop`.
- [ ] `ptp_links` - real PTP links, both endpoints.
- [ ] `vantage_points` - the production server's default gateway, i.e. the
      POP router it plugs into, and optionally a public IP such as 1.1.1.1.
- [ ] `telegram_chat_id` and `timezone` (section C).
- [ ] SNMPv3 only (ISP policy): users under `snmpv3_users` in the vault,
      `auth: <name>` on each device, v2c removed from the device -
      [snmpv3.md](snmpv3.md). v2c communities (`snmp_communities`, vault) only
      where the firmware has no v3, restricted on the device to the server's IP.
      AirFiber has no v3: management decides ping-only, a documented
      exception, or replacement.
- [ ] Sector routes, POP by POP. The server plugs directly into its POP
      router (one hop); the POP routers already reach each other over the
      backhaul, so no inter-POP routing is needed. A NARROW route on each sector (monitoring subnet
      only, no default route) and, unless the sector range is already on a
      POP router interface, the matching route on the POP router; if the
      server has a second NIC or another default gateway, a static route on
      the server too. Then remove the POP masquerade rule. No routing protocols on
      sectors. Sector services (`/ip service`) limited to management ranges.
- [ ] Each POP router drops customer-to-management traffic, so sector/router
      management IPs stay unreachable from the customer side; test from a
      customer address (SECURITY.md checklist).

## C. Secrets and identities

With Ansible every `secrets/...` file and `.env` below is a key in the
site's `vault.yml` (`scripts/edit-secrets.sh --site <folder>`); the
playbook writes the files with the right modes and refuses placeholder values.

- [ ] `secrets/grafana_admin_password.txt` - strong password (not the
      `change-me-now` placeholder); create Viewer accounts for NOC staff.
- [ ] Telegram: production bot token (`telegram_bot_token`, vault), and the
      **production NOC group id** as `telegram_chat_id` in the site's
      `vars.yml` (the deploy writes it into `alertmanager/alertmanager.yml`
      for both `telegram` and `telegram-warnings`). The product file holds an
      example id.
- [ ] Anything ever pasted in chat/email is rotated (e.g. WhatsApp tokens).
- [ ] Linux host: `secrets/` is `700` and its files `644` (`bash scripts/init.sh`
      sets this; a `600` secret is unreadable inside the containers and
      Alertmanager starts without its Telegram token).

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

With Ansible, `scripts/deploy.sh --site <folder>` runs all of the commands
below plus the site checks (locally on the site's rendered files, and again on
the server against the real files) and stops on any failure; the
boxes after them are still yours. By hand:

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
- [ ] Image: publish a tag that includes the current rules (v1.4+) before using
      `docker-compose.image.yml` ([DOCKER.md](DOCKER.md)).
- [ ] A named owner for tuning and upgrades; NOC trained on the Operations Manual.
- [ ] Dead-man's switch on: healthchecks.io check, URL in
      `secrets/deadman_url.txt`, Watchdog route receiver `deadman`
      ([deadman.md](deadman.md)); `docker compose stop alertmanager` test passes.
- [ ] Rollback plan: `scripts/deploy.sh --site <folder> <previous deploy-<isp>-... tag>`;
      site data by checking out the site repo's `deploy-...` tag (or by hand:
      `git revert` the change, `docker compose up -d`, restart the changed
      service); WhatsApp can be switched off instantly with
      `whatsapp_mode: dry_run` in the site's `vars.yml` (or `WHATSAPP_MODE=dry_run` in
      `.env`) and a deploy.
- [ ] Backups (Ansible): the site repo (with its encrypted `vault.yml`) is
      pushed to a private remote; the vault password is kept somewhere other
      than the deploying laptop.

## Known limits to accept

- Prometheus cannot alert about its own death unless the dead-man's switch is
  switched on ([deadman.md](deadman.md), free healthchecks.io check).
- SNMPv2c is cleartext on the management network for devices still on v2c
  (move them to SNMPv3, [snmpv3.md](snmpv3.md)); the RouterOS API exporter's
  TLS mode encrypts but does not verify certificates.
- WhatsApp test numbers allow only a few verified recipients; production needs
  a registered number.
