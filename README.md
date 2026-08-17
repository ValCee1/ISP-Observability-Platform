# Observability stack (Prometheus + SNMP exporter + Grafana)

Run the stack with:

```bash
docker compose up -d
```

Secrets are stored in the local `secret/` directory and mounted as Docker secrets. Update the files before starting the stack:

- `secret/grafana_admin_password.txt` for the Grafana admin password
- `secret/snmp.yml` for the router SNMP community and MIB modules

Grafana: http://localhost:3000 (user: admin)
Prometheus: http://localhost:9090
SNMP exporter metrics: http://localhost:9116/metrics

The Prometheus SNMP job targets the router IP in `prometheus/prometheus.yml` (for example `192.168.10.1`), and the exporter reads the SNMP credentials from the mounted secret config so the stack can scrape a LAN router without hardcoding sensitive values in the compose file.
