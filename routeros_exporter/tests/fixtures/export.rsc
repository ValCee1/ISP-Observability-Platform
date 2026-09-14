# 2026-09-09 01:00:00 by RouterOS 7.15.3
# software id = ABCD-1234
/interface bridge
add name=bridge-sector
/ip pool
add name=pppoe-pool ranges=100.64.12.0/24
/ppp profile
add name=home-10m local-address=100.64.12.1 remote-address=pppoe-pool rate-limit=10M/10M
/ip firewall filter
add action=accept chain=forward comment="allow established"
