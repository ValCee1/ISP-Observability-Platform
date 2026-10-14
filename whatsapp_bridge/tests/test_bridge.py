import os
import sys
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import bridge  # noqa: E402


def cfg(**over):
    env = {"WHATSAPP_RECIPIENTS": "2348000000001", "WHATSAPP_STATE_FILE": os.path.join(tempfile.mkdtemp(), "s.json"),
           "WHATSAPP_DAILY_CAP": "2", "WHATSAPP_MONTHLY_CAP": "100", "WHATSAPP_PHONE_NUMBER_ID": "123"}
    env.update(over)
    return bridge.Config(env)


def payload(*alerts):
    return {"alerts": [{"status": s, "labels": {"severity": sev, "link_id": "L1", "alertname": "LinkDown"},
                        "annotations": {"description": "Link L1\nis down"}} for s, sev in alerts]}


class BridgeTests(unittest.TestCase):
    def test_dry_run_is_default_and_sends_nothing(self):
        calls = []
        b = bridge.Bridge(cfg(), sender=lambda *a: calls.append(a))
        code, _ = b.handle(payload(("firing", "critical")))
        self.assertEqual(code, 200)
        self.assertEqual(calls, [])
        self.assertEqual(b.counts["dry_run"], 1)

    def test_warnings_and_resolved_are_ignored(self):
        calls = []
        b = bridge.Bridge(cfg(WHATSAPP_MODE="live"), sender=lambda *a: calls.append(a))
        b.handle(payload(("firing", "warning"), ("resolved", "critical")))
        self.assertEqual(calls, [])
        self.assertEqual(b.counts["ignored"], 1)

    def test_live_send_and_daily_cap(self):
        calls = []
        b = bridge.Bridge(cfg(WHATSAPP_MODE="live"), sender=lambda *a: calls.append(a))
        for _ in range(4):
            b.handle(payload(("firing", "critical")))
        self.assertEqual(len(calls), 2)            # cap of 2/day
        self.assertEqual(b.counts["suppressed_cap"], 2)

    def test_failure_returns_502_and_does_not_spend_budget(self):
        def boom(*a):
            raise OSError("network")
        b = bridge.Bridge(cfg(WHATSAPP_MODE="live"), sender=boom)
        code, _ = b.handle(payload(("firing", "critical")))
        self.assertEqual(code, 502)
        self.assertEqual(b.budget.snapshot()["day_count"], 0)

    def test_cap_survives_restart(self):
        c = cfg(WHATSAPP_MODE="live")
        b1 = bridge.Bridge(c, sender=lambda *a: None)
        b1.handle(payload(("firing", "critical")))
        b2 = bridge.Bridge(c, sender=lambda *a: None)
        self.assertEqual(b2.budget.snapshot()["day_count"], 1)

    def test_day_rolls_over(self):
        c = cfg()
        bud = bridge.Budget(c.state_file, 1, 100, c.tz)
        d1 = datetime(2026, 10, 14, 10, tzinfo=ZoneInfo("Africa/Lagos"))
        d2 = datetime(2026, 10, 15, 10, tzinfo=ZoneInfo("Africa/Lagos"))
        bud.record(1, d1)
        self.assertEqual(bud.allowance(d1), 0)
        self.assertEqual(bud.allowance(d2), 1)

    def test_params_have_no_newlines(self):
        p1, p2 = bridge.build_params(bridge.critical_firing(payload(("firing", "critical"))))
        self.assertNotIn("\n", p1 + p2)
        self.assertIn("Link L1", p1)


if __name__ == "__main__":
    unittest.main()
