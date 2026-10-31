# Example site (fictional ISP)

The per-ISP folder that `scripts/deploy.sh --site` deploys. Start a new ISP by
copying this folder **out of the product repo** into a private git repo of its
own, then:

1. `hosts.ini` - the monitoring server.
2. `group_vars/all/vars.yml` - the network (POPs, routers, sectors, links,
   vantage points), Telegram group, timezone, firewall ranges, WhatsApp
   settings.
3. `scripts/edit-secrets.sh --site <folder>` - creates the encrypted
   `group_vars/all/vault.yml` from `vault.example.yml` and opens it.

The product repo's `prometheus/targets/*.yml`, `prometheus/topology.yml` and
`routeros_exporter/config.example.yml` are rendered from this folder
(`scripts/render-example.sh`), so the local checks and the Docker image have
valid example data. Full guide: docs/ANSIBLE.md.
