# WhatsApp alerting (cost-controlled)

Critical alerts can additionally be sent over WhatsApp. WhatsApp is the only
**paid** channel here, so the design is "off until you opt in, and bounded
once on". Telegram is unchanged and remains the primary channel.

```
Prometheus -> Alertmanager --(critical only, continue:true)--> whatsapp-bridge --> Meta Cloud API
                          \--> Telegram (as before)
```

## Cost controls (all enforced in code/config)

| Control | Where | Effect |
|---|---|---|
| **dry_run by default** | `WHATSAPP_MODE` (`.env`) | nothing leaves the box; messages are logged |
| Criticals only | Alertmanager route + bridge re-checks | warnings / resolved stay on Telegram (free) |
| No `scope=monitoring` | Alertmanager route + bridge | server/exporter down, vantage lost, delivery failing stay on Telegram: usually someone is deliberately working on the box, and if the server has no network WhatsApp can't be sent anyway |
| Grouping | route `group_by: [scope]` | a 40-router outage = one message per recipient |
| Slow repeat | `repeat_interval: 6h` | vs 2 h on Telegram |
| Hard daily cap | `WHATSAPP_DAILY_CAP` (default 10) | extra sends dropped, never queued |
| Hard monthly cap | `WHATSAPP_MONTHLY_CAP` (default 100) | worst-case spend is bounded |
| Caps persist | `whatsapp_bridge_data` volume | restarts cannot reset them |
| Budget alerts | `whatsapp-budget.yml` | Telegram warning at 80 % of a cap |
| Dry runs count | bridge | caps behave identically before and after go-live |

Cap counts are **per message, i.e. per recipient** (that is how Meta bills).
Worst-case monthly spend = `WHATSAPP_MONTHLY_CAP` x Meta's per-message rate
for the template category in your country. Look the current rate up on
Meta's pricing page before going live; it is not hard-coded anywhere here.

## Components

- `whatsapp_bridge/bridge.py` — standard-library-only webhook receiver
  (`POST /alert`, `GET /metrics`, `GET /healthz`) + unit tests in
  `whatsapp_bridge/tests/`.
- `alertmanager/alertmanager.yml` — `whatsapp` receiver + first route.
- `docker-compose.yml` — `whatsapp-bridge` service, `whatsapp_access_token` secret.
- `prometheus/rules/whatsapp-budget.yml` — `WhatsAppBudgetNearlyUsed`, `WhatsAppSendFailing`.

## Going live (checklist)

1. Create a Meta developer app with the WhatsApp product. For development use
   Meta's **free test number**; add your own number(s) as verified recipients.
2. Create a message template named `isp_critical_alert`, category **Utility**,
   language `en`, body: `ISP alert: {{1}}. {{2}}` and wait for approval.
3. Put the access token in `secrets/whatsapp_access_token.txt` (gitignored).
   Prefer a System User token, not a temporary one.
4. In `.env` (gitignored):
   ```
   WHATSAPP_PHONE_NUMBER_ID=...
   WHATSAPP_RECIPIENTS=2348012345678          # E.164, no +, comma-separated
   WHATSAPP_DAILY_CAP=10
   WHATSAPP_MONTHLY_CAP=100
   WHATSAPP_MODE=dry_run                      # watch logs first
   ```
5. `docker compose up -d --build whatsapp-bridge alertmanager prometheus`, then
   `docker compose logs -f whatsapp-bridge` and trigger a critical alert.
   You should see `DRY_RUN would send to ...`.
6. When satisfied set `WHATSAPP_MODE=live` and `docker compose up -d whatsapp-bridge`.

## Operating notes

- Start with **one** recipient (the on-call number). Each extra recipient
  multiplies cost.
- Template variables may not contain newlines; the bridge flattens text.
- A send failure returns HTTP 502 so Alertmanager retries; failed sends do
  not consume budget. Persistent failure raises `WhatsAppSendFailing` and
  `AlertDeliveryFailing` (integration `webhook`).
- Messages outside Meta's rules (unapproved template, unverified recipient)
  fail at Meta; check `docker compose logs whatsapp-bridge`.
- Free-form replies/2-way chat are out of scope: only template alerts are sent.
