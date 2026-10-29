# First install: what you must provide, and where

A fresh `git clone` (or a pulled Docker image) does **not** contain anything
site-specific or secret. This is the complete list.

- **Deploying with Ansible** ([ANSIBLE.md](ANSIBLE.md)): everything here is
  a key in the ISP's **site folder**, outside the product repo (start from
  `inventories/example`): secrets in the encrypted `group_vars/all/vault.yml`,
  the rest in `group_vars/all/vars.yml`. The "Ansible key" column below names
  the key; the playbook generates every file from them on the server, with
  the right modes. Section E (the devices) still applies as written.
- **By hand:** run `scripts/init.sh` first: it creates every missing file
  below from the repo's examples (never overwriting), then you edit the ones
  marked **EDIT**.

> A Docker registry pull alone is not enough. Only the Prometheus image is
> published; Grafana, Alertmanager, the exporters and the bridge are built
> from / mount files in this repo. Always install from the repo (or the
> offline bundle in [INSTALL.md](INSTALL.md)).

## A. Secrets (files in `secrets/`, gitignored)

| File | Ansible key (vault) | What goes in it | Where you get it |
|---|---|---|---|
| `grafana_admin_password.txt` | `grafana_admin_password` | the Grafana `admin` password (one line) | you choose; **EDIT** (init.sh writes `change-me-now`) |
| `telegram_bot_token.txt` | `telegram_bot_token` | Telegram bot token | @BotFather in Telegram → `/newbot` |
| `routeros_api_credentials.json` | `routeros_api_logins` | `{"<router name>": {"username","password"}}` per **POP router**, names exactly as in `routeros_exporter/config.yml` (Ansible: as in `pops.*.router.name`, checked) | a **read-only** RouterOS user: `/user add name=prom-ro group=read password=...` |
| `whatsapp_access_token.txt` | `whatsapp_access_token` | Meta access token; leave the placeholder while in dry-run | Meta, see [whatsapp.md](whatsapp.md) |
| `deadman_url.txt` | `deadman_url` | heartbeat ping URL for the dead-man's switch; **required** (the switch is on, the placeholder is refused) | healthchecks.io check, see [deadman.md](deadman.md) |
| `snmp_auths.yml` | `snmp_communities` + `snmpv3_users` | every SNMP credential set: v1/v2c communities and SNMPv3 users (`auths:` entries) | the community / v3 user you set on each device, see [snmpv3.md](snmpv3.md) |

**Permissions:** files `644`, the `secrets/` folder `700` (init.sh sets both).
The containers read secrets as non-root users; on Linux a `600` file is
unreadable to them, and Alertmanager would start without its Telegram token.

**Changing the Grafana password later:** with Ansible, change
`grafana_admin_password` in the vault and deploy; the playbook also resets
the live admin password. By hand: `grafana_admin_password.txt` is only
read when Grafana first creates its database (first start, empty `grafana_data`
volume). Editing the file afterwards does **not** change the existing admin
password, so the old one keeps working. To apply a new password to a running
install, either change it in Grafana (profile > Change password) or run:

```bash
docker compose exec grafana grafana cli admin reset-admin-password '<new-password>'
```
and keep the file in step with it. On a brand-new install, set the file
before the first `docker compose up`.

No trailing newline issues: the stack trims whitespace, but avoid BOMs on Windows.

## B. `.env` (repo root, gitignored; template `.env.example`)

Only WhatsApp settings live here (Ansible: `whatsapp_*` in `vars.yml`, and
`whatsapp_recipients` - phone numbers - in the vault). **Everything is
optional** - an empty or missing `.env` leaves WhatsApp in dry-run. Fields: `WHATSAPP_MODE`,
`WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_RECIPIENTS`, `WHATSAPP_TEMPLATE`,
`WHATSAPP_DAILY_CAP`, `WHATSAPP_MONTHLY_CAP` (explained in the file).

## C. Describe your network

**With Ansible** it is all in the site's `group_vars/all/vars.yml`, and each
POP router is written **once**; the deploy generates the files in the table
below from it and refuses duplicates, unknown POPs and POP routers without an
API login ([ANSIBLE.md](ANSIBLE.md#site-variables-varsyml-plain)):

| Ansible key | Put here | Becomes |
|---|---|---|
| `pops` | each POP; its PPPoE router once: `name`, `ip`, `wan_ifname`, `auth` | `mikrotik-routers.yml`, `routeros_exporter/config.yml`, `topology.yml` |
| `snmp_only_routers` | other MikroTiks to watch over SNMP only (never the API) | `mikrotik-routers.yml` |
| `sectors` | each PtMP sector AP + its `pop` | `mikrotik-sectors.yml` |
| `ptp_links` | each PTP link's endpoints + SNMP `auth` choice | `links.yml` |
| `vantage_points` | the server's gateway / a public IP: "can the server see out?" | `vantage-points.yml` |

**By hand** (no Ansible), edit the generated files directly - **EDIT**, keep
them out of the product repo:

| File | Put here |
|---|---|
| `prometheus/targets/mikrotik-routers.yml` | each router's IP + `site`, `role`, `router` name, `wan_ifname`, `auth` |
| `prometheus/targets/mikrotik-sectors.yml` | each PtMP sector AP |
| `prometheus/targets/links.yml` | each PTP link's endpoints + SNMP `auth` choice |
| `prometheus/targets/vantage-points.yml` | the server's gateway / a public IP |
| `routeros_exporter/config.yml` (gitignored; from `config.example.yml`) | the POP routers, API side: `name` (= credentials key and the `router` label), `host`, `site`, `pop`, `role` |
| `prometheus/topology.yml` | POP / dependency layout (documentation) |

By hand, a router name must match in three places: `mikrotik-routers.yml`
(`router:`), `routeros_exporter/config.yml` (`name:`) and
`routeros_api_credentials.json` (key). With Ansible it is written once.

## D. Alert delivery

**Changing the Telegram group (any time):**

1. Add the bot to the new group (and make it able to post there).
2. Find the group's id: send any message in the group, then open
   `https://api.telegram.org/bot<bot-token>/getUpdates` in a browser and read
   `"chat":{"id":...}`. Group ids are negative, e.g. `-100...`.
3. **Ansible:** set `telegram_chat_id: "-100..."` in the site's `vars.yml`,
   commit, push, deploy - Alertmanager reloads. The timezone of the daily
   capacity digest is `timezone` (e.g. `Africa/Lagos`) in the same file.
   **By hand:** in `alertmanager/alertmanager.yml`, search for `noc_chat_id`
   and replace the number on that one line (`chat_id: &noc_chat_id -100...`);
   the warnings receiver reuses it automatically. Then:
   ```bash
   docker run --rm -v "$PWD/alertmanager:/a" --entrypoint amtool prom/alertmanager:v0.33.1 check-config /a/alertmanager.yml
   docker compose restart alertmanager
   ```

**Changing the bot** (new token): Ansible: `telegram_bot_token` in the vault
and deploy. By hand: put it in `secrets/telegram_bot_token.txt` (one line),
then `docker compose restart alertmanager`.

## E. On the devices themselves

- RouterOS: enable the API service (TCP 8728), create the read-only user above,
  allow the monitoring server's IP.
- SNMP enabled with a v3 user (or, where the firmware has no v3, a community)
  matching `snmpv3_users` / `snmp_communities` - by hand,
  `secrets/snmp_auths.yml` ([snmpv3.md](snmpv3.md)) - reachable from the server.
- Radios/CPEs: see `prometheus/rules/link-schema-notes.md`.

## F. Then

```bash
# Ansible: scripts/deploy.sh --site <folder> does all of this. By hand:
bash scripts/init.sh          # creates missing files, lists what to edit
# ...edit the files above...
docker compose config -q      # validates; fails loudly on a missing secret file
docker compose up -d --build
```
Check http://<server>:9090/targets - everything should be UP.
