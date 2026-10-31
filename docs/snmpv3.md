# SNMPv3

**ISP policy: SNMPv3 only.** SNMPv2c sends the community string in cleartext. SNMPv3 `authPriv`
authenticates each request (SHA) and encrypts it (AES). The stack supports
both side by side: each target picks its credential set with its `auth`
label, so devices can move to v3 one at a time.

## How it is wired

| Piece | File |
|---|---|
| v1/v2c communities | `secrets/snmp_auths.yml` too (Ansible: `snmp_communities` in the site vault); `snmp_exporter/snmp.yml` holds no credentials |
| v3 users + passwords | `secrets/snmp_auths.yml` (gitignored Docker secret; template `snmp_exporter/snmp_auths.example.yml`; Ansible: `snmpv3_users` in the site vault) |
| Which auth a device uses | `auth:` label on the target in `prometheus/targets/*.yml` |

snmp-exporter loads both config files (two `--config.file` flags in
`docker-compose.yml`) and merges their `auths:`. An auth name may appear in
only one of them; a duplicate stops snmp-exporter at startup.

Every SNMP job passes the target's `auth` label through. A router or sector
with no `auth` label uses the exporter default, `public_v2`.

## Move one device to v3

1. **Create the user on the device** (read-only, server IP only), keeping the
   v2c community for now so monitoring continues during the switch.

   MikroTik RouterOS v6 / v7 (the community name is the v3 username):
   ```
   /snmp community add name=monitoring security=private \
       authentication-protocol=SHA1 authentication-password="<auth-pass>" \
       encryption-protocol=AES encryption-password="<priv-pass>" \
       addresses=<server-ip>/32 read-access=yes write-access=no
   /snmp set enabled=yes
   ```
   SAF: the radio's SNMPv3 user settings; SHA-256 + AES-256 confirmed working
   (`auth_protocol: SHA256`, `priv_protocol: AES256`).
   Ubiquiti airMAX: Services > SNMP Agent > SNMPv3 (**UNVERIFIED - needs live
   device**). **AirFiber does not support SNMPv3** - keep it on v2c.

   Write the protocols the way snmp-exporter spells them: `SHA` (not `SHA1`),
   `AES256` (not `AES-256`). A wrong spelling stops snmp-exporter at startup;
   `docker compose logs snmp-exporter` shows `auth protocol must be ...`.

   Passwords must be at least 8 characters.

2. **Fill in the server side.** In `secrets/snmp_auths.yml`, set the
   `username`, `password` (auth) and `priv_password` of the matching entry
   (`mikrotik_v3`, `ubiquiti_v3`, `saf_v3`, or a new name). Then:
   ```bash
   docker compose restart snmp-exporter
   ```

3. **Test before switching the target:**
   ```bash
   curl -s 'http://localhost:9116/snmp?target=192.168.10.1&module=mikrotik&auth=mikrotik_v3' | grep -c '^sysUpTime'
   ```
   `1` = working. `request timeout` or an error such as `wrong digest` /
   `unknown user name` means the username, password, algorithm or allowed
   address differs from the device (RouterOS silently drops requests for a
   user it does not have, so a mismatch usually shows as a timeout).
   `Unknown auth '<name>'` means the name is not in `snmp_auths.yml`.
   (Use the target's own module: `mikrotik_sector`, `ubiquiti_airmax_link`, ...)

4. **Switch the target:** set `auth: mikrotik_v3` on it in
   `prometheus/targets/*.yml`. Prometheus picks it up within a minute; check
   `http://<server>:9090/targets` shows it UP.

5. **Remove the v2c community** from the device so cleartext SNMP is no
   longer accepted (RouterOS: disable or remove the old `/snmp community`
   entry; also disable the built-in `public` one if it is still there).

## Status (2026-10-25)

| Device | SNMP |
|---|---|
| pop1-home (192.168.10.1) | v3 `mikrotik_v3`, v2c off |
| SAF POP1-POP7 (10.10.6.90 / .91) | v3 `saf_v3`, v2c off |
| AirFiber POP6-POP2 | v2c only (no v3 in firmware) |
| other MikroTik routers / sector 5B | v2c, not migrated yet |

## Notes

- A single slow radio can time out when it is walked many times at once;
  test with one request at a time.

- A device that supports only v1/v2c (AirFiber; possibly older airOS
  M-series) does not meet the policy. Until management decides (ping-only, a
  documented exception, or replacement), keep its community restricted to the
  server's IP.
- Editors that save by replacing the file can leave a running container on
  the old copy; `docker compose restart snmp-exporter` always picks up edits.
- Using different users per device: add one entry per device (e.g.
  `pop1_v3`) and point each target at its own name.
