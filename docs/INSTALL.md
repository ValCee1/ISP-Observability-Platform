# Installing on another machine

> Start with [SETUP-INPUTS.md](SETUP-INPUTS.md): the full list of secrets and
> site-specific files a fresh install needs.

The whole platform is a Docker Compose project: **install = get the repo onto
the machine, fill in the per-site files, `docker compose up -d`.** Nothing is
installed on the host except Docker.

## 1. Linux (recommended for production)

Needs: Docker Engine + the Compose plugin, git (or a tarball), and L3
reachability from the machine to the routers/radios (ICMP + SNMP/UDP 161 +
RouterOS API TCP 8728).

```bash
# 1. get the code (or: tar czf from the dev box and scp it - see "Offline")
git clone https://github.com/ValCee1/ISP-Observability-Platform.git
cd ISP-Observability-Platform

# 2. per-site files. `bash scripts/init.sh` creates all the placeholders for
#    you (see SETUP-INPUTS.md for what goes in each); manual equivalent:
mkdir -p secrets
echo 'choose-a-password'      > secrets/grafana_admin_password.txt
echo '<telegram bot token>'   > secrets/telegram_bot_token.txt
echo 'dry-run-placeholder'    > secrets/whatsapp_access_token.txt   # real token only at go-live
cp routeros_exporter/routeros_api_credentials.example.json secrets/routeros_api_credentials.json
cp snmp_exporter/snmp_auths.example.yml secrets/snmp_auths.yml      # SNMPv3 users (docs/snmpv3.md)
cp routeros_exporter/config.example.yml routeros_exporter/config.yml
# Files 644, folder 700: containers read secrets as non-root users, and on
# Linux a 600 file owned by you is unreadable to them (Alertmanager would
# lose its Telegram token). The 700 folder keeps other host users out.
chmod 700 secrets && chmod 644 secrets/*

# 3. describe the estate
$EDITOR prometheus/targets/mikrotik-routers.yml   # routers
$EDITOR prometheus/targets/mikrotik-sectors.yml   # sectors
$EDITOR prometheus/targets/links.yml              # PTP links
$EDITOR prometheus/targets/vantage-points.yml     # "can the server see out?" targets
$EDITOR routeros_exporter/config.yml              # same routers, API side
$EDITOR secrets/routeros_api_credentials.json     # read-only API user per router
$EDITOR alertmanager/alertmanager.yml             # Telegram chat_id

# 4. optional WhatsApp (leave unset = dry-run, nothing sent) - docs/whatsapp.md
cp /dev/null .env

# 5. start
docker compose up -d --build
docker compose ps
```

Grafana http://<server>:3000 (user `admin`), Prometheus :9090, Alertmanager :9093.

**Start on boot:** every service has `restart: unless-stopped` and Docker
itself is enabled at boot (`sudo systemctl enable docker`), so the stack
returns after a reboot with no extra unit file.

**Firewall:** 3000/9090/9093 are published on all interfaces. On a server
restrict them to your management network, or bind them to `127.0.0.1` in
`docker-compose.yml` and put a reverse proxy in front. The exporter ports
(9100/9115/9116/9436) are already bound to `127.0.0.1`.

**node-exporter on Linux:** you may append `,rslave` to its `/:/host:ro`
mount so mounts made later are seen; not needed for a normal server.

### Offline / no Git on the target

```bash
# on a machine with the repo and internet:
docker compose build
docker save $(docker compose config --images) | gzip > isp-stack-images.tgz
tar czf isp-stack-config.tgz --exclude=secrets --exclude=.git .
# copy both files over, then on the target:
gunzip -c isp-stack-images.tgz | docker load
tar xzf isp-stack-config.tgz && docker compose up -d --no-build
```

### Using the published Prometheus image

`docker-compose.image.yml` swaps Prometheus's bind-mounted config for the
version-pinned baked image (see [DOCKER.md](DOCKER.md)). Useful for a fleet of
identical sites; remember rule changes need a new tag.

### Upgrading

```bash
git pull && docker compose up -d --build
```
Prometheus/Alertmanager config reloads on `docker compose restart <service>`;
target files reload on their own within a minute. Data lives in named volumes
and survives `down`/`up` (not `down -v`).

## Moving an existing installation to another computer

**Do not copy the whole folder.** It contains things that must not travel:
`routeros_exporter/.venv` (a Python environment tied to this OS/CPU),
`__pycache__`, `.claude/`, and local state. Instead move three things
separately:

1. **The configuration (git).** Commit and `git push` from the old machine,
   `git clone` on the new one. This carries rules, dashboards, targets,
   compose files and docs.
2. **The per-site secrets (NOT in git).** Copy these over an encrypted
   channel (scp, an encrypted USB stick, a password manager attachment),
   never email or chat:
   - `secrets/` (Grafana password, Telegram token, RouterOS credentials,
     WhatsApp token, SNMPv3 users)
   - `.env`
   - `routeros_exporter/config.yml`

   ```bash
   # from the old machine
   tar czf site-secrets.tgz secrets .env routeros_exporter/config.yml
   scp site-secrets.tgz user@new-server:~/
   # on the new machine, inside the cloned repo
   tar xzf ~/site-secrets.tgz && chmod 700 secrets && chmod 644 secrets/* && chmod 600 .env && shred -u ~/site-secrets.tgz
   ```
3. **Metric history (optional).** It lives in Docker volumes, not in the
   folder. Archive and restore the `prometheus_data` volume only if you need
   the old graphs (Operations Manual, section 7). Skipping it starts history
   fresh; all configuration and alerts are unaffected.

Then `docker compose up -d --build` on the new machine (images are rebuilt
for its own CPU architecture, so a Mac-to-Linux or ARM-to-x86 move is fine).
Before switching, stop the old stack, or at least the WhatsApp bridge, so the
two machines do not both send alerts, and re-check the firewall and the
management-network placement ([SECURITY.md](SECURITY.md)). WhatsApp message
counters restart at zero on the new machine, so re-confirm the caps.

## 2. Windows

**Yes, via Docker Desktop with the WSL2 backend** (or run the Linux steps
inside a WSL2 Ubuntu distro with Docker). The containers are the same Linux
containers; only the host differs. Not tested here - verify on first install.

- Use PowerShell instead of the bash commands (`mkdir secrets`,
  `Set-Content secrets\telegram_bot_token.txt '<token>'`, `copy` for the
  examples). Write secret files **without a trailing newline or BOM**
  (`Set-Content -NoNewline -Encoding ascii`).
- Keep the repo inside the WSL filesystem (`\\wsl$\...`), not `C:\`, for
  speed and so file permissions behave.
- **Host metrics are the WSL2 VM, not Windows.** `node-exporter` will report
  the VM's CPU/RAM/disk. To monitor the actual Windows host, run the upstream
  `windows_exporter` natively and add a scrape job pointing at
  `host.docker.internal:9182`; `server-health.yml` alerts are written for
  node-exporter metric names, so they would need Windows equivalents.
- Containers reach LAN devices through Docker Desktop's NAT. Outbound
  ICMP/SNMP/API normally work; if the Windows host is on Wi-Fi with client
  isolation, or the router only allows the host's own IP, test with
  `docker compose exec blackbox-exporter ...` first.
- Docker Desktop must start at login and the machine must not sleep - this is
  why a Windows host is a poor production monitor. Use Linux for the
  always-on server and Windows only for dev/demo.
