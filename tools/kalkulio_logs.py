"""Parse the 29 Sep tech-log export into per-lead call outcomes.

One place for the parsing, so the reconciliation and the redelivery tool
read the logs the same way.
"""
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

CASE = Path(__file__).resolve().parent.parent / "case"
LOGS = CASE / "data" / "tech_logs_2026-09-29.jsonl"
LEADS = CASE / "data" / "leads_sent.csv"
METRICS = CASE / "data" / "k8s_and_metrics.txt"

# logger_name values in the export
FETCH = "extensions.fetch_url"                     # status POST to Kalkulio
PATCH = "extensions.update_campaign_call_result"   # internal write to outbound-api
ENTITY = "extensions.update_entity"

# Fields Kalkulio's API requires on every status item.
REQUIRED = ("assignmentId", "callStatus", "callDuration", "callInitDatetime")


def parse_ts(s):
    """ISO 8601 with a trailing Z -> aware datetime."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_leads(path=LEADS):
    with open(path) as f:
        return {row["assignmentId"]: row for row in csv.DictReader(f)}


def load_records(path=LOGS):
    with open(path) as f:
        return [json.loads(line) for line in f]


def delivery_kind(rec):
    """'ok' = the bot delivered it, 'manual' = the 13:00 hand resend, 'fail' = never sent."""
    if "manual retry" in rec["msg"]:
        return "manual"
    return "ok" if rec.get("httpStatus") == 200 else "fail"


def calls_by_lead(records):
    """assignmentId -> list of calls, oldest call first.

    Each call is {item, attempt, key, delivered_at, how}:
      item          the exact status payload the bot built for Kalkulio
      key           callInitDatetime, Kalkulio's dedupe key together with the id
      delivered_at  log time of the successful POST, or None if it never arrived
      how           'ok' (bot), 'manual' (hand resend) or None

    The payload is logged even when delivery failed, so the logs alone are
    enough to rebuild what should have been sent.
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
            elif key not in calls:
                calls[key] = {
                    "item": item,
                    "attempt": item.get("attempt"),
                    "key": item["callInitDatetime"],
                    "delivered_at": r["time"] if kind == "ok" else None,
                    "how": "ok" if kind == "ok" else None,
                }
            elif kind == "ok":  # logged twice: a success always wins over a failure
                calls[key]["delivered_at"], calls[key]["how"] = r["time"], "ok"
    for key, t in manual:
        if key not in calls:
            raise ValueError(f"manual resend {key} has no matching bot call in the logs")
        c = calls[key]
        if c["delivered_at"] is None:
            c["delivered_at"], c["how"] = t, "manual"
    by_lead = defaultdict(list)
    for (aid, _), c in calls.items():
        by_lead[aid].append(c)
    for lst in by_lead.values():
        lst.sort(key=lambda c: parse_ts(c["key"]))
    return dict(by_lead)


def crm_state_end_of_day(calls):
    """The call Kalkulio's CRM showed as current status after 29 Sep, or None.

    Their CRM shows the status RECEIVED most recently, so this orders by
    delivery time, not by call time.
    """
    delivered = [c for c in calls if c["delivered_at"]]
    if not delivered:
        return None
    return max(delivered, key=lambda c: parse_ts(c["delivered_at"]))
