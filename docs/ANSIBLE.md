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
  Pro). 22.04 leaves standard support in April 2027; 26.04 is fine once it
  has a point release. A user you can SSH into with a key and `sudo`.
- **Laptop (macOS):** this repo, Ansible (`brew install ansible`), Docker
  Desktop for the local checks (optional; the server repeats them), and SSH
  access to the server.

Nothing else: the server needs no GitHub access, no deploy key, and no
internet path to GitHub. The code is pushed from your laptop.

## First-time setup (once per laptop)

```bash
# 1. where the server is (gitignored: per site)
cp ansible/inventory.example.ini ansible/inventory.ini
$EDITOR ansible/inventory.ini

# 2. the vault password, stored in the macOS Keychain (never in a file)
security add-generic-password -a "$USER" -s isp-observability-vault -w
#    (it prompts for the password; pick a long one and keep a copy in your
#     password manager - without it the secrets file cannot be opened)

# 3. the per-site settings and secrets: created from the template and
#    encrypted BEFORE you type anything, then opened in $EDITOR
scripts/edit-secrets.sh
git add ansible/secrets.yml && git commit -m "Site secrets (vault)"
```

Run `scripts/edit-secrets.sh` again whenever a value changes; it opens the
file decrypted in your editor and re-encrypts it on save. The decrypted text
never sits on disk outside the editor's temporary file. The vault password is
read from the Keychain by `ansible/vault-pass.sh`, so no deploy ever prompts.

**`ansible/secrets.yml` is committed, encrypted.** That makes git the backup
and a handover means sharing one password. `scripts/deploy.sh` refuses to
run if the file is not encrypted, and `.gitignore` cannot help here, so never
commit it after `ansible-vault decrypt`.

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
   gitignored files never travel).
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
   SNMPv3 users (catches `SHA1`/`AES-256` spellings); the bridge and
   exporter images built. Any failure stops here: the live stack is
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

New Ubuntu 24.04 box, point `ansible/inventory.ini` at it, `scripts/deploy.sh`.
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
