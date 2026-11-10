"""Run: python3 -m unittest discover -s tests -v"""
import csv
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
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import redeliver  # noqa: E402
from kalkulio_logs import calls_by_lead, load_records  # noqa: E402


def ts(hhmm):
    return f"2026-09-29T{hhmm}:00.000Z"


def call(aid, at, status, attempt, delivered_at=None, how=None):
    return {"item": {"assignmentId": aid, "callStatus": status, "callDuration": 10,
                     "callInitDatetime": ts(at), "attempt": attempt, "hlrErrorCode": None},
            "attempt": attempt, "key": ts(at), "delivered_at": delivered_at and ts(delivered_at), "how": how}


class PlanTests(unittest.TestCase):
    def test_plan_rules(self):
        leads = {a: {} for a in ("A-1", "A-2", "A-3", "A-4", "A-5")}
        by_lead = {
            # attempt 1 lost, attempt 2 delivered: do not resend attempt 1
            "A-1": [call("A-1", "09:00", "NO_ANSWER", 1), call("A-1", "11:00", "ANSWERED_INTERESTED", 2, "11:00", "ok")],
            # attempt 1 delivered, attempt 2 lost: send attempt 2
            "A-2": [call("A-2", "07:00", "NO_ANSWER", 1, "07:00", "ok"), call("A-2", "09:00", "BUSY", 2)],
            # only call lost: send it
            "A-3": [call("A-3", "10:00", "ANSWERED_INTERESTED", 1)],
            # manual resend of attempt 1 landed after attempt 2: needs a manual fix
            "A-4": [call("A-4", "09:00", "NO_ANSWER", 1, "13:04", "manual"),
                    call("A-4", "11:00", "ANSWERED_INTERESTED", 2, "11:08", "ok")],
            # A-5 never called
        }
        cands, skips = redeliver.build_plan(leads, by_lead)
        # interested first, so a canary or an early stop still covers the most valuable leads
        self.assertEqual([(c["item"]["assignmentId"], c["key"]) for c in cands],
                         [("A-3", ts("10:00")), ("A-2", ts("09:00"))])
        reasons = {(a, r) for a, _, r in skips}
        self.assertIn(("A-1", "superseded_by_newer_attempt"), reasons)
        self.assertIn(("A-4", redeliver.MANUAL_FIX), reasons)
        self.assertIn(("A-5", "not_called_yet"), reasons)

    def test_decide(self):
        c = call("A-1", "10:00", "BUSY", 2)
        older, newer = {"callInitDatetime": ts("09:00")}, {"callInitDatetime": ts("12:00")}
        self.assertEqual(redeliver.decide(c, None)[1], "unknown_assignment_id")
        self.assertEqual(redeliver.decide(c, {"statusHistory": [], "currentStatus": None})[0], redeliver.SEND)
        self.assertEqual(redeliver.decide(c, {"statusHistory": [older], "currentStatus": older})[0], redeliver.SEND)
        same = {"statusHistory": [c["item"]], "currentStatus": c["item"]}
        self.assertEqual(redeliver.decide(c, same)[1], "already_delivered")
        self.assertEqual(redeliver.decide(c, {"statusHistory": [newer], "currentStatus": newer})[1],
                         "kalkulio_has_newer_status")
        # current is the status received last, not the newest call: the 12:00 call still wins
        resent_after = {"statusHistory": [newer, older], "currentStatus": older}
        self.assertEqual(redeliver.decide(c, resent_after)[1], "kalkulio_has_newer_status")

    def test_retry_after_parsing(self):
        self.assertEqual(redeliver._seconds("2"), 2.0)
        self.assertIsNone(redeliver._seconds("Wed, 30 Sep 2026 07:00:05 GMT"))
        self.assertIsNone(redeliver._seconds(None))


    def test_send_tells_refused_from_transient(self):
        batch = [call("A-1", "10:00", "BUSY", 1)]
        for error, advice in ((redeliver.Refused("HTTP 422"), "Fix the cause"),
                              (RuntimeError("still failing"), "Run the same command again later")):
            api = mock.Mock(**{"post_calls.side_effect": error})
            with redirect_stdout(io.StringIO()):
                _, stopped = redeliver.send(api, batch, log=lambda rec: None)
            self.assertIn(advice, stopped)


class EndToEndTests(unittest.TestCase):
    """The real mock, with 20 % 503s and its 5 req/s limit, while we send faster than allowed."""

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
        cls.env = mock.patch.dict(os.environ, {"KALKULIO_TOKEN": cls.mock.TOKEN})
        cls.env.start()
        # keep real retries, but without waiting seconds for each backoff
        cls.fast = mock.patch.object(redeliver, "BASE_DELAY", 0.01)
        cls.fast.start()

    @classmethod
    def tearDownClass(cls):
        cls.fast.stop()
        cls.env.stop()
        cls.server.shutdown()

    def run_tool(self, out, *extra, url=None):
        """-> (stdout, exit code)."""
        buf, code = io.StringIO(), 0
        with redirect_stdout(buf):
            try:
                redeliver.main(["--base-url", url or self.url, "--rate", "200", "--out", out, *extra])
            except SystemExit as e:
                code = e.code
        return buf.getvalue(), code

    def test_dry_run_canary_apply_rerun(self):
        with tempfile.TemporaryDirectory() as out:
            before = dict(self.mock.counters)
            _, code = self.run_tool(out)
            self.assertEqual(code, 0)
            self.assertEqual(self.mock.counters["accepted"], before["accepted"], "dry run must not send")
            with open(Path(out) / "for_kalkulio.csv") as f:
                self.assertEqual(sum(1 for _ in csv.DictReader(f)), 1286 + 7)

            self.run_tool(out, "--apply", "--limit", "50")
            self.assertEqual(self.mock.counters["accepted"] - before["accepted"], 50)
            with open(Path(out) / "redeliver_plan_apply.csv") as f:
                held = sum(row["reason"] == "held_back_by_limit" for row in csv.DictReader(f))
            self.assertGreater(held, 0)

            _, code = self.run_tool(out, "--apply")
            self.assertEqual(code, 0)
            self.assertGreater(self.mock.counters["503"], before["503"], "503 retries were exercised")
            self.assertGreater(self.mock.counters["429"], before["429"], "429 retries were exercised")
            self.assertEqual(self.mock.counters["rejected"], 0)
            # every lead's newest call from the logs is now what Kalkulio shows ...
            by_lead = calls_by_lead(load_records())
            wrong = {a for a, cs in by_lead.items() if self.mock.current[a]["callInitDatetime"] != cs[-1]["key"]}
            # ... except the 7 leads overwritten by the 13:00 manual resend (left to Kalkulio, by design)
            self.assertEqual(len(wrong), 7)

            accepted, dup = self.mock.counters["accepted"], self.mock.counters["duplicate"]
            report, code = self.run_tool(out, "--apply")
            self.assertEqual(code, 0)
            self.assertEqual((self.mock.counters["accepted"], self.mock.counters["duplicate"]), (accepted, dup))
            self.assertIn("send: 0", report)

            # the client file comes from the logs, so a later dry run with --limit does not shrink it
            self.run_tool(out, "--limit", "5")
            with open(Path(out) / "for_kalkulio.csv") as f:
                self.assertEqual(sum(1 for _ in csv.DictReader(f)), 1286 + 7)

    def test_wrong_base_url_stops_instead_of_reporting_nothing_to_do(self):
        with tempfile.TemporaryDirectory() as out:
            report, code = self.run_tool(out, "--apply", url=self.url + "/v2")
            self.assertEqual(code, 1)
            self.assertIn("preflight", report)

    def test_second_run_is_blocked_while_first_holds_the_lock(self):
        with tempfile.TemporaryDirectory() as out:
            with redeliver.single_run(Path(out) / "redeliver.lock"):
                with self.assertRaises(SystemExit) as e:
                    redeliver.main(["--base-url", self.url, "--out", out])
            self.assertIn("Another redeliver run is active", str(e.exception.code))


if __name__ == "__main__":
    unittest.main()
