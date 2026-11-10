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
the lead's history with GET before sending, and a lock stops two runs at once
from the same out/ directory. Every request and result is appended to the log.

Exit codes: 0 done, 1 stopped early (the message says whether to rerun later
or fix something first), 2 Kalkulio rejected some items (see the log).
"""
import argparse
import csv
import fcntl
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kalkulio_logs import (LEADS, LOGS, REQUIRED, calls_by_lead,  # noqa: E402
                           crm_state_end_of_day, load_leads, load_records,
                           parse_ts)

OUT = Path(__file__).resolve().parent.parent / "out"
MAX_BATCH = 50          # API limit per request
BASE_DELAY = 1.0        # seconds, first backoff; doubles on every retry
MAX_BACKOFF = 30        # seconds, also caps a server's Retry-After
# Send interested leads and callbacks first: if the run stops early or only
# the canary goes out, the leads worth the most are already in the CRM.
PRIORITY = {"ANSWERED_INTERESTED": 0, "CALLBACK_REQUESTED": 1}

SEND, MANUAL_FIX = "send", "crm_shows_older_status_needs_manual_fix"


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
            skips.append((aid, newest, MANUAL_FIX))
    candidates.sort(key=lambda c: (PRIORITY.get(c["item"]["callStatus"], 9), c["item"]["assignmentId"]))
    return candidates, skips


def decide(call, lead_doc):
    """Compare one candidate with what Kalkulio holds now. -> (action, reason)."""
    item = call["item"]
    if any(item.get(k) is None for k in REQUIRED):
        return "skip", "missing_required_field"
    if lead_doc is None:
        return "skip", "unknown_assignment_id"
    ours = parse_ts(item["callInitDatetime"])
    known = [parse_ts(h["callInitDatetime"]) for h in lead_doc.get("statusHistory") or []
             if h.get("callInitDatetime")]
    if ours in known:
        return "skip", "already_delivered"
    # Check the whole history, not currentStatus: current is the status
    # received last, which is not always the newest call.
    if any(t > ours for t in known):
        return "skip", "kalkulio_has_newer_status"
    return SEND, "missing_in_kalkulio"


# ---------- HTTP ----------

class Refused(RuntimeError):
    """Kalkulio answered with an error that a retry will not change (4xx)."""


def _seconds(retry_after):
    """Retry-After in seconds, or None if missing or not a number (HTTP-date)."""
    try:
        return float(retry_after)
    except (TypeError, ValueError):
        return None


class KalkulioClient:
    """Kalkulio status API with a client-side rate limit and retries.

    Retries 429, 5xx and network errors with backoff. Any other HTTP error
    is returned to the caller at once, since retrying would not change it.
    Retrying a POST is safe because Kalkulio dedupes every item.
    """

    def __init__(self, base_url, token, rate, max_retries=6, log=None):
        self.base, self.token = base_url.rstrip("/"), token
        self.min_gap = 1.0 / rate
        self.last = 0.0
        self.max_retries = max_retries
        self.log = log or (lambda rec: None)
        self.retries = Counter()

    def _wait_turn(self):
        gap = self.last + self.min_gap - time.monotonic()
        if gap > 0:
            time.sleep(gap)
        self.last = time.monotonic()

    def _request(self, method, path, body=None):
        """-> (status, parsed json). Raises RuntimeError when retries run out."""
        data = json.dumps(body).encode() if body is not None else None
        status = None
        for attempt in range(self.max_retries + 1):
            self._wait_turn()
            req = urllib.request.Request(self.base + path, data=data, method=method, headers={
                "Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
            retry_after = None
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return resp.status, json.loads(resp.read() or b"null")
            except urllib.error.HTTPError as e:
                status, retry_after = e.code, e.headers.get("Retry-After")
                payload = e.read()
                e.close()
                if status != 429 and status < 500:
                    return status, _json_or_text(payload)
            except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError,
                    json.JSONDecodeError) as e:
                status = f"network:{e}"
            if attempt == self.max_retries:
                break
            self.retries[str(status)[:40]] += 1
            delay = min(_seconds(retry_after) or BASE_DELAY * 2 ** attempt, MAX_BACKOFF)
            self.log({"event": "retry", "method": method, "path": path, "status": str(status), "sleep": delay})
            time.sleep(delay)
        raise RuntimeError(f"{method} {path} still failing after {self.max_retries} retries (last: {status})")

    def get_lead(self, aid):
        """Kalkulio's view of one lead, or None if Kalkulio does not know the id."""
        status, doc = self._request("GET", f"/callbot/leads/{aid}")
        if status == 404:
            return None
        if status != 200:
            raise Refused(f"GET lead {aid}: HTTP {status} {doc}")
        return doc

    def post_calls(self, items):
        if not 1 <= len(items) <= MAX_BATCH:
            raise ValueError(f"batch of {len(items)}, the API takes 1..{MAX_BATCH}")
        status, doc = self._request("POST", "/callbot/calls", {"calls": items})
        if status != 200:
            raise Refused(f"POST /callbot/calls: HTTP {status} {doc}")
        results = doc["results"]
        if len(results) != len(items):
            raise RuntimeError(f"POST /callbot/calls: {len(results)} results for {len(items)} items")
        return results


def _json_or_text(raw):
    try:
        return json.loads(raw or b"null")
    except json.JSONDecodeError:
        return raw.decode(errors="replace")[:200]


# ---------- steps of a run ----------

def check_live(api, candidates, skips):
    """GET every candidate from Kalkulio and keep only what is really missing.

    Also re-checks the manual-fix leads, in case Kalkulio already fixed them.
    Returns the items to send; skips is extended in place.
    """
    to_send = []
    for i, c in enumerate(candidates, 1):
        action, reason = decide(c, api.get_lead(c["item"]["assignmentId"]))
        if action == SEND:
            to_send.append(c)
        else:
            skips.append((c["item"]["assignmentId"], c, reason))
        if i % 250 == 0:
            print(f"  checked {i}/{len(candidates)} leads in Kalkulio", flush=True)
    for n, (aid, c, reason) in enumerate(skips):
        if reason == MANUAL_FIX:
            cur = (api.get_lead(aid) or {}).get("currentStatus") or {}
            if cur.get("callInitDatetime") == c["item"]["callInitDatetime"]:
                skips[n] = (aid, c, "already_correct_in_kalkulio")
    return to_send


def write_plan(path, to_send, skips):
    """One CSV row per decision: what we send and what we hold back, with the reason."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["assignmentId", "action", "reason", "callStatus", "attempt", "callInitDatetime"])
        for c in to_send:
            item = c["item"]
            w.writerow([item["assignmentId"], SEND, "missing_in_kalkulio", item["callStatus"],
                        item["attempt"], item["callInitDatetime"]])
        for aid, c, reason in sorted(skips, key=lambda s: (s[2], s[0])):
            item = c["item"] if c else {}
            w.writerow([aid, "skip", reason, item.get("callStatus", ""), item.get("attempt", ""),
                        item.get("callInitDatetime", "")])


def write_client_file(path, candidates, skips):
    """The file for Kalkulio's team: missing results, then the leads they must fix by hand.

    Built from the logs only, so it is the same whatever was already sent or --limit.
    """
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["assignmentId", "callStatus", "callInitDatetime", "callDuration", "attempt", "note"])
        rows = [(c, "missing, will be loaded into the CRM") for c in candidates]
        rows += [(c, "CRM shows an older call, please set this status by hand")
                 for _, c, reason in skips if reason == MANUAL_FIX]
        for c, note in rows:
            item = c["item"]
            w.writerow([item["assignmentId"], item["callStatus"], item["callInitDatetime"],
                        item["callDuration"], item["attempt"], note])


def send(api, to_send, log):
    """POST in batches. -> (results by kind, error that stopped the run or None)."""
    results = Counter()
    try:
        for start in range(0, len(to_send), MAX_BATCH):
            batch = [c["item"] for c in to_send[start:start + MAX_BATCH]]
            log({"event": "post", "items": [(i["assignmentId"], i["callInitDatetime"]) for i in batch]})
            for r in api.post_calls(batch):
                results[r["result"]] += 1
                log({"event": "result", **r})
            print(f"  sent {start + len(batch)}/{len(to_send)}", flush=True)
    except Refused as e:  # a rerun would get the same answer
        stopped = f"{e}\n  Kalkulio refused the request. Fix the cause before running again."
    except RuntimeError as e:  # the API kept failing
        stopped = f"{e}\n  Nothing is lost. Run the same command again later."
    except KeyboardInterrupt:
        stopped = "Interrupted. Nothing is lost. Run the same command again."
    else:
        return results, None
    log({"event": "stopped", "error": stopped})
    return results, stopped


def print_report(apply, to_send, skips, results, stopped, retries):
    print("\nPLAN")
    print(f"  send: {len(to_send)}  by status: {dict(Counter(c['item']['callStatus'] for c in to_send).most_common())}")
    print("  not sending:")
    for reason, n in Counter(s[2] for s in skips).most_common():
        print(f"    {n:5}  {reason}")
    manual = [s for s in skips if s[2] == MANUAL_FIX]
    if manual:
        print("  ask Kalkulio to set these by hand (a resend would only be a 'duplicate'):")
        for aid, c, _ in manual:
            print(f"    {aid}: should be {c['item']['callStatus']} (call {c['item']['callInitDatetime']})")
    if not apply:
        print("\nDry run. Rerun with --apply to send.")
        return
    print(f"\nRESULT  {dict(results) or 'nothing sent'}   retries: {dict(retries) or 'none'}")
    if results.get("rejected"):
        print("  REJECTED items exist, see the log file")
    if stopped:
        print(f"  STOPPED EARLY: {stopped}")


def _shown(path):
    """Path relative to the working directory when inside it, else absolute."""
    path = Path(path).resolve()
    try:
        return path.relative_to(Path.cwd())
    except ValueError:
        return path


def run(args):
    """One full run. Returns the exit code."""
    out = Path(args.out)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    log_path = out / "redeliver_log.jsonl"

    def log(rec):
        with open(log_path, "a") as f:
            f.write(json.dumps({"run": run_id, "ts": datetime.now(timezone.utc).isoformat(), **rec}) + "\n")

    print(f"redeliver {run_id}  mode: {'APPLY' if args.apply else 'DRY RUN (nothing will be sent)'}  api: {args.base_url}")
    log({"event": "start", "apply": args.apply, "limit": args.limit})

    leads = load_leads(args.leads)
    by_lead = calls_by_lead(load_records(args.logs))
    candidates, skips = build_plan(leads, by_lead)
    print(f"logs: {len(leads)} leads, {len(by_lead)} called, {len(candidates)} with newest status missing per logs")
    client_path = out / "for_kalkulio.csv"
    if not args.apply:
        write_client_file(client_path, candidates, skips)

    api = KalkulioClient(args.base_url, args.token, args.rate, log=log)
    # Preflight: every lead in leads_sent.csv was pushed by Kalkulio, so it must
    # exist there. A 404 here means a wrong URL, not "nothing to do".
    probe = next(iter(leads), None)
    if probe is None or api.get_lead(probe) is None:
        raise RuntimeError(f"preflight: Kalkulio does not know lead {probe}. Check --base-url.")

    to_send = check_live(api, candidates, skips)
    if args.limit is not None:
        skips += [(c["item"]["assignmentId"], c, "held_back_by_limit") for c in to_send[args.limit:]]
        to_send = to_send[:args.limit]

    plan_path = out / ("redeliver_plan_apply.csv" if args.apply else "redeliver_plan_dry_run.csv")
    write_plan(plan_path, to_send, skips)

    results, stopped = send(api, to_send, log) if args.apply and to_send else (Counter(), None)
    print_report(args.apply, to_send, skips, results, stopped, api.retries)
    print(f"plan: {_shown(plan_path)}\nlog:  {_shown(log_path)}")
    if not args.apply:
        print(f"file for Kalkulio: {_shown(client_path)}")
    log({"event": "end", "results": dict(results), "stopped": stopped})
    return 1 if stopped else 2 if results.get("rejected") else 0


@contextmanager
def single_run(path):
    """Exclusive lock on path. The OS releases it when the process dies, even on kill -9."""
    with open(path, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit(f"Another redeliver run is active ({path}).")
        yield


def positive(cast):
    def parse(s):
        v = cast(s)
        if v <= 0:
            raise argparse.ArgumentTypeError(f"must be > 0, got {s}")
        return v
    return parse


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="actually send (default: dry run)")
    p.add_argument("--limit", type=positive(int), help="send at most N statuses (canary)")
    p.add_argument("--base-url", default=os.environ.get("KALKULIO_URL", "http://127.0.0.1:8099"))
    p.add_argument("--rate", type=positive(float), default=4.0, help="max requests per second (API allows 5)")
    p.add_argument("--logs", default=LOGS)
    p.add_argument("--leads", default=LEADS)
    p.add_argument("--out", default=OUT)
    args = p.parse_args(argv)
    args.token = os.environ.get("KALKULIO_TOKEN")
    if not args.token:
        sys.exit("Set KALKULIO_TOKEN (for the mock: local-test-token).")
    Path(args.out).mkdir(parents=True, exist_ok=True)
    with single_run(Path(args.out) / "redeliver.lock"):
        try:
            code = run(args)
        except RuntimeError as e:
            print(f"\nSTOPPED: {e}\nNothing unsafe happened. Fix the cause and run the same command again.")
            code = 1
    if code:
        sys.exit(code)


if __name__ == "__main__":
    main()
