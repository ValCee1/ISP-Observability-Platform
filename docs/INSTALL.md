# Installing on another machine

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

# 2. per-site files (all gitignored - never commit these)
mkdir -p secrets
echo 'choose-a-password'      > secrets/grafana_admin_password.txt
echo '<telegram bot token>'   > secrets/telegram_bot_token.txt
echo 'dry-run-placeholder'    > secrets/whatsapp_access_token.txt   # real token only at go-live
cp routeros_exporter/routeros_api_credentials.example.json secrets/routeros_api_credentials.json
cp routeros_exporter/config.example.yml routeros_exporter/config.yml
chmod 600 secrets/*

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

**Firewall:** these ports are published on all interfaces by default. On a
server restrict 3000/9090/9093/9100 to your management network, or bind them
to `127.0.0.1` in `docker-compose.yml` and put a reverse proxy in front.

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
