# Dead-man's switch

Nothing inside the stack can tell you the stack itself has died: if the
server, Docker, Prometheus, Alertmanager or the internet uplink goes down,
alerts simply stop. The dead-man's switch turns that silence into an alert.

- `prometheus/rules/deadman.yml` - `Watchdog`, an alert that is **always
  firing**.
- `alertmanager/alertmanager.yml` - first route: Watchdog is re-sent every
  minute to an outside heartbeat service (never to Telegram/WhatsApp).
- The heartbeat service expects a ping every few minutes and alerts you
  (email, Telegram, SMS - its own channels) when the pings stop.

**Not the gateway check.** `prometheus/targets/vantage-points.yml` (the POP
router / default gateway) answers "can the server still see the network?"
from *inside* the stack. The heartbeat is the opposite direction: an
*outside* service noticing that the server itself has gone quiet. A dead
server cannot ping its gateway or send that alert, so only something outside
can catch it.

It is **off by default**: until a real URL is set, Watchdog goes to the
`deadman-off` receiver and is dropped. (A placeholder URL would fail every
minute and set off `AlertDeliveryFailing`.)

## Switch it on (healthchecks.io, free tier)

1. Create a check at https://healthchecks.io: **Period 5 minutes, Grace
   5 minutes**. Add the NOC's notification channels to it (email at minimum;
   it can also post to Telegram). Use a channel that does **not** depend on
   this server.
2. Put the check's ping URL (`https://hc-ping.com/<uuid>`) in
   `secrets/deadman_url.txt`, one line. The URL is a secret: anyone with it
   can fake a heartbeat.
3. In `alertmanager/alertmanager.yml`, Watchdog route: change
   `receiver: deadman-off` to `receiver: deadman`.
4. `docker compose up -d alertmanager`. Within a couple of minutes the check
   turns green on healthchecks.io.

Any service that accepts an HTTP POST and alerts on missing pings works the
same way (Uptime Kuma push monitor, Better Stack heartbeat, Cronitor).

## Test it

`docker compose stop alertmanager` - the check should go red and notify you
after Period + Grace (about 10 minutes). `docker compose start alertmanager`
to recover.

Watchdog does not appear on the NOC Overview incident board
(`rules/incidents.yml` excludes it).
