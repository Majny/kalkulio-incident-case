"""Run: python3 -m unittest discover -s tests -v"""
import importlib.util
import io
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import redeliver  # noqa: E402
from kalkulio_logs import calls_by_lead, load_leads, load_records  # noqa: E402


def call(aid, key, status, attempt, delivered_at=None, how=None):
    return {"item": {"assignmentId": aid, "callStatus": status, "callDuration": 10,
                     "callInitDatetime": key, "attempt": attempt, "hlrErrorCode": None},
            "attempt": attempt, "key": key, "delivered_at": delivered_at, "how": how}


class PlanTests(unittest.TestCase):
    def test_plan_rules(self):
        leads = {a: {} for a in ("A-1", "A-2", "A-3", "A-4", "A-5")}
        by_lead = {
            # attempt 1 lost, attempt 2 delivered: do not resend attempt 1
            "A-1": [call("A-1", "T09", "NO_ANSWER", 1), call("A-1", "T11", "ANSWERED_INTERESTED", 2, "11:00", "ok")],
            # attempt 1 delivered, attempt 2 lost: send attempt 2
            "A-2": [call("A-2", "T07", "NO_ANSWER", 1, "07:00", "ok"), call("A-2", "T09", "BUSY", 2)],
            # only call lost: send it
            "A-3": [call("A-3", "T10", "ANSWERED_INTERESTED", 1)],
            # manual resend of attempt 1 landed after attempt 2: needs manual fix
            "A-4": [call("A-4", "T09", "NO_ANSWER", 1, "13:04", "manual"),
                    call("A-4", "T11", "ANSWERED_INTERESTED", 2, "11:08", "ok")],
            # A-5 never called
        }
        cands, skips = redeliver.build_plan(leads, by_lead)
        self.assertEqual({(c["item"]["assignmentId"], c["key"]) for c in cands}, {("A-2", "T09"), ("A-3", "T10")})
        reasons = {(a, r) for a, _, r in skips}
        self.assertIn(("A-1", "superseded_by_newer_attempt"), reasons)
        self.assertIn(("A-4", "crm_shows_older_status_needs_manual_fix"), reasons)
        self.assertIn(("A-5", "not_called_yet"), reasons)

    def test_decide(self):
        c = call("A-1", "2026-09-29T10:00:00Z", "BUSY", 2)
        self.assertEqual(redeliver.decide(c, None)[1], "unknown_assignment_id")
        self.assertEqual(redeliver.decide(c, {"statusHistory": [], "currentStatus": None})[0], "send")
        same = {"statusHistory": [c["item"]], "currentStatus": c["item"]}
        self.assertEqual(redeliver.decide(c, same)[1], "already_delivered")
        newer = {"callInitDatetime": "2026-09-29T12:00:00Z"}
        self.assertEqual(redeliver.decide(c, {"statusHistory": [newer], "currentStatus": newer})[1],
                         "kalkulio_has_newer_status")


class EndToEndTests(unittest.TestCase):
    """Real mock, with 20 % 503s and its 5 req/s limit, sending faster than allowed."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("kmock", ROOT / "case" / "mock_api" / "kalkulio_mock.py")
        cls.mock = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mock)
        cls.mock.FAIL_RATE = 0.2
        cls.mock.preload()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), cls.mock.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        os.environ["KALKULIO_TOKEN"] = cls.mock.TOKEN

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def run_tool(self, out, *extra):
        buf = io.StringIO()
        with redirect_stdout(buf):
            redeliver.main(["--base-url", self.url, "--rate", "200", "--out", out, *extra])
        return buf.getvalue()

    def test_dry_run_apply_rerun(self):
        with tempfile.TemporaryDirectory() as out:
            before = dict(self.mock.counters)
            self.run_tool(out)
            self.assertEqual(self.mock.counters["accepted"], before["accepted"], "dry run must not send")

            self.run_tool(out, "--apply")
            self.assertGreater(self.mock.counters["503"], before["503"])  # retries were exercised
            # every lead's newest call from the logs is now what Kalkulio shows
            by_lead = calls_by_lead(load_records())
            wrong = [a for a, cs in by_lead.items()
                     if self.mock.current[a]["callInitDatetime"] != cs[-1]["key"]]
            # only the 7 leads overwritten by the 13:00 manual resend stay wrong (by design)
            self.assertEqual(len(wrong), 7)
            self.assertEqual(self.mock.counters["rejected"], 0)

            accepted = self.mock.counters["accepted"]
            dup = self.mock.counters["duplicate"]
            report = self.run_tool(out, "--apply")
            self.assertEqual(self.mock.counters["accepted"], accepted)
            self.assertEqual(self.mock.counters["duplicate"], dup)
            self.assertIn("send: 0", report)
            self.assertEqual(len(load_leads()), 4000)


if __name__ == "__main__":
    unittest.main()
