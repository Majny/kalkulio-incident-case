"""Local mock of Kalkulio's call-status intake API.

Behaves like the real endpoint as documented in ../docs/kalkulio-status-api.md.
State is in memory and preloaded with every status that was actually delivered
on 2026-09-29 (read from the tech log), so it reflects what Kalkulio's CRM holds.

Run:  python3 kalkulio_mock.py            (listens on 127.0.0.1:8099)
Env:  KALKULIO_TOKEN   bearer token the mock accepts (default: local-test-token)
      MOCK_FAIL_RATE   share of requests answered 503 (default: 0.03)
"""
import csv
import json
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
TOKEN = os.environ.get("KALKULIO_TOKEN", "local-test-token")
FAIL_RATE = float(os.environ.get("MOCK_FAIL_RATE", "0.03"))
MAX_BATCH = 50
RATE_PER_SEC = 5
REQUIRED = ("assignmentId", "callStatus", "callDuration", "callInitDatetime")

lock = threading.Lock()
known_ids = set()
history = {}   # assignmentId -> list of received items, in RECEIVED order
current = {}   # assignmentId -> item; the CRM shows the most recently RECEIVED status
seen = set()   # (assignmentId, callInitDatetime) -> idempotency key
counters = {"requests": 0, "accepted": 0, "duplicate": 0, "rejected": 0, "429": 0, "503": 0}
window = []    # request timestamps for the rate limiter


def _accept(item):
    key = (item["assignmentId"], item["callInitDatetime"])
    if key in seen:
        return "duplicate"
    seen.add(key)
    history.setdefault(item["assignmentId"], []).append(item)
    current[item["assignmentId"]] = item
    return "accepted"


def preload():
    with open(DATA / "leads_sent.csv") as f:
        known_ids.update(row["assignmentId"] for row in csv.DictReader(f))
    with open(DATA / "tech_logs_2026-09-29.jsonl") as f:
        for line in f:
            r = json.loads(line)
            if r.get("logger_name") == "extensions.fetch_url" and r.get("httpStatus") == 200:
                for item in r["body"]["calls"]:
                    _accept(item)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, headers=None):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _authorized(self):
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self._send(401, {"error": "unauthorized"})
            return False
        return True

    def do_GET(self):
        if not self._authorized():
            return
        if self.path.startswith("/callbot/leads/"):
            aid = self.path.rsplit("/", 1)[-1]
            with lock:
                if aid not in known_ids:
                    return self._send(404, {"error": "unknown assignmentId"})
                return self._send(200, {"assignmentId": aid, "currentStatus": current.get(aid),
                                        "statusHistory": history.get(aid, [])})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/callbot/calls":
            return self._send(404, {"error": "not found"})
        if not self._authorized():
            return
        now = time.monotonic()
        with lock:
            counters["requests"] += 1
            window[:] = [t for t in window if now - t < 1.0]
            if len(window) >= RATE_PER_SEC:
                counters["429"] += 1
                return self._send(429, {"error": "rate limit exceeded"}, {"Retry-After": "1"})
            window.append(now)
        if random.random() < FAIL_RATE:
            with lock:
                counters["503"] += 1
            return self._send(503, {"error": "upstream CRM unavailable, retry later"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            calls = body["calls"]
            assert isinstance(calls, list)
        except Exception:
            return self._send(400, {"error": "body must be {\"calls\": [...]}"})
        if not 1 <= len(calls) <= MAX_BATCH:
            return self._send(413, {"error": f"batch size must be 1..{MAX_BATCH}"})
        results = []
        with lock:
            for item in calls:
                aid = item.get("assignmentId") if isinstance(item, dict) else None
                if not isinstance(item, dict) or any(k not in item for k in REQUIRED):
                    res = {"assignmentId": aid, "result": "rejected", "reason": "missing required field"}
                elif aid not in known_ids:
                    res = {"assignmentId": aid, "result": "rejected", "reason": "unknown assignmentId"}
                else:
                    res = {"assignmentId": aid, "result": _accept(item)}
                counters[res["result"]] += 1
                results.append(res)
        self._send(200, {"results": results})


if __name__ == "__main__":
    preload()
    print(f"Kalkulio mock on http://127.0.0.1:8099  ({len(current)} leads already have a status)")
    ThreadingHTTPServer(("127.0.0.1", 8099), Handler).serve_forever()
