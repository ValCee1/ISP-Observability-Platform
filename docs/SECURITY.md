# Security model

## Summary

The platform is **hosted locally** on a Linux server inside the ISP's own
network. It makes **outbound** connections only (to Telegram and, if enabled,
Meta's WhatsApp API); nothing needs to be reachable from the internet.

Whether it adds attack surface depends on where the server sits. If the
network is split the usual way:

```
 Internet / customers ──X──►  (no route)  ◄──X── Management network (routers, sectors, radios, monitoring server)
```

- **Management network:** the management IPs of routers, sector APs and PTP
  radios. Customers have no route to it.
- **Customer network:** subscriber CPEs and routers addressed separately.

then **the monitoring server lives only on the management side**, adds no
route between the customer side and the management side, and so does not
open a new path *into* the network. What it does add is **one more host on
the management network** that holds read-only credentials. The controls
below are what keep that host from becoming the weak point.

> The claim "no increased attack surface" is true only while these hold:
> the server has no interface or route into the customer network, its
> published ports are firewalled to the management network, and its
> credentials are read-only. Verify them at install time (checklist below).

## What the server can and cannot do to the network

| Channel | Direction | Access level | Notes |
|---|---|---|---|
| ICMP ping (blackbox) | server → devices | none (reachability only) | |
| SNMP (snmp-exporter) | server → devices, UDP 161 | **read-only** community or SNMPv3 user | SNMPv2c is cleartext: restrict the community to the server's IP on every device. SNMPv3 `authPriv` (supported, [snmpv3.md](snmpv3.md)) authenticates and encrypts; use it where the device supports it |
| RouterOS API (routeros-exporter) | server → routers, TCP 8728 (8729 with `use_tls`) | **read-only** RouterOS group (`/user add group=read`) | the exporter only reads `/ppp`, `/ip route`, neighbours; it cannot change config. Prefer `use_tls: true` where certificates exist |
| Telegram | server → internet, HTTPS | send-only bot | |
| WhatsApp (Meta Cloud API) | server → internet, HTTPS | send-only template messages | optional; capped; dry-run by default |

Monitoring never writes to a network device. The earlier config-backup and
frequency-migration features, which needed write access, were removed.

## Controls to apply

1. **Network placement.** Give the server an address on the management
   network only. Do not give it a leg in the customer network, and do not
   enable IP forwarding on it.
   If the server plugs directly into a POP router, use a management-only
   port or VLAN on that router, never the customer bridge. Sectors still
   need a route to and from the POP router before the server can reach them
   (see [GO-LIVE.md](GO-LIVE.md)).
2. **Firewall the published ports.** `docker-compose.yml` publishes
   3000 (Grafana), 9090 (Prometheus) and 9093 (Alertmanager) on all
   interfaces. Allow only the NOC/management workstations to reach 3000
   (and 9090/9093 if needed). The exporter ports (9100/9115/9116/9436) are
   bound to `127.0.0.1` only - Prometheus reaches them over the internal
   Docker network, so they are not reachable from the LAN at all. Docker bypasses simple `ufw` rules for published ports; put
   the restriction in the `DOCKER-USER` iptables chain, or bind a port to a
   specific address (`"10.0.0.5:3000:3000"`) in the compose file.
3. **Device-side limits.** On each router/sector: SNMP allowed only from the
   server's IP; RouterOS API service (`/ip service`) `address=` limited to
   the server's IP; the monitoring user is in the `read` group.
4. **Secrets.** All credentials are files under `secrets/` mounted as Docker
   secrets (never environment variables, never in git): Grafana admin
   password, Telegram bot token, RouterOS API credentials, WhatsApp token,
   SNMPv3 users. Folder `secrets/` is `chmod 700` (only the service account
   can open it); the files inside are `644` because the containers read them
   as non-root users and a Linux bind mount keeps the host mode. Change the Grafana default
   password (`scripts/init.sh` writes a placeholder you must replace) **before
   the first start**: Grafana reads the secret file only when it creates its
   database, so editing the file later does not change an existing password
   (use `grafana cli admin reset-admin-password` or the UI; see SETUP-INPUTS.md).
   Rotate any token that has been pasted into chat or email.
5. **Grafana.** Disable anonymous access (default is off), use a strong
   admin password, and give NOC staff Viewer accounts only.
6. **Host hardening.** Keep Docker and the OS patched; SSH keys only; the
   server runs only this stack. node-exporter reads the host filesystem
   read-only (`/:/host:ro`) and exposes metrics only, not file contents.
7. **Images.** Every image is version-pinned (no `latest`) and plugin
   auto-update is off, so a restart cannot silently pull new code.
8. **WhatsApp cost/abuse limits.** Dry-run by default, hard daily/monthly
   caps, criticals only (see [whatsapp.md](whatsapp.md)).

## Install-time verification checklist

- [ ] `ip addr` / `ip route` on the server show only management-network
      interfaces; `sysctl net.ipv4.ip_forward` is `0` (Docker may set it to
      1 for its own bridge; the host must still have no route into the
      customer network).
- [ ] From a customer-side address, ping/SNMP to a device's management IP
      and to the server's ports **fail**.
- [ ] From an unauthorised host on the management network, ports 9090,
      9093, 9100, 9116, 9115, 9436 are **closed**; 3000 only from the NOC.
- [ ] `snmpwalk` from another host with the community is refused.
- [ ] The RouterOS user is in group `read`; a write attempt with it fails.
- [ ] `git status` shows no secrets; `secrets/` folder is `700`, files `644`.
- [ ] Grafana default password changed.

## Residual risks (honest list)

- A compromised monitoring server exposes the read-only credentials and a
  map of the network. It cannot change devices, but it can read their
  state. Hence firewalling and host hardening above.
- SNMPv2c communities travel in cleartext on the management network on
  any device not yet moved to SNMPv3 (some radios support only v1/v2c).
- The exporter's TLS mode currently skips certificate verification
  (RouterOS commonly uses self-signed certs); it encrypts but does not
  authenticate the router.
- No authentication in front of Prometheus/Alertmanager UIs; rely on
  firewalling or a reverse proxy with auth.
