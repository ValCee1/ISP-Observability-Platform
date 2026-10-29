# Deploying with Ansible (recommended for production)

One command from your laptop prepares the server, checks everything, and
releases the stack. It replaces the manual steps in [INSTALL.md](INSTALL.md),
which still work if you ever need to install by hand.

```
your laptop                                        monitoring server (Ubuntu 24.04)
-----------                                        --------------------------------
scripts/deploy.sh
  1. safety: clean tree, on main, merged
  2. local checks (promtool, amtool, ...)
  3. git archive <commit>  ---------- ssh ------>  4. Docker, firewall, packages
                                                    5. unpack to staging, write secrets
     ansible/secrets.yml (vault)  -------------->  6. checks against the real files
     vault password: macOS Keychain                7. sync into /opt/isp-observability
                                                    8. compose up; reload/restart
                                                       only what changed
                                                    9. health checks, record version
```

Docker Compose still describes the stack (`docker-compose.yml` is untouched).
Ansible only gets the server and the per-site files right first.

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

Done once. Expect about an hour, most of it filling in site data. The deploy
itself takes 10-40 minutes depending on the internet link (it downloads
about 1.5 GB).

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

# where the server is (gitignored: per site)
cp ansible/inventory.example.ini ansible/inventory.ini
$EDITOR ansible/inventory.ini     # <name> ansible_host=<server-ip> ansible_user=noc

# the vault password, stored in the macOS Keychain (never in a file)
security add-generic-password -a "$USER" -s isp-observability-vault -w
#    (it prompts for the password; pick a long one and keep a copy in your
#     password manager - without it the secrets file cannot be opened)
```

Kept a sudo password instead of section 1 step 3? Add
`-- --ask-become-pass` to every `scripts/deploy.sh` command; it prompts once
per run.

### 3. Site settings and secrets

```bash
git checkout -b site-setup
scripts/edit-secrets.sh
```

This creates `ansible/secrets.yml` from the template and encrypts it
**before** you type anything, then opens it in `$EDITOR`. Fill in every key;
the template says what each one is and [SETUP-INPUTS.md](SETUP-INPUTS.md)
sections A and B where to get the values:

| Key | What |
|---|---|
| `firewall_ssh_cidrs` | ranges allowed to SSH in; **must include the address you deploy from**, or the playbook refuses (it would lock you out) |
| `firewall_engineer_cidrs` | who may open Prometheus (9090) and Alertmanager (9093) |
| `firewall_noc_cidrs` | NOC workstations that may open Grafana (3000); engineers get it too |
| `grafana_admin_password` | a strong password |
| `telegram_bot_token` | from @BotFather |
| `routeros_api_credentials`, `routeros_exporter_config` | the routers and their read-only API users, names identical in both |
| `snmp_auths` | SNMPv3 users |
| `deadman_url` | the healthchecks.io ping URL; required, because the dead-man's switch is on in `alertmanager/alertmanager.yml` (the placeholder is refused) |
| `whatsapp_access_token` | the placeholder is accepted while WhatsApp is in `dry_run` |

Then describe the network in the tracked files ([SETUP-INPUTS.md](SETUP-INPUTS.md)
section C: targets, topology; section D: the Telegram chat id) and ship it
all through a reviewed merge, because `scripts/deploy.sh` only releases
what is on `origin/main`:

```bash
head -1 ansible/secrets.yml         # must print $ANSIBLE_VAULT;1.1;AES256
git add ansible/secrets.yml prometheus/ alertmanager/
git commit -m "Site setup: <site name>"
git push -u origin site-setup       # open a PR, review, merge
git checkout main && git pull
```

### 4. Deploy

```bash
scripts/deploy.sh
```

First the local checks print `ok   ...` lines, then Ansible's tasks scroll
by. On a fresh server the slow tasks are *Docker packages*, *Pull the
published images* and *Build the bridge and exporter images*; a
`FAILED - RETRYING` line there is a flaky download being retried, not an
error. Grafana's very first start takes up to 5 minutes (it sets up its
database and downloads its plugin). It ends with a summary:

```
Deployed main (1a2b3c4d5e6f)
Changed files: ...
Down targets: 0
```

and a local tag `deploy-YYYY-MM-DD-HHMM`. Push it so anyone can roll back to
it: `git push origin deploy-YYYY-MM-DD-HHMM`.

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
`scripts/deploy.sh` again. Add `-- -vv` for more detail.

| Message | Cause and fix |
|---|---|
| `uncommitted changes to tracked files` | commit or stash them |
| `... is not on origin/main` | merge the PR first (`--force-branch` is for a lab server only) |
| `ansible/secrets.yml is NOT encrypted` | `ansible-vault encrypt ansible/secrets.yml` before anything else |
| `vault password not found` | the Keychain entry is missing (section 2) |
| `UNREACHABLE`, `Permission denied (publickey)` | SSH: `ssh-copy-id` again; check `ansible/inventory.ini` |
| `Missing sudo password` | passwordless sudo is not set (section 1), or add `-- --ask-become-pass` |
| `<key> still holds a placeholder` | that vault key still says `CHANGE-ME`/`PUT-...-HERE`: `scripts/edit-secrets.sh` |
| `... is not in firewall_ssh_cidrs` | add the range you deploy from |
| `Routers without credentials: [...]` | a `routeros_exporter_config` name has no entry in `routeros_api_credentials` |
| `promtool` / `amtool` / `snmp-exporter --dry-run` failed | the named file has an error; the output gives the line |
| download errors after the retries (`connection reset`, `TLS handshake timeout`) | the server's internet link; run again later (finished downloads are kept) |
| Grafana not healthy after 5 minutes | usually `grafana.com` unreachable for the plugin: `ssh` in, `sudo docker logs grafana`, run again |
| locked out of SSH | at the server console: `sudo ufw allow from <your-ip> to any port 22`, fix `firewall_ssh_cidrs`, deploy |

## Changing a secret later

`scripts/edit-secrets.sh` opens the vault decrypted in your editor and
re-encrypts it on save; the decrypted text never sits on disk outside the
editor's temporary file. Commit, merge, `scripts/deploy.sh`. The vault
password comes from the Keychain through `ansible/vault-pass.sh`, so no
deploy prompts for it.

**`ansible/secrets.yml` is committed, encrypted.** Git is its backup, and a
handover means sharing one password. `scripts/deploy.sh` refuses to run if
the file is not encrypted, and `.gitignore` cannot help here, so never
commit it after `ansible-vault decrypt`. It is never copied to the server.

## What lives where

| Thing | File | In git? |
|---|---|---|
| Stack definition | `docker-compose.yml` | yes |
| Rules, targets, dashboards, Alertmanager routing | `prometheus/`, `grafana/`, `alertmanager/` | yes |
| Server address | `ansible/inventory.ini` | no (example committed) |
| Defaults (paths, project name) | `ansible/group_vars/all.yml` | yes |
| **Every per-site secret and setting**: the six `secrets/` files, `.env` (WhatsApp), `routeros_exporter/config.yml`, firewall ranges | `ansible/secrets.yml` | yes, **encrypted** |

The playbook writes the server's `secrets/` (folder `700`, files `644`),
`.env` (`600`) and `routeros_exporter/config.yml` from the vault, so nobody
edits files on the server. `bash scripts/init.sh` stays for manual installs.

## Releasing a change

```bash
# edit on a branch -> PR -> merge to main, as usual. Then:
git checkout main && git pull
scripts/deploy.sh --check      # optional preview: what would change, nothing touched
scripts/deploy.sh              # release
```

`scripts/deploy.sh` (the whole pipeline):

1. **Safety.** Refuses uncommitted changes to tracked files, a branch other
   than `main`, or a commit not yet on `origin/main` (so only reviewed,
   merged code ships). `--force-branch` overrides for a lab server.
2. **Local checks** (needs Docker Desktop; skipped with a warning if it is not
   running, the server repeats the important ones): `promtool check config`,
   `promtool test rules`, `amtool check-config`, playbook syntax check, and
   "is `ansible/secrets.yml` still encrypted?".
3. **Package** the exact commit with `git archive` (uncommitted edits and
   gitignored files never travel; `ansible/`, vault included, stays on the
   laptop).
4. **Server preparation** (safe to repeat; only acts on drift): Docker
   Engine + Compose plugin from Docker's repo, enabled at boot; `rsync`;
   `ufw` default-deny with SSH allowed; the `DOCKER-USER` rules for the
   published ports (below).
5. **Staging.** The release is unpacked to a staging folder next to the live
   one, with the per-site files written from the vault.
6. **Checks against the real files**, before anything live changes:
   placeholder values (`CHANGE-ME`, `PUT-...-HERE`, `change-me-now`) refused;
   router names in `routeros_exporter_config` without credentials refused;
   your own address missing from `firewall_ssh_cidrs` refused (lock-out);
   `docker compose config`; `promtool check config`; `amtool check-config`
   with the real secrets mounted; `blackbox_exporter --config.check`;
   `snmp_exporter --dry-run` with the real
   SNMPv3 users (catches `SHA1`/`AES-256` spellings); the images
   pulled (retried on a flaky link) and the bridge and exporter images built. Any failure stops here: the live stack is
   untouched.
7. **Sync** staging into `/opt/isp-observability`, rewriting changed files in
   place. Files a container has mounted on their own (for example
   `prometheus.yml`) must keep the same inode or the container keeps reading
   the old one; `rsync --inplace` does that.
8. **Apply only what changed:**

   | What changed | What happens |
   |---|---|
   | `prometheus/` rules, config, targets | Prometheus hot reload (no restart, no scrape gap) |
   | `alertmanager/alertmanager.yml` | Alertmanager reload |
   | `snmp_exporter/snmp.yml`, `blackbox/blackbox.yml` | that exporter reloads |
   | `grafana/dashboards/` | nothing; Grafana picks them up within 30 s |
   | `grafana/provisioning/` | Grafana restart |
   | a secret, `routeros_exporter/config.yml` | restart of the container(s) that read it |
   | the Grafana password | Grafana restart **and** the admin password reset to the new value (the file alone only works on a first start) |
   | `docker-compose.yml`, an image tag, `.env`, bridge/exporter code | `docker compose up -d --build` recreates just those containers |
   | nothing | nothing restarts |

9. **Verify:** every container running; Prometheus, Alertmanager and Grafana
   healthy; both config reloads reported successful; any scrape target that
   is `DOWN` is listed (a warning, not a failure, since a device may really be
   down). Then the commit is written to `/opt/isp-observability/DEPLOYED_VERSION`,
   appended to `.deploy-history` there, and tagged locally as
   `deploy-YYYY-MM-DD-HHMM`.

## Common tasks

| Task | Do |
|---|---|
| Change an alert rule, target, dashboard | PR, merge, `scripts/deploy.sh` |
| Change or add a secret, WhatsApp setting, router API login | `scripts/edit-secrets.sh`, commit, `scripts/deploy.sh` |
| Upgrade an image (e.g. Prometheus) | bump the tag in `docker-compose.yml`, PR, merge, deploy |
| Preview a release | `scripts/deploy.sh --check` |
| Roll back | `scripts/deploy.sh deploy-2026-10-26-0915` (any earlier tag or commit) |
| OS security updates | `scripts/deploy.sh --os-updates` (reboots only if Ubuntu asks for it) |
| What is running? | `ssh <server> cat /opt/isp-observability/DEPLOYED_VERSION` |
| Lab server, feature branch | `scripts/deploy.sh --force-branch` |

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
no rule. Set the three ranges in `ansible/secrets.yml`; the playbook refuses
to run with them empty rather than guess.

## Moving or rebuilding the server

New Ubuntu 24.04 box prepared as in [First production install](#first-production-install-step-by-step)
section 1, point `ansible/inventory.ini` at it, `scripts/deploy.sh`.
Everything except metric history is recreated. Restore `prometheus_data` only
if you want the old graphs (Operations Manual, section 7). Stop the old stack
first so the two do not both send alerts.

**Existing manual install:** the Compose project name decides the Docker
volume names. A clone in `~/ISP-Observability-Platform` used the project name
`isp-observability-platform`; set `compose_project` to that in
`ansible/secrets.yml` so the playbook keeps using its volumes (and history).

## Later: GitHub Actions

Not used yet: GitHub's runners cannot reach a server on the management
network without opening SSH to the internet, a self-hosted runner, or a VPN.
When more than one person deploys, a workflow can run the same
`scripts/deploy.sh` (over Tailscale or from a self-hosted runner); nothing
here needs rewriting.
