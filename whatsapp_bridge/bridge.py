"""Alertmanager -> WhatsApp Cloud API bridge.

Alertmanager has no native WhatsApp receiver, so it POSTs its standard
webhook payload here and this service turns it into ONE template message
per recipient. It is deliberately tiny (standard library only) and built
so a mistake cannot become a bill:

  * dry_run is the DEFAULT. Nothing is sent to Meta until WHATSAPP_MODE=live.
  * Only status=firing AND severity=critical alerts are ever sent, even if
    Alertmanager's routing is later widened (defence in depth). Warnings
    and "resolved" notices stay on Telegram, which is free.
  * Hard daily and monthly caps (per message, i.e. per recipient), kept
    across restarts. When a cap is hit, further sends are dropped and
    counted - they are never queued, so a storm cannot spend more later.
  * Everything is exposed on /metrics so Prometheus can alert at 80% of
    a cap (rules/whatsapp-budget.yml).
"""
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

GRAPH_URL = "https://graph.facebook.com/v21.0/{phone_number_id}/messages"


class Config:
    """All settings come from environment variables (see docker-compose.yml)."""

    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.mode = env.get("WHATSAPP_MODE", "dry_run").strip().lower()
        if self.mode not in ("dry_run", "live"):
            raise ValueError("WHATSAPP_MODE must be 'dry_run' or 'live'")
        self.phone_number_id = env.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
        # Comma-separated E.164 numbers without '+', e.g. 2348012345678
        self.recipients = [r.strip() for r in env.get("WHATSAPP_RECIPIENTS", "").split(",") if r.strip()]
        self.template = env.get("WHATSAPP_TEMPLATE", "isp_critical_alert").strip()
        self.language = env.get("WHATSAPP_TEMPLATE_LANG", "en").strip()
        self.daily_cap = int(env.get("WHATSAPP_DAILY_CAP", "10"))
        self.monthly_cap = int(env.get("WHATSAPP_MONTHLY_CAP", "100"))
        self.state_file = env.get("WHATSAPP_STATE_FILE", "/var/lib/whatsapp-bridge/state.json")
        self.token_file = env.get("WHATSAPP_TOKEN_FILE", "/run/secrets/whatsapp_access_token")
        self.tz = ZoneInfo(env.get("WHATSAPP_TZ", "Africa/Lagos"))

    def token(self):
        with open(self.token_file) as f:
            return f.read().strip()


class Budget:
    """Daily + monthly message counters, persisted so a restart can't reset them."""

    def __init__(self, path, daily_cap, monthly_cap, tz):
        self.path, self.daily_cap, self.monthly_cap, self.tz = path, daily_cap, monthly_cap, tz
        self.lock = threading.Lock()
        self.state = {"day": "", "day_count": 0, "month": "", "month_count": 0}
        try:
            with open(path) as f:
                self.state.update(json.load(f))
        except (OSError, ValueError):
            pass  # first run, or unreadable file: start from zero

    def _roll(self, now):
        day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
        if self.state["day"] != day:
            self.state["day"], self.state["day_count"] = day, 0
        if self.state["month"] != month:
            self.state["month"], self.state["month_count"] = month, 0

    def allowance(self, now=None):
        """How many more messages may be sent right now."""
        with self.lock:
            self._roll(now or datetime.now(self.tz))
            return max(0, min(self.daily_cap - self.state["day_count"],
                              self.monthly_cap - self.state["month_count"]))

    def record(self, n, now=None):
        with self.lock:
            self._roll(now or datetime.now(self.tz))
            self.state["day_count"] += n
            self.state["month_count"] += n
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                with open(self.path, "w") as f:
                    json.dump(self.state, f)
            except OSError as e:
                print(f"warn: could not persist budget state: {e}", file=sys.stderr)

    def snapshot(self):
        with self.lock:
            self._roll(datetime.now(self.tz))
            return dict(self.state)


def _clean(text, limit):
    """Template parameters may not contain newlines, tabs or long space runs."""
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _object(labels):
    for key, fmt in (("exporter", "Monitoring: {}"), ("link_id", "Link {}"), ("sector", "Sector {}"),
                     ("pop", "POP {}"), ("router", "Router {}"), ("instance", "{}")):
        if labels.get(key):
            return fmt.format(labels[key])
    return labels.get("alertname", "unknown")


def critical_firing(payload):
    # scope=monitoring is excluded here as well as in Alertmanager's route:
    # a monitoring-box problem is often deliberate maintenance (see
    # docs/whatsapp.md), so it stays on Telegram only.
    return [a for a in payload.get("alerts", [])
            if a.get("status") == "firing"
            and a.get("labels", {}).get("severity") == "critical"
            and a.get("labels", {}).get("scope") != "monitoring"]


def build_params(alerts):
    """Two template variables: a short headline and a details line."""
    objects = []
    for a in alerts:
        o = _object(a.get("labels", {}))
        if o not in objects:
            objects.append(o)
    headline = f"{len(alerts)} critical: " + ", ".join(objects[:3]) + (f" +{len(objects) - 3} more" if len(objects) > 3 else "")
    details = " | ".join(_clean(a.get("annotations", {}).get("description")
                                or a.get("annotations", {}).get("summary", ""), 160) for a in alerts[:3])
    return _clean(headline, 200), _clean(details, 500)


def graph_send(cfg, to, p1, p2):
    """One template message to one recipient. Raises on any failure."""
    body = {"messaging_product": "whatsapp", "to": to, "type": "template",
            "template": {"name": cfg.template, "language": {"code": cfg.language},
                         "components": [{"type": "body", "parameters": [
                             {"type": "text", "text": p1}, {"type": "text", "text": p2}]}]}}
    req = urllib.request.Request(
        GRAPH_URL.format(phone_number_id=cfg.phone_number_id), data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {cfg.token()}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


class Bridge:
    def __init__(self, cfg, sender=graph_send):
        self.cfg, self.sender = cfg, sender
        self.budget = Budget(cfg.state_file, cfg.daily_cap, cfg.monthly_cap, cfg.tz)
        self.counts = {"sent": 0, "dry_run": 0, "suppressed_cap": 0, "failed": 0, "ignored": 0}
        self.lock = threading.Lock()

    def _count(self, key, n=1):
        with self.lock:
            self.counts[key] += n

    def handle(self, payload):
        """Returns (http_status, message). 5xx makes Alertmanager retry."""
        alerts = critical_firing(payload)
        if not alerts:
            self._count("ignored")
            return 200, "no firing critical alerts; nothing to send"
        if not self.cfg.recipients:
            self._count("ignored")
            return 200, "no recipients configured"
        p1, p2 = build_params(alerts)
        allowed = self.budget.allowance()
        targets = self.cfg.recipients[:allowed]
        dropped = len(self.cfg.recipients) - len(targets)
        if dropped:
            self._count("suppressed_cap", dropped)
            print(f"CAP: dropped {dropped} message(s): {p1}", file=sys.stderr)
        if self.cfg.mode == "dry_run":
            for to in targets:
                print(f"DRY_RUN would send to {to}: {p1} || {p2}", file=sys.stderr)
            self._count("dry_run", len(targets))
            # Dry runs consume budget too, so caps behave exactly like live.
            self.budget.record(len(targets))
            return 200, f"dry_run: {len(targets)} message(s) logged"
        sent = 0
        for to in targets:
            try:
                self.sender(self.cfg, to, p1, p2)
                sent += 1
            except (urllib.error.URLError, OSError, ValueError) as e:
                self._count("failed")
                print(f"send to {to} failed: {e}", file=sys.stderr)
        if sent:
            self._count("sent", sent)
            self.budget.record(sent)
        if targets and sent < len(targets):
            return 502, f"sent {sent}/{len(targets)}"
        return 200, f"sent {sent}"

    def metrics(self):
        s = self.budget.snapshot()
        lines = [
            "# TYPE whatsapp_messages_total counter",
            *[f'whatsapp_messages_total{{result="{k}"}} {v}' for k, v in self.counts.items()],
            "# TYPE whatsapp_budget_used gauge",
            f'whatsapp_budget_used{{period="day"}} {s["day_count"]}',
            f'whatsapp_budget_used{{period="month"}} {s["month_count"]}',
            "# TYPE whatsapp_budget_cap gauge",
            f'whatsapp_budget_cap{{period="day"}} {self.cfg.daily_cap}',
            f'whatsapp_budget_cap{{period="month"}} {self.cfg.monthly_cap}',
            "# TYPE whatsapp_live gauge",
            f"whatsapp_live {1 if self.cfg.mode == 'live' else 0}",
        ]
        return "\n".join(lines) + "\n"


def make_handler(bridge):
    class H(BaseHTTPRequestHandler):
        def _reply(self, code, text, ctype="text/plain"):
            data = text.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/metrics":
                self._reply(200, bridge.metrics(), "text/plain; version=0.0.4")
            elif self.path == "/healthz":
                self._reply(200, "ok")
            else:
                self._reply(404, "not found")

        def do_POST(self):
            if self.path != "/alert":
                return self._reply(404, "not found")
            try:
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            except ValueError:
                return self._reply(400, "bad json")
            code, msg = bridge.handle(payload)
            self._reply(code, msg)

        def log_message(self, *a):  # quiet access log; we print what matters
            pass
    return H


def main():
    cfg = Config()
    bridge = Bridge(cfg)
    print(f"whatsapp-bridge mode={cfg.mode} recipients={len(cfg.recipients)} "
          f"caps={cfg.daily_cap}/day {cfg.monthly_cap}/month", file=sys.stderr)
    ThreadingHTTPServer(("0.0.0.0", 8080), make_handler(bridge)).serve_forever()


if __name__ == "__main__":
    main()
