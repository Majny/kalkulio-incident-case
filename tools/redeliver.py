"""Redeliver call statuses from 29 Sep that never reached Kalkulio.

Dry run by default. Nothing is sent without --apply.

    export KALKULIO_TOKEN=local-test-token
    python3 tools/redeliver.py                      # plan only (reads Kalkulio via GET)
    python3 tools/redeliver.py --apply --limit 50   # canary: send 50, check
    python3 tools/redeliver.py --apply              # send the rest

What it sends: for each lead, the NEWEST call status from the logs, only if
Kalkulio does not already hold it and does not hold anything newer.
Why only the newest: Kalkulio's CRM shows the status received most recently,
so sending an older attempt after a newer one would overwrite the newer one.

Safe to rerun: Kalkulio dedupes on assignmentId + callInitDatetime, we check
the lead's history with GET before sending, and a lock file stops two runs
at once. Every request and result is appended to the log file.
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kalkulio_logs import (LEADS, LOGS, calls_by_lead, crm_state_end_of_day,  # noqa: E402
                           load_leads, load_records)

OUT = Path(__file__).resolve().parent.parent / "out"
REQUIRED = ("assignmentId", "callStatus", "callDuration", "callInitDatetime")
MAX_BATCH = 50
PRIORITY = {"ANSWERED_INTERESTED": 0, "CALLBACK_REQUESTED": 1}


# ---------- planning (pure, no network) ----------

def build_plan(leads, by_lead):
    """Decide from the logs what could be missing. Returns (candidates, skips).

    candidates: newest call per lead that our logs say never arrived.
    skips: list of (assignmentId, call or None, reason) we will not send.
    """
    candidates, skips = [], []
    for aid in sorted(leads):
        calls = by_lead.get(aid)
        if not calls:
            skips.append((aid, None, "not_called_yet"))
            continue
        newest = calls[-1]
        for c in calls[:-1]:
            if c["delivered_at"] is None:
                skips.append((aid, c, "superseded_by_newer_attempt"))
        if newest["delivered_at"] is None:
            candidates.append(newest)
        elif crm_state_end_of_day(calls) is not newest:
            # newest arrived, but an older one was resent by hand afterwards
            skips.append((aid, newest, "crm_shows_older_status_needs_manual_fix"))
    return candidates, skips


def decide(call, lead_doc):
    """Compare one candidate with what Kalkulio holds now. -> (action, reason)."""
    item = call["item"]
    if any(item.get(k) is None for k in REQUIRED):
        return "skip", "missing_required_field"
    if lead_doc is None:
        return "skip", "unknown_assignment_id"
    history = lead_doc.get("statusHistory") or []
    if any(h.get("callInitDatetime") == item["callInitDatetime"] for h in history):
        return "skip", "already_delivered"
    current = lead_doc.get("currentStatus")
    if current and current.get("callInitDatetime", "") > item["callInitDatetime"]:
        return "skip", "kalkulio_has_newer_status"
    return "send", "missing_in_kalkulio"


# ---------- HTTP ----------

class Api:
    def __init__(self, base_url, token, rate, max_retries=6, log=None):
        self.base, self.token = base_url.rstrip("/"), token
        self.min_gap = 1.0 / rate
        self.last = 0.0
        self.max_retries = max_retries
        self.log = log or (lambda rec: None)
        self.stats = Counter()

    def _wait_turn(self):
        gap = self.last + self.min_gap - time.monotonic()
        if gap > 0:
            time.sleep(gap)
        self.last = time.monotonic()

    def _request(self, method, path, body=None):
        """Returns (status, json). Retries 429 / 5xx / network errors with backoff."""
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(self.max_retries + 1):
            self._wait_turn()
            req = urllib.request.Request(self.base + path, data=data, method=method, headers={
                "Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return resp.status, json.loads(resp.read() or b"null")
            except urllib.error.HTTPError as e:
                status, retry_after = e.code, e.headers.get("Retry-After")
                payload = e.read()
                e.close()
                if status in (400, 401, 403, 404, 413):
                    return status, json.loads(payload or b"null")
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                status, retry_after = f"network:{e}", None
            self.stats[f"retry_{status}"] += 1
            delay = float(retry_after) if retry_after else min(2 ** attempt, 30)
            self.log({"event": "retry", "method": method, "path": path, "status": str(status), "sleep": delay})
            time.sleep(delay)
        raise RuntimeError(f"{method} {path} still failing after {self.max_retries} retries (last: {status})")

    def get_lead(self, aid):
        status, doc = self._request("GET", f"/callbot/leads/{aid}")
        if status == 404:
            return None
        if status != 200:
            raise RuntimeError(f"GET lead {aid}: HTTP {status} {doc}")
        return doc

    def post_calls(self, items):
        assert 1 <= len(items) <= MAX_BATCH
        status, doc = self._request("POST", "/callbot/calls", {"calls": items})
        if status != 200:
            raise RuntimeError(f"POST /callbot/calls: HTTP {status} {doc}")
        return doc["results"]


# ---------- run ----------

class Lock:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            sys.exit(f"Another run holds {self.path}. If no run is active, delete it and retry.")
        os.write(self.fd, str(os.getpid()).encode())
        return self

    def __exit__(self, *exc):
        os.close(self.fd)
        os.unlink(self.path)


def run(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    log_path = out / "redeliver_log.jsonl"

    def log(rec):
        with open(log_path, "a") as f:
            f.write(json.dumps({"run": run_id, "ts": datetime.now(timezone.utc).isoformat(), **rec}) + "\n")

    mode = "APPLY" if args.apply else "DRY RUN (nothing will be sent)"
    print(f"redeliver {run_id}  mode: {mode}  api: {args.base_url}")
    log({"event": "start", "apply": args.apply, "limit": args.limit})

    leads = load_leads(args.leads)
    by_lead = calls_by_lead(load_records(args.logs))
    candidates, skips = build_plan(leads, by_lead)
    candidates.sort(key=lambda c: (PRIORITY.get(c["item"]["callStatus"], 9), c["item"]["assignmentId"]))
    print(f"logs: {len(leads)} leads, {len(by_lead)} called, {len(candidates)} with newest status missing per logs")

    api = Api(args.base_url, args.token, args.rate, log=log)
    api.get_lead(next(iter(leads)))  # preflight: fails fast on wrong URL or token
    to_send = []
    for i, c in enumerate(candidates, 1):
        action, reason = decide(c, api.get_lead(c["item"]["assignmentId"]))
        if action == "send":
            to_send.append(c)
        else:
            skips.append((c["item"]["assignmentId"], c, reason))
        if i % 250 == 0:
            print(f"  checked {i}/{len(candidates)} leads in Kalkulio", flush=True)
    # confirm the manual-fix cases against Kalkulio's live state
    for n, (aid, c, reason) in enumerate(skips):
        if reason == "crm_shows_older_status_needs_manual_fix":
            cur = (api.get_lead(aid) or {}).get("currentStatus") or {}
            if cur.get("callInitDatetime") == c["item"]["callInitDatetime"]:
                skips[n] = (aid, c, "already_correct_in_kalkulio")

    if args.limit is not None:
        held = to_send[args.limit:]
        to_send = to_send[:args.limit]
        skips += [(c["item"]["assignmentId"], c, "held_back_by_limit") for c in held]

    plan_path = out / ("redeliver_plan_apply.csv" if args.apply else "redeliver_plan_dry_run.csv")
    with open(plan_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["assignmentId", "action", "reason", "callStatus", "attempt", "callInitDatetime"])
        for c in to_send:
            it = c["item"]
            w.writerow([it["assignmentId"], "send", "missing_in_kalkulio", it["callStatus"], it["attempt"], it["callInitDatetime"]])
        for aid, c, reason in sorted(skips, key=lambda s: (s[2], s[0])):
            it = c["item"] if c else {}
            w.writerow([aid, "skip", reason, it.get("callStatus", ""), it.get("attempt", ""), it.get("callInitDatetime", "")])

    results, stopped = Counter(), None
    if args.apply and to_send:
        try:
            for start in range(0, len(to_send), MAX_BATCH):
                batch = [c["item"] for c in to_send[start:start + MAX_BATCH]]
                log({"event": "post", "items": [(i["assignmentId"], i["callInitDatetime"]) for i in batch]})
                for r in api.post_calls(batch):
                    results[r["result"]] += 1
                    log({"event": "result", **r})
                print(f"  sent {min(start + MAX_BATCH, len(to_send))}/{len(to_send)}", flush=True)
        except RuntimeError as e:  # API kept failing: stop, report what got through, safe to rerun
            stopped = str(e)
            log({"event": "stopped", "error": stopped})

    # report
    print("\nPLAN")
    print(f"  send: {len(to_send)}  by status: {dict(Counter(c['item']['callStatus'] for c in to_send).most_common())}")
    print("  not sending:")
    for reason, n in Counter(s[2] for s in skips).most_common():
        print(f"    {n:5}  {reason}")
    manual = [s for s in skips if s[2] == "crm_shows_older_status_needs_manual_fix"]
    if manual:
        print("  ask Kalkulio to set these by hand (a resend would only be a 'duplicate'):")
        for aid, c, _ in manual:
            print(f"    {aid}: should be {c['item']['callStatus']} (call {c['item']['callInitDatetime']})")
    if args.apply:
        print(f"\nRESULT  {dict(results) or 'nothing sent'}   retries: {dict(api.stats) or 'none'}")
        if results.get("rejected"):
            print("  REJECTED items exist, see the log file")
        if stopped:
            print(f"  STOPPED EARLY: {stopped}\n  Nothing is lost. Run the same command again later.")
    else:
        print("\nDry run. Rerun with --apply to send.")
    print(f"plan: {plan_path}\nlog:  {log_path}")
    log({"event": "end", "results": dict(results), "stopped": stopped})
    if stopped:
        sys.exit(1)
    return to_send, skips, results


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="actually send (default: dry run)")
    p.add_argument("--limit", type=int, help="send at most N statuses (canary)")
    p.add_argument("--base-url", default=os.environ.get("KALKULIO_URL", "http://127.0.0.1:8099"))
    p.add_argument("--rate", type=float, default=4.0, help="max requests per second (API allows 5)")
    p.add_argument("--logs", default=LOGS)
    p.add_argument("--leads", default=LEADS)
    p.add_argument("--out", default=OUT)
    args = p.parse_args(argv)
    args.token = os.environ.get("KALKULIO_TOKEN")
    if not args.token:
        sys.exit("Set KALKULIO_TOKEN (for the mock: local-test-token).")
    Path(args.out).mkdir(parents=True, exist_ok=True)
    with Lock(Path(args.out) / "redeliver.lock"):
        run(args)


if __name__ == "__main__":
    main()
