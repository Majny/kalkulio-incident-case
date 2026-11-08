"""Parse the 29 Sep tech-log export into per-lead call outcomes.

One place for the parsing so the reconciliation and the redelivery tool
read the logs the same way.
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

CASE = Path(__file__).resolve().parent.parent / "case"
LOGS = CASE / "data" / "tech_logs_2026-09-29.jsonl"
LEADS = CASE / "data" / "leads_sent.csv"

FETCH = "extensions.fetch_url"
PATCH = "extensions.update_campaign_call_result"
ENTITY = "extensions.update_entity"


def load_leads(path=LEADS):
    with open(path) as f:
        return {row["assignmentId"]: row for row in csv.DictReader(f)}


def load_records(path=LOGS):
    with open(path) as f:
        return [json.loads(line) for line in f]


def delivery_kind(rec):
    """ok = bot delivered it, manual = 13:00 hand resend, fail = never sent."""
    if "manual retry" in rec["msg"]:
        return "manual"
    return "ok" if rec.get("httpStatus") == 200 else "fail"


def calls_by_lead(records):
    """assignmentId -> list of calls, oldest first.

    Each call: {item, conv, attempt, key, delivered_at (log time of the
    successful POST or None), how ('ok' / 'manual' / None)}.
    The status payload is in the fetch_url record even when delivery failed,
    so the logs alone are enough to rebuild what should have been sent.
    """
    calls = {}  # (assignmentId, callInitDatetime) -> call
    manual = []
    for r in records:
        if r["logger_name"] != FETCH:
            continue
        kind = delivery_kind(r)
        for item in r["body"]["calls"]:
            key = (item["assignmentId"], item["callInitDatetime"])
            if kind == "manual":
                manual.append((key, r["time"]))
                continue
            calls[key] = {
                "item": item,
                "conv": r["ConversationId"],
                "attempt": item.get("attempt"),
                "key": item["callInitDatetime"],
                "delivered_at": r["time"] if kind == "ok" else None,
                "how": "ok" if kind == "ok" else None,
                "failed_at": r["time"] if kind == "fail" else None,
            }
    for key, t in manual:
        c = calls[key]  # every manual resend matches a failed call (checked in reconcile)
        if c["delivered_at"] is None:
            c["delivered_at"], c["how"] = t, "manual"
    by_lead = defaultdict(list)
    for (aid, _), c in calls.items():
        by_lead[aid].append(c)
    for lst in by_lead.values():
        lst.sort(key=lambda c: c["key"])
    return dict(by_lead)


def crm_state_end_of_day(calls):
    """What Kalkulio's CRM showed as current status after 29 Sep.

    Their CRM keeps the status RECEIVED most recently, so order by delivery
    time, not by call time. Returns the call shown as current, or None.
    """
    delivered = [c for c in calls if c["delivered_at"]]
    if not delivered:
        return None
    return max(delivered, key=lambda c: c["delivered_at"])
