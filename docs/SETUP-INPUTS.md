# First install: what you must provide, and where

A fresh `git clone` (or a pulled Docker image) does **not** contain anything
site-specific or secret. This is the complete list. Run `scripts/init.sh`
first: it creates every missing file below from the repo's examples
(never overwriting), then you edit the ones marked **EDIT**.

> A Docker registry pull alone is not enough. Only the Prometheus image is
> published; Grafana, Alertmanager, the exporters and the bridge are built
> from / mount files in this repo. Always install from the repo (or the
> offline bundle in [INSTALL.md](INSTALL.md)).

## A. Secrets (files in `secrets/`, gitignored)

| File | What goes in it | Where you get it |
|---|---|---|
| `grafana_admin_password.txt` | the Grafana `admin` password (one line) | you choose; **EDIT** (init.sh writes `change-me-now`) |
| `telegram_bot_token.txt` | Telegram bot token | @BotFather in Telegram → `/newbot` |
| `routeros_api_credentials.json` | `{"<router name>": {"username","password"}}` per router, names exactly as in `routeros_exporter/config.yml` | a **read-only** RouterOS user: `/user add name=prom-ro group=read password=...` |
| `whatsapp_access_token.txt` | Meta access token; leave the placeholder while in dry-run | Meta, see [whatsapp.md](whatsapp.md) |
| `deadman_url.txt` | heartbeat ping URL for the dead-man's switch; the placeholder is fine while it is off | healthchecks.io check, see [deadman.md](deadman.md) |
| `snmp_auths.yml` | SNMPv3 users (`auths:` entries); keep the template, or `auths: {}`, while every device is on v2c | the v3 user you create on each device, see [snmpv3.md](snmpv3.md) |

**Permissions:** files `644`, the `secrets/` folder `700` (init.sh sets both).
The containers read secrets as non-root users; on Linux a `600` file is
unreadable to them, and Alertmanager would start without its Telegram token.

**Changing the Grafana password later:** `grafana_admin_password.txt` is only
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

Only WhatsApp settings live here. **Everything is optional** - an empty or
missing `.env` leaves WhatsApp in dry-run. Fields: `WHATSAPP_MODE`,
`WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_RECIPIENTS`, `WHATSAPP_TEMPLATE`,
`WHATSAPP_DAILY_CAP`, `WHATSAPP_MONTHLY_CAP` (explained in the file).

## C. Describe your network (tracked files - **EDIT**, commit per site)

| File | Put here |
|---|---|
| `prometheus/targets/mikrotik-routers.yml` | each router's IP + `site`, `role`, `router` name, `wan_ifname` |
| `prometheus/targets/mikrotik-sectors.yml` | each PtMP sector AP |
| `prometheus/targets/links.yml` | each PTP link's endpoints + SNMP `auth` choice |
| `prometheus/targets/vantage-points.yml` | the server's gateway / a public IP: "can the server see out?" |
| `routeros_exporter/config.yml` (gitignored; from `config.example.yml`) | the same routers, API side: `name` (= credentials key and the `router` label), `host`, `site`, `pop`, `role` |
| `snmp_exporter/snmp.yml` | SNMP v1/v2c community strings (`auths:`) if yours differ from the repo defaults, and the same community configured on the devices. SNMPv3 users go in `secrets/snmp_auths.yml` instead |
| `prometheus/topology.yml` | POP / dependency layout used for alert suppression |

Router names must match in four places: `mikrotik-routers.yml` (`router:`),
`routeros_exporter/config.yml` (`name:`), `routeros_api_credentials.json`
(key), and Grafana's per-router variables.

## D. Alert delivery (tracked file - **EDIT**)

`alertmanager/alertmanager.yml` → the `telegram` and `telegram-warnings`
receivers: set `chat_id` to your NOC group's id (add the bot to the group;
group ids are negative, e.g. `-100...`). Both receivers need the same id.

## E. On the devices themselves

- RouterOS: enable the API service (TCP 8728), create the read-only user above,
  allow the monitoring server's IP.
- SNMP enabled with the community in `snmp.yml` (or a v3 user matching
  `secrets/snmp_auths.yml`, [snmpv3.md](snmpv3.md)), reachable from the server.
- Radios/CPEs: see `prometheus/rules/link-schema-notes.md`.

## F. Then

```bash
bash scripts/init.sh          # creates missing files, lists what to edit
# ...edit the files above...
docker compose config -q      # validates; fails loudly on a missing secret file
docker compose up -d --build
```
Check http://<server>:9090/targets - everything should be UP.
