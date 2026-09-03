# Docker image — build & publish

## What the image is

`isp-observability` is **Prometheus with this repo's configuration baked in**:
`prometheus.yml`, every file under `prometheus/rules/`, the SNMP/ICMP target
lists in `prometheus/targets/`, `topology.yml`, and the promtool alert tests.

It is built `FROM prom/prometheus:v3.13.2` — the config is the only custom
layer. The rest of the stack (Grafana, Alertmanager, snmp-exporter,
blackbox-exporter) stays on unmodified upstream images and mounts its config
from the repo as before.

The build fails if the bundled config or rules don't parse
(`promtool check config`) or if the alert unit tests fail
(`promtool test rules`).

> **Image name.** Docker image references can't contain spaces or uppercase, so
> the repository is `isp-observability` (not "isp observability"). Full
> reference: `<dockerhub-namespace>/isp-observability:v1.0`.

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | the image definition |
| `.dockerignore` | restricts the build context to `prometheus/**` |
| `docker-compose.image.yml` | compose override to run the stack from the image instead of bind mounts |

## Build

```bash
# from the repo root
docker build -t valcee1/isp-observability:v1.0 .
```

Override the Prometheus base version if needed:

```bash
docker build --build-arg PROMETHEUS_VERSION=v3.13.2 -t valcee1/isp-observability:v1.0 .
```

Verify before pushing:

```bash
docker run --rm valcee1/isp-observability:v1.0 --version
docker run --rm --entrypoint promtool valcee1/isp-observability:v1.0 \
  check config /etc/prometheus/prometheus.yml
```

## Publish to a private Docker Hub repo

1. **Create the private repo** on Docker Hub: `Repositories → Create` →
   name `isp-observability`, visibility **Private**. The namespace is your
   Docker Hub username or org (examples below use `valcee1` — replace it).

2. **Log in** (use a Personal Access Token, not your password):

   ```bash
   docker login -u valcee1
   ```

3. **Tag** (skip if you already built with the final name above):

   ```bash
   docker tag valcee1/isp-observability:v1.0 valcee1/isp-observability:v1.0
   ```

4. **Push:**

   ```bash
   docker push valcee1/isp-observability:v1.0
   ```

The repo stays private — anyone pulling it (including other hosts running this
stack) must `docker login` with an account that has access.

### Multi-arch (optional)

If your monitoring host is arm64 (or mixed):

```bash
docker buildx create --use --name isp-obs 2>/dev/null || docker buildx use isp-obs
docker buildx build --platform linux/amd64,linux/arm64 \
  -t valcee1/isp-observability:v1.0 --push .
```

## Run the stack from the image

```bash
docker compose -f docker-compose.yml -f docker-compose.image.yml up -d
```

This replaces the `prometheus` service's config bind mounts with the baked
config while keeping the `prometheus_data` volume, and leaves the other four
services untouched. Secrets (`secrets/grafana_admin_password.txt`,
`secrets/telegram_bot_token.txt`) are still required.

Point at another tag or registry without editing files:

```bash
ISP_OBS_IMAGE=valcee1/isp-observability:v1.1 \
  docker compose -f docker-compose.yml -f docker-compose.image.yml up -d
```

## Updating config after release

Baked config is immutable per tag. Two options:

- **New tag** — edit `prometheus/**`, `docker build -t valcee1/isp-observability:v1.1 .`,
  push, bump `ISP_OBS_IMAGE`. Preferred.
- **Hot patch** — bind-mount a single file over the baked path at run time,
  e.g. add `- ./prometheus/targets/mikrotik-sectors.yml:/etc/prometheus/targets/mikrotik-sectors.yml:ro`
  to the `prometheus` service. Target files reload within 1 min; rule/scrape
  changes need `docker compose restart prometheus`.

## What is *not* in the image

- Secrets — always injected at run time as Docker secrets.
- TSDB data — lives in the `prometheus_data` named volume.
- Grafana dashboards / provisioning, Alertmanager routing, `snmp.yml`,
  `blackbox.yml` — mounted from the repo into their own upstream containers.
