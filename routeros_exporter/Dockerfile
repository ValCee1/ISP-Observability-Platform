###############################################################################
# routeros-exporter — RouterOS API collector for the ISP Observability stack.
#
# Fills the gaps SNMP can't: per-subscriber PPP/PPPoE session intelligence
# and topology auto-discovery. Runs beside
# snmp-exporter / blackbox-exporter in docker-compose.yml; Prometheus scrapes
# it on :9436 (job "routeros-api").
#
#   docker build -t <user>/isp-routeros-exporter:v0.1 routeros_exporter/
###############################################################################
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ROUTEROS_EXPORTER_CONFIG=/etc/routeros-exporter/config.yml \
    ROUTEROS_CREDENTIALS_FILE=/run/secrets/routeros_api_credentials

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY routeros_exporter/ ./routeros_exporter/

# State (generated topology file) lives here; mount a
# volume over it in compose so it survives restarts.
RUN mkdir -p /var/lib/routeros-exporter && useradd -r -u 10014 exporter \
 && chown -R exporter /var/lib/routeros-exporter
USER exporter

EXPOSE 9436
ENTRYPOINT ["python", "-m", "routeros_exporter"]
