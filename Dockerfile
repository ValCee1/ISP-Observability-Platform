###############################################################################
# ISP Network Observability Platform — Prometheus image
#
# Bakes this repo's Prometheus configuration (scrape config, recording &
# alerting rules, SNMP/ICMP target lists, topology, promtool tests) into an
# immutable, version-pinned image on top of the upstream prom/prometheus
# release.
#
# The rest of the stack (Grafana, Alertmanager, snmp-exporter,
# blackbox-exporter) keeps running from unmodified upstream images via
# docker-compose.yml, mounting its config straight from this repo.
#
#   docker build -t <dockerhub-user>/isp-observability:v1.4 .
#   docker push   <dockerhub-user>/isp-observability:v1.4
#
# The baked targets/topology are the EXAMPLE site's (inventories/example,
# rendered by scripts/render-example.sh) - valid, fictional data so the build
# checks pass. A real ISP's targets are per site: the Ansible deploy renders
# them and docker-compose.image.yml bind-mounts them over /etc/prometheus/
# targets. Never bake an ISP's targets into a published image (they name its
# devices and addresses) - build only from a tree with the example targets.
###############################################################################
ARG PROMETHEUS_VERSION=v3.13.2

FROM prom/prometheus:${PROMETHEUS_VERSION}

ARG PROMETHEUS_VERSION
LABEL org.opencontainers.image.title="ISP Network Observability Platform" \
      org.opencontainers.image.description="Prometheus with baked-in ISP monitoring config: MikroTik core routers, PTP backhaul links (airMAX/AirFiber/SAF), MikroTik PtMP sectors + CPEs" \
      org.opencontainers.image.version="v1.4" \
      org.opencontainers.image.source="https://github.com/ValCee1/ISP-Observability-Platform" \
      org.opencontainers.image.base.name="docker.io/prom/prometheus:${PROMETHEUS_VERSION}"

# Baked configuration. Owned by nobody (the uid prom/prometheus runs as).
COPY --chown=nobody:nobody prometheus/prometheus.yml /etc/prometheus/prometheus.yml
COPY --chown=nobody:nobody prometheus/rules/         /etc/prometheus/rules/
COPY --chown=nobody:nobody prometheus/targets/       /etc/prometheus/targets/
COPY --chown=nobody:nobody prometheus/topology.yml   /etc/prometheus/topology.yml

# Fail the build if the bundled config or any rule file does not parse,
# and if the promtool alert unit tests do not pass.
RUN ["promtool", "check", "config", "/etc/prometheus/prometheus.yml"]
# Every unit-test file is run (glob, so newly added tests are never skipped).
RUN ["sh", "-c", "promtool test rules /etc/prometheus/rules/tests/*.yml"]

# Entrypoint is inherited from the base image (/bin/prometheus). CMD is
# repeated verbatim from upstream so the config path isn't silently lost if
# CMD is overridden downstream (e.g. to raise --storage.tsdb.retention.time).
CMD [ "--config.file=/etc/prometheus/prometheus.yml", \
      "--storage.tsdb.path=/prometheus", \
      "--web.console.libraries=/usr/share/prometheus/console_libraries", \
      "--web.console.templates=/usr/share/prometheus/consoles" ]
