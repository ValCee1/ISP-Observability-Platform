# Deploying with Ansible (recommended for production)

One command from your laptop prepares the server, checks everything, and
releases the stack. It replaces the manual steps in [INSTALL.md](INSTALL.md),
which still work if you ever need to install by hand.

The product (this repo) is the same for every ISP. Everything that belongs to
one ISP - its server, devices, Telegram group, timezone, firewall ranges and
secrets - lives in a **site folder** outside this repo, normally a private git
repo of its own. A deploy combines the two:

```
your laptop                                        monitoring server (Ubuntu 24.04)
-----------                                        --------------------------------
scripts/deploy.sh --site ~/isp-sites/acme
  1. safety: product on origin/main; site clean + pushed
  2. site checks, render the site's targets, local
     checks (promtool, amtool, ...) on the result
  3. git archive <commit>  ---------- ssh ------>  4. Docker, firewall, packages
                                                    5. unpack to staging, render the
     site folder: vars.yml + vault.yml ---------->     site's files, write secrets
     vault password: macOS Keychain                6. checks against the real files
                                                    7. sync into /opt/isp-observability
                                                    8. compose up; reload/restart
                                                       only what changed
                                                    9. health checks, record version
 10. tag deploy-acme-<time> here, deploy-<time> in the site repo
```

Docker Compose still describes the stack (`docker-compose.yml` is untouched).
Ansible only gets the server and the per-site files right first.

## The site folder

```
acme/                         # e.g. ~/isp-sites/acme, a private git repo
  hosts.ini                   # the monitoring server
  group_vars/all/vars.yml     # plain: network, Telegram group, timezone, firewall, WhatsApp
  group_vars/all/vault.yml    # ansible-vault encrypted: passwords, tokens, logins, SNMP credentials
  vault.example.yml           # the template vault.yml was created from (optional)
```

Start one by copying [`inventories/example`](../inventories/example) (a
fictional ISP) out of this repo. Its `vars.yml` documents every key in
comments; the shapes are below. The product's defaults
(`ansible/roles/isp_defaults/defaults/main.yml`) are Ansible's lowest
precedence, so the site overrides any of them.

### Site variables (`vars.yml`, plain)

```yaml
isp_name: acme                     # short id; names the deploy tags
timezone: Africa/Lagos             # Alertmanager's 08:00 capacity-digest window
telegram_chat_id: "-1001234567890" # the NOC Telegram group

firewall_ssh_cidrs:      [10.50.0.0/24]   # must include the address you deploy from
firewall_engineer_cidrs: [10.50.0.0/24]   # Grafana, Prometheus, Alertmanager
firewall_noc_cidrs:      [10.50.1.0/24]   # Grafana only

whatsapp_mode: dry_run             # dry_run | live
whatsapp_phone_number_id: ""
whatsapp_template: isp_critical_alert
whatsapp_daily_cap: 10
whatsapp_monthly_cap: 100

pops:                              # one entry per POP
  IKEJA:
    site: Ikeja
    router: {name: pop-ikeja, ip: 10.50.0.1, wan_ifname: ether1, auth: mikrotik_v3}
    upstream_links: []
  YABA:
    site: Yaba
    router: {name: pop-yaba, ip: 10.50.2.1, wan_ifname: sfp1, auth: mikrotik_v3,
             api_port: 8729, api_tls: true}      # optional; default 8728, no TLS
    upstream_links: [IKEJA-YABA]   # ptp_links ids feeding this POP
  LEKKI:
    site: Lekki
    router: null                   # a POP with no polled router yet
    upstream_links: [YABA-LEKKI]

snmp_only_routers:                 # SNMP targets never polled over the API
  - {name: yaba-repeater, ip: 10.50.2.15, site: Yaba, role: Repeater,
     wan_ifname: wlan1, auth: mikrotik_v3, pop: YABA}     # pop optional

sectors:                           # site defaults to the POP's, role to sector-ap
  - {sector: YB1, ip: 10.50.13.1, pop: YABA, role: Yaba sector 1, auth: mikrotik_v3}

ptp_links:
  - id: IKEJA-YABA
    vendor: ubiquiti_airfiber      # ubiquiti_airmax | ubiquiti_airfiber | saf
    role: Backhaul
    auth: public_v2                # an endpoint may override it
    endpoints:                     # site defaults to endpoint, link_name to id
      - {ip: 10.50.6.82, endpoint: IKEJA, site: IKEJA, pop: YABA}
      - {ip: 10.50.6.83, endpoint: YABA, site: YABA, pop: YABA}

vantage_points:                    # what the server pings to know it is itself online
  - {vantage: server-gateway, ip: 10.50.0.1}
  - {vantage: public-internet, ip: 1.1.1.1}
```

**Each POP router is listed once.** From `pops.<POP>.router` the deploy
generates its SNMP target (`prometheus/targets/mikrotik-routers.yml`, with the
`router`, `pop`, `wan_ifname` and `auth` labels), its entry in
`routeros_exporter/config.yml`, and the key its API login must have in
`routeros_api_logins`. A name no longer has to match by hand in four places.
Only POP routers (the PPPoE servers) are polled over the RouterOS API; client
routers never are. Everything else that should be an SNMP target goes in
`snmp_only_routers`.

### Site secrets (`vault.yml`, encrypted)

```yaml
grafana_admin_password: ...
telegram_bot_token: ...
deadman_url: https://hc-ping.com/<uuid>      # required: the dead-man's switch is on
whatsapp_access_token: dry-run-placeholder    # placeholder fine while dry_run
whatsapp_recipients: "2348012345678"          # phone numbers: personal data

routeros_api_logins:               # exactly one per POP router name
  pop-ikeja: {username: prom-ro, password: ...}
  pop-yaba:  {username: prom-ro, password: ...}

snmp_communities:                  # v1/v2c, only where the firmware has no v3
  public_v2: {version: 2, community: ...}

snmpv3_users:                      # security_level defaults to authPriv
  mikrotik_v3: {username: monitoring, auth_protocol: SHA, password: ...,
                priv_protocol: AES, priv_password: ...}
```

`snmp_communities` and `snmpv3_users` together become `secrets/snmp_auths.yml`:
every SNMP credential set a target's `auth` may name. `snmp_exporter/snmp.yml`
holds no credentials.

### What the deploy generates from them

| On the server | From |
|---|---|
| `prometheus/targets/mikrotik-routers.yml` | `pops.*.router` + `snmp_only_routers` |
| `prometheus/targets/mikrotik-sectors.yml`, `links.yml`, `vantage-points.yml` | `sectors`, `ptp_links`, `vantage_points` |
| `prometheus/topology.yml` (documentation) | `pops`, `sectors` |
| `routeros_exporter/config.yml` | `pops.*.router` |
| `alertmanager/alertmanager.yml` | the product file, with `telegram_chat_id` and `timezone` swapped in (routing stays in the product) |
| `secrets/*` (folder `700`, files `644`), `.env` (`600`) | `vault.yml` and the WhatsApp settings |

The templates are in `ansible/templates/site/`. In this repo the same files
are rendered from `inventories/example` (`scripts/render-example.sh`;
`--check` reports whether they are current), so the local checks, the promtool
tests and the Docker image always have valid, fictional data. Never commit an
ISP's own targets here.

### Site checks

Run on the laptop before anything is packaged, and again on the server, each
failing with a message naming the key (never a secret value):

- duplicate IPs across routers, sectors and link endpoints; duplicate router
  names, sector ids, link ids, vantage names;
- a link endpoint, sector or SNMP-only router naming an unknown POP; an
  `upstream_links` entry naming an unknown link;
- a POP router without an entry in `routeros_api_logins`, or a login for a
  name that is not a POP router;
- an `auth` not defined in `snmp_communities`/`snmpv3_users`, and SNMPv3
  spellings (`SHA`, not `SHA1`; `AES256`, not `AES-256`);
- missing fields, a malformed IPv4 address, `isp_name`/`timezone`/
  `telegram_chat_id` unset;
- placeholder secrets (`CHANGE-ME`, `PUT-...-HERE`, `change-me-now`, and
  `dry-run-placeholder` once WhatsApp is live).

## What you need

- **Server:** Ubuntu **24.04 LTS** (supported to 2029, 2034 with free Ubuntu
  Pro). 22.04 also works (tested; the playbook warns) but leaves standard
  support in April 2027. 4 GB RAM, 2 CPUs, disk per the Operations Manual
  Appendix B (40 GB is plenty for a pilot). A fixed IP on the management
  network.
- **Outbound HTTPS from the server** during installs and upgrades: Ubuntu's
  mirrors, `download.docker.com`, Docker Hub (`registry-1.docker.io`,
  `auth.docker.io`, `production.cloudflare.docker.com`), PyPI (`pypi.org`,
  `files.pythonhosted.org`, for the RouterOS exporter build) and
  `grafana.com` (Grafana's pinned Prometheus plugin). Nothing inbound from
  the internet.
- **Laptop (macOS):** this repo, Homebrew, Ansible (`brew install ansible`),
  Docker Desktop for the local checks (optional; the server repeats them),
  and SSH access to the server.

The server needs no GitHub access and no deploy key: the code is pushed
from your laptop.

## First production install, step by step

The same steps, with the device preparation and the acceptance tests, are in
one printable document: [Installation Manual](<ISP Network Observability Platform - Installation Manual.docx>).

Done once per ISP. Expect about an hour, most of it filling in site data.
The deploy itself takes 10-40 minutes depending on the internet link (it
downloads about 1.5 GB).

### 1. The server (at its console, or your VM host's console)

1. Install Ubuntu Server 24.04 LTS. In the installer: a fixed IP on the
   management network, **Install OpenSSH server** ticked, and your admin
   user (below: `noc`). With the installer's default LVM layout, check that
   `/` got the whole disk (`df -h /`); if not:
   `sudo lvextend -r -l +100%FREE /dev/ubuntu-vg/ubuntu-lv`.
2. Turn on time sync (alerts and graphs depend on the clock):
   `sudo timedatectl set-ntp true`; `timedatectl` must then show
   `System clock synchronized: yes`.
3. Let the admin user use sudo without a password (Ansible needs sudo on
   every run; the alternative is typing it each time, see section 2):

   ```bash
   echo "noc ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/noc
   sudo chmod 440 /etc/sudoers.d/noc
   ```

Nothing else is installed by hand: Ansible installs Docker, rsync and the
firewall.

### 2. Your laptop (once per laptop)

```bash
brew install ansible
cd <this repo> && git checkout main && git pull
ansible-galaxy collection install -r ansible/requirements.yml

# SSH key login to the server (creates a key first if you have none)
[ -f ~/.ssh/id_ed25519 ] || ssh-keygen -t ed25519
ssh-copy-id noc@<server-ip>
ssh noc@<server-ip> sudo -n true && echo OK     # must print OK, no prompts
```

Kept a sudo password instead of section 1 step 3? Add
`-- --ask-become-pass` to every `scripts/deploy.sh` command; it prompts once
per run.

### 3. The site folder (once per ISP)

```bash
# a private repo for this ISP, outside the product repo
cp -R inventories/example ~/isp-sites/acme
cd ~/isp-sites/acme && git init && git remote add origin <private-repo-url>
$EDITOR hosts.ini                  # <name> ansible_host=<server-ip> ansible_user=noc
$EDITOR group_vars/all/vars.yml    # isp_name: acme, then the network (above)

# this ISP's vault password, in the macOS Keychain (never in a file)
security add-generic-password -a "$USER" -s isp-observability-vault-acme -w
#    (it prompts; pick a long one, keep a copy in your password manager -
#     without it the vault cannot be opened. -acme = isp_name.)

cd <this repo>
scripts/edit-secrets.sh --site ~/isp-sites/acme
```

`edit-secrets.sh` creates `group_vars/all/vault.yml` from the site's
`vault.example.yml` and encrypts it **before** you type anything, then opens
it in `$EDITOR`. [SETUP-INPUTS.md](SETUP-INPUTS.md) says where each value comes
from. Then commit and push the site repo (the deploy refuses an unpushed or
dirty site):

```bash
cd ~/isp-sites/acme
head -1 group_vars/all/vault.yml   # must print $ANSIBLE_VAULT;1.1;AES256
git add -A && git commit -m "Site setup: acme" && git push -u origin main
```

The vault password is looked up as `isp-observability-vault-<isp_name>` and
falls back to `isp-observability-vault`, so a single-ISP laptop can keep the
one shared item.

### 4. Deploy

```bash
scripts/deploy.sh --site ~/isp-sites/acme
```

First the local checks print `ok   ...` lines (`ok   site inventory (acme)`
means the site checks passed), then Ansible's tasks scroll by. On a fresh
server the slow tasks are *Docker packages*, *Pull the published images* and
*Build the bridge and exporter images*; a `FAILED - RETRYING` line there is a
flaky download being retried, not an error. Grafana's very first start takes
up to 5 minutes (it sets up its database and downloads its plugin). It ends
with a summary:

```
Deployed main (1a2b3c4d5e6f) to acme, site data 9f8e7d6c5b4a
Changed files: ...
Down targets: 0
```

and two local tags: `deploy-acme-YYYY-MM-DD-HHMM` here and
`deploy-YYYY-MM-DD-HHMM` in the site repo. Push both so anyone can roll back.

Set `ISP_OBS_SITE=~/isp-sites/acme` in your shell to leave out `--site`.

(`--check` has nothing to compare against on a fresh server: it stops after
the checks with "Preview needs a server that has been deployed once".)

### 5. Check it

- `http://<server-ip>:3000`: log in as `admin` with the vault password; the
  NOC Overview and Server Health dashboards show data.
- `http://<server-ip>:9090/targets`: every target UP. The deploy summary
  also lists the down ones: wrong IP, SNMP user, or a device firewall.
- Reboot once (`ssh noc@<server-ip> sudo reboot`): a few minutes later
  everything is back on its own, firewall included.
- Continue with [GO-LIVE.md](GO-LIVE.md) sections E and F (test alert,
  drills, dead-man's switch, backups).

### If a deploy stops

Repeating a deploy is always safe: it only acts on what differs, and nothing
live is touched until every check has passed. Fix the cause and run
`scripts/deploy.sh --site ...` again. Add `-- -vv` for more detail.

| Message | Cause and fix |
|---|---|
| `no site folder` | pass `--site <folder>` (or set `ISP_OBS_SITE`) |
| `ansible/secrets.yml is the old single-site layout` | move it into a site folder: [below](#moving-from-ansiblesecretsyml) |
| `uncommitted changes to tracked files` | commit or stash them (product repo) |
| `site ... has uncommitted or untracked files` | commit them in the site repo - Ansible would deploy them as they are |
| `... is not on origin/main` / `site commit ... is not pushed` | merge the product PR / push the site repo (`--force-branch` is for a lab server only) |
| `vault.yml is NOT encrypted` | `ansible-vault encrypt <site>/group_vars/all/vault.yml` before anything else |
| `vault password not found` | the Keychain item for this `isp_name` is missing (section 3) |
| `site inventory check failed` / `Fix in the site folder: ...` | each line names the key: duplicate IP, unknown POP, missing API login, unknown auth, `SHA1` spelling... |
| `UNREACHABLE`, `Permission denied (publickey)` | SSH: `ssh-copy-id` again; check the site's `hosts.ini` |
| `Missing sudo password` | passwordless sudo is not set (section 1), or add `-- --ask-become-pass` |
| `<key> still holds a placeholder` | that vault key still says `CHANGE-ME`/`PUT-...-HERE`: `scripts/edit-secrets.sh --site ...` |
| `... is not in firewall_ssh_cidrs` | add the range you deploy from |
| `promtool` / `amtool` / `snmp-exporter --dry-run` failed | the named file has an error; the output gives the line |
| download errors after the retries (`connection reset`, `TLS handshake timeout`) | the server's internet link; run again later (finished downloads are kept) |
| Grafana not healthy after 5 minutes | usually `grafana.com` unreachable for the plugin: `ssh` in, `sudo docker logs grafana`, run again |
| locked out of SSH | at the server console: `sudo ufw allow from <your-ip> to any port 22`, fix `firewall_ssh_cidrs`, deploy |

## Changing a secret later

`scripts/edit-secrets.sh --site <folder>` opens the vault decrypted in your
editor and re-encrypts it on save; the decrypted text never sits on disk
outside the editor's temporary file. Commit and push the site repo, then
`scripts/deploy.sh --site <folder>`. The vault password comes from the
Keychain through `ansible/vault-pass.sh`, so no deploy prompts for it.

**`vault.yml` is committed to the site repo, encrypted.** Git is its backup,
and a handover means sharing the site repo and one password. Both scripts
refuse to run if the file is not encrypted, and `.gitignore` cannot help
here, so never commit it after `ansible-vault decrypt`. It is never copied to
the server; the playbook writes only the files derived from it.

## What lives where

| Thing | File | Repo |
|---|---|---|
| Stack definition | `docker-compose.yml` | product |
| Rules, dashboards, Alertmanager routing, SNMP modules | `prometheus/`, `grafana/`, `alertmanager/`, `snmp_exporter/snmp.yml` | product |
| Templates for the per-site files | `ansible/templates/site/` | product |
| Defaults (paths, project name, exporter options) | `ansible/roles/isp_defaults/defaults/main.yml` | product |
| Example targets (fictional ISP) | `inventories/example/`, rendered into `prometheus/targets/` | product |
| Server address | `hosts.ini` | site |
| Network, Telegram group, timezone, firewall ranges, WhatsApp settings | `group_vars/all/vars.yml` | site |
| Passwords, tokens, API logins, SNMP credentials, dead-man URL, WhatsApp recipients | `group_vars/all/vault.yml` | site, **encrypted** |

Nobody edits files on the server. `bash scripts/init.sh` stays for manual
installs (it copies the example files).

## Releasing a change

```bash
# product change: branch -> PR -> merge to main, as usual. Then:
git checkout main && git pull
scripts/deploy.sh --site ~/isp-sites/acme --check   # optional preview, nothing touched
scripts/deploy.sh --site ~/isp-sites/acme           # release
```

A network change (new router, sector, link) is a commit to the site repo
only; deploy the same way. A target-only change reloads Prometheus, nothing
restarts.

`scripts/deploy.sh` (the whole pipeline):

1. **Safety.** Refuses uncommitted changes to the product's tracked files or
   a product commit not yet on `origin/main` (so only reviewed, merged code
   ships). For a site folder that is a git repo: refuses uncommitted or
   untracked files, and a site commit not on any remote branch.
   `--force-branch` overrides the "on origin/main" and "pushed" checks for a
   lab server. A site folder that is not a git repo gets a warning.
2. **Local checks.** The site checks (above), then the site's files are
   rendered into the packaged tree and checked there: `promtool check
   config` (the site's targets), `promtool test rules`, `amtool check-config`
   (the site's Telegram group and timezone), and a playbook syntax check. The
   Docker-based ones need Docker Desktop (skipped with a warning if it is not
   running; the server repeats them).
3. **Package** the exact product commit with `git archive` (uncommitted edits
   and gitignored files never travel; `ansible/` and `inventories/` stay on
   the laptop).
4. **Server preparation** (safe to repeat; only acts on drift): Docker
   Engine + Compose plugin from Docker's repo, enabled at boot; `rsync`;
   `ufw` default-deny with SSH allowed; the `DOCKER-USER` rules for the
   published ports (below).
5. **Staging.** The release is unpacked to a staging folder next to the live
   one; the site's targets, topology, exporter config and Alertmanager values
   are rendered into it and the secrets written from the vault.
6. **Checks against the real files**, before anything live changes: the site
   checks again; placeholder values refused; your own address missing from
   `firewall_ssh_cidrs` refused (lock-out); `docker compose config`;
   `promtool check config`; `amtool check-config` with the real secrets
   mounted; `blackbox_exporter --config.check`; `snmp_exporter --dry-run` with
   the real SNMP credentials; the images pulled (retried on a flaky link) and
   the bridge and exporter images built. Any failure stops here: the live
   stack is untouched.
7. **Sync** staging into `/opt/isp-observability`, rewriting changed files in
   place. Files a container has mounted on their own (for example
   `prometheus.yml`) must keep the same inode or the container keeps reading
   the old one; `rsync --inplace` does that.
8. **Apply only what changed:**

   | What changed | What happens |
   |---|---|
   | `prometheus/` rules, config, targets | Prometheus hot reload (no restart, no scrape gap) |
   | `alertmanager/alertmanager.yml` (incl. Telegram group, timezone) | Alertmanager reload |
   | `snmp_exporter/snmp.yml`, `blackbox/blackbox.yml` | that exporter reloads |
   | `grafana/dashboards/` | nothing; Grafana picks them up within 30 s |
   | `grafana/provisioning/` | Grafana restart |
   | a secret (incl. SNMP credentials), `routeros_exporter/config.yml` (a POP router) | restart of the container(s) that read it |
   | the Grafana password | Grafana restart **and** the admin password reset to the new value (the file alone only works on a first start) |
   | `docker-compose.yml`, an image tag, `.env`, bridge/exporter code | `docker compose up -d --build` recreates just those containers |
   | nothing | nothing restarts |

9. **Verify:** every container running; Prometheus, Alertmanager and Grafana
   healthy; both config reloads reported successful; any scrape target that
   is `DOWN` is listed (a warning, not a failure, since a device may really be
   down). Then the product commit, the ISP and the site commit are written to
   `/opt/isp-observability/DEPLOYED_VERSION` and appended to `.deploy-history`
   there.
10. **Tag** `deploy-<isp_name>-YYYY-MM-DD-HHMM` in the product repo and
    `deploy-YYYY-MM-DD-HHMM` in the site repo (same timestamp).

## Common tasks

| Task | Do |
|---|---|
| Add or change a router, sector, link, vantage point | edit the site's `vars.yml` (+ an API login in the vault for a new POP router), commit, push, `scripts/deploy.sh --site ...` |
| Change an alert rule, dashboard | product PR, merge, `scripts/deploy.sh --site ...` for each ISP |
| Change or add a secret, WhatsApp setting, router API login | `scripts/edit-secrets.sh --site ...`, commit, push, deploy |
| Upgrade an image (e.g. Prometheus) | bump the tag in `docker-compose.yml`, PR, merge, deploy |
| Preview a release | `scripts/deploy.sh --site ... --check` |
| Roll back the product | `scripts/deploy.sh --site ... deploy-acme-2026-10-26-0915` (any earlier tag or commit) |
| Roll back the site data | `git -C <site> checkout deploy-2026-10-26-0915`, deploy, then `git -C <site> checkout main` |
| OS security updates | `scripts/deploy.sh --site ... --os-updates` (reboots only if Ubuntu asks for it) |
| What is running? | `ssh <server> cat /opt/isp-observability/DEPLOYED_VERSION` |
| Lab server, feature branch | `scripts/deploy.sh --site <lab> --force-branch` |
| Change the example site | edit `inventories/example/`, `scripts/render-example.sh`, commit both |

`scripts/deploy.sh --help` lists every option. Extra arguments after `--` go
straight to `ansible-playbook` (e.g. `-- -vv`).

## Firewall

Docker publishes ports through its own iptables rules, which bypass `ufw`.
The playbook therefore:

- enables `ufw` (deny incoming, allow SSH from `firewall_ssh_cidrs`), and
- installs `isp-obs-firewall.service`, which loads the `DOCKER-USER` chain:
  Grafana (3000) from `firewall_noc_cidrs` + `firewall_engineer_cidrs`,
  Prometheus (9090) and Alertmanager (9093) from `firewall_engineer_cidrs`
  only, everything else to those ports dropped. It re-applies whenever
  Docker starts.

The exporter ports are bound to `127.0.0.1` in `docker-compose.yml` and need
no rule. Set the three ranges in the site's `vars.yml`; the playbook refuses
to run with them empty rather than guess.

## Moving or rebuilding the server

New Ubuntu 24.04 box prepared as in [First production install](#first-production-install-step-by-step)
section 1, point the site's `hosts.ini` at it, `scripts/deploy.sh --site ...`.
Everything except metric history is recreated. Restore `prometheus_data` only
if you want the old graphs (Operations Manual, section 7). Stop the old stack
first so the two do not both send alerts.

**Existing manual install:** the Compose project name decides the Docker
volume names. A clone in `~/ISP-Observability-Platform` used the project name
`isp-observability-platform`; set `compose_project` to that in the site's
`vars.yml` so the playbook keeps using its volumes (and history).

## Moving from `ansible/secrets.yml`

The first Ansible release kept one site in `ansible/inventory.ini` +
`ansible/secrets.yml` and the network in this repo's `prometheus/targets/`.
To move:

1. Make a site folder from `inventories/example`; copy `inventory.ini` to its
   `hosts.ini`.
2. Into `vars.yml`: the firewall ranges, WhatsApp settings and
   `compose_project` from the old vault; the routers, sectors, links and
   vantage points from the old target files (one `pops.<POP>.router` per
   router in the old `routeros_exporter_config`, everything else that was in
   `mikrotik-routers.yml` under `snmp_only_routers`); the chat id and
   timezone from `alertmanager.yml`.
3. `scripts/edit-secrets.sh --site <folder>`, then from the old vault
   (`ansible-vault view ansible/secrets.yml`): the passwords and tokens as
   they were; `routeros_api_credentials` (JSON) becomes the
   `routeros_api_logins` mapping; `snmp_auths` becomes `snmpv3_users`; the v2c
   communities that were in `snmp_exporter/snmp.yml` (`public_v2`,
   `public_v1`, `saf_v2`) go into `snmp_communities`.
4. `scripts/deploy.sh --site <folder> --check`: the changed-files list should
   show only comment and ordering changes in the target files.
5. Delete `ansible/secrets.yml` and `ansible/inventory.ini` (both are now
   gitignored).

## Later: GitHub Actions

Not used yet: GitHub's runners cannot reach a server on the management
network without opening SSH to the internet, a self-hosted runner, or a VPN.
When more than one person deploys, a workflow can run the same
`scripts/deploy.sh --site ...` (over Tailscale or from a self-hosted runner,
with the site repo checked out next to it); nothing here needs rewriting.
