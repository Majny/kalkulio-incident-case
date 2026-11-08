"""Reconcile Marta's numbers with our logs and print every figure used in the docs.

Run: python3 tools/reconcile.py   (output saved in out/reconcile.txt)
"""
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kalkulio_logs import (CASE, ENTITY, FETCH, PATCH, calls_by_lead,  # noqa: E402
                           crm_state_end_of_day, delivery_kind, load_leads,
                           load_records)

CONTACT = {"ANSWERED_INTERESTED", "ANSWERED_NOT_INTERESTED", "CALLBACK_REQUESTED"}
PRICE_BOT, PRICE_CC = 2.40, 9.50
CONV_RATE, MARGIN = 0.09, 180
CUTOFF_UTC = "17:00:00"  # 19:00 Warsaw


def h(title):
    print(f"\n== {title}")


def main():
    leads = load_leads()
    recs = load_records()
    by_lead = calls_by_lead(recs)

    h("Record types in the log")
    for (lg, msg, st), n in Counter((r["logger_name"], r["msg"], r.get("httpStatus")) for r in recs).most_common():
        print(f"{n:6}  {lg}  {msg!r}  http={st}")

    h("Number 1: 4,000 leads")
    print("rows in leads_sent.csv:", len(leads), " distinct ids:", len(set(leads)))
    print("products:", dict(Counter(l["product"] for l in leads.values())))
    print("ids in logs not in csv:", len(set(by_lead) - set(leads)))
    late = [a for a in leads if a not in by_lead]
    print("leads with no call at all:", len(late))
    print("  of which created >= 17:00 UTC (19:00 Warsaw):",
          sum(leads[a]["createdAt"][11:19] >= CUTOFF_UTC for a in late))
    print("  earliest createdAt among them:", min(leads[a]["createdAt"] for a in late))
    print("  latest createdAt of a called lead:", max(leads[a]["createdAt"] for a in by_lead))
    print("last status POST in log:", max(r["time"] for r in recs if r["logger_name"] == FETCH))

    h("Number 2: 2,474 leads with a status")
    with_status = [a for a, cs in by_lead.items() if crm_state_end_of_day(cs)]
    print("leads with >=1 delivered status (bot or manual):", len(with_status))
    print("leads called but no status delivered:", len(by_lead) - len(with_status))
    by_bot = [a for a, cs in by_lead.items() if any(c["how"] == "ok" for c in cs)]
    print("  delivered by the bot:", len(by_bot), " only via the manual resend:", len(with_status) - len(by_bot))
    print("4000 - 2474 =", len(leads) - len(with_status), "=",
          len(by_lead) - len(with_status), "(called, status lost) +", len(late), "(not called yet)")

    h("Number 3: 5,121 'status updates'")
    patch = [r for r in recs if r["logger_name"] == PATCH]
    ok_patch = [r for r in patch if r.get("httpStatus") == 200]
    print("successful internal PATCH call-result to outbound-api:", len(ok_patch))
    print("  IN_PROGRESS:", sum(r["callStatus"] == "IN_PROGRESS" for r in ok_patch),
          " final result:", sum(r["callStatus"] != "IN_PROGRESS" for r in ok_patch))
    print("failed internal PATCH:", len(patch) - len(ok_patch))
    fetch = [r for r in recs if r["logger_name"] == FETCH]
    kinds = Counter(delivery_kind(r) for r in fetch)
    print("calls (conversations):", len({r["ConversationId"] for r in patch}))
    print("status POSTs to Kalkulio: bot ok", kinds["ok"], " bot skipped (failed)", kinds["fail"],
          " manual resend", kinds["manual"])
    print("successful POSTs to Kalkulio total:", kinds["ok"] + kinds["manual"])
    bot_lines = Counter(r["ConversationId"] for r in fetch if delivery_kind(r) != "manual")
    patch_lines = Counter((r["ConversationId"], r["callStatus"] == "IN_PROGRESS") for r in patch)
    convs = {r["ConversationId"] for r in patch}
    print("every call has 1 IN_PROGRESS + 1 result PATCH line and exactly 1 status line:",
          all(patch_lines[(c, True)] == 1 and patch_lines[(c, False)] == 1 and bot_lines[c] == 1 for c in convs)
          and set(bot_lines) == convs)
    final = {r["ConversationId"]: r["callStatus"] for r in patch if r["callStatus"] != "IN_PROGRESS"}
    print("status in the Kalkulio payload differs from the PATCH result:",
          sum(final[r["ConversationId"]] != r["body"]["calls"][0]["callStatus"] for r in fetch if delivery_kind(r) != "manual"))
    print("items per POST:", dict(Counter(len(r["body"]["calls"]) for r in fetch)))
    req = ("assignmentId", "callStatus", "callDuration", "callInitDatetime")
    bad = [c for r in fetch for c in r["body"]["calls"]
           if any(c.get(k) is None for k in req) or c["assignmentId"] not in leads]
    print("status items Kalkulio would reject (missing field or unknown id):", len(bad))
    print("failure reasons:", dict(Counter(r.get("error") for r in fetch if delivery_kind(r) == "fail")))
    ft = sorted(r["time"] for r in fetch if delivery_kind(r) == "fail")
    print("failed deliveries between", ft[0], "and", ft[-1])
    print("failed by hour UTC:", dict(sorted(Counter(t[11:13] for t in ft).items())))

    h("Manual resend at ~13:00 UTC")
    man = [r for r in fetch if delivery_kind(r) == "manual"]
    mk = [(r["body"]["calls"][0]["assignmentId"], r["body"]["calls"][0]["callInitDatetime"]) for r in man]
    failed_keys = {(r["body"]["calls"][0]["assignmentId"], r["body"]["calls"][0]["callInitDatetime"])
                   for r in fetch if delivery_kind(r) == "fail"}
    print("manual resends:", len(man), " distinct:", len(set(mk)), " all match a failed call:",
          all(k in failed_keys for k in mk), " window:", min(r["time"] for r in man), "-", max(r["time"] for r in man))
    overwritten = []
    for a, cs in by_lead.items():
        cur = crm_state_end_of_day(cs)
        newest = cs[-1]
        if cur and newest["delivered_at"] and cur is not newest:
            overwritten.append((a, newest, cur))
    print("leads where the manual resend replaced a NEWER status in their CRM:", len(overwritten))
    for a, newest, cur in sorted(overwritten, key=lambda x: x[0]):
        print(f"  {a}: real latest {newest['item']['callStatus']} (attempt {newest['attempt']}, delivered "
              f"{newest['delivered_at'][11:19]}) -> CRM now shows {cur['item']['callStatus']} "
              f"(attempt {cur['attempt']}, resent {cur['delivered_at'][11:19]})")
    print("  of which the visible status differs:",
          sum(n["item"]["callStatus"] != c["item"]["callStatus"] for _, n, c in overwritten))

    h("Per-lead delivery patterns (called leads)")
    pat = Counter(tuple((c["attempt"], c["how"] or "lost") for c in cs) for cs in by_lead.values())
    for p, n in pat.most_common():
        print(f"{n:6}  {p}")

    h("What is still missing (input for the redelivery tool)")
    to_send, superseded = [], []
    for a, cs in by_lead.items():
        newest = cs[-1]
        if newest["delivered_at"] is None:
            to_send.append(newest)
        superseded += [c for c in cs[:-1] if c["delivered_at"] is None]
    print("leads whose newest status never arrived:", len(to_send))
    print("  of which lead has no status at all:", sum(not crm_state_end_of_day(by_lead[c['item']['assignmentId']]) for c in to_send))
    print("  of which lead shows an older attempt:", sum(bool(crm_state_end_of_day(by_lead[c['item']['assignmentId']])) for c in to_send))
    print("  by status:", dict(Counter(c["item"]["callStatus"] for c in to_send).most_common()))
    print("older undelivered statuses superseded by a newer delivered one (do not send):", len(superseded))
    print("leads 'with status' that show the wrong one: 63 older attempt + overwritten =",
          sum(bool(crm_state_end_of_day(by_lead[c['item']['assignmentId']])) for c in to_send) + len(overwritten))

    h("Unrelated: update_entity warnings")
    ue = [r for r in recs if r["logger_name"] == ENTITY]
    conv_status = {r["ConversationId"]: r["body"]["calls"][0]["callStatus"] for r in fetch if delivery_kind(r) != "manual"}
    print("count:", len(ue), " errors:", dict(Counter(r["error"] for r in ue)))
    print("metaData sent:", dict(Counter(str(r["request"]["metaData"]) for r in ue)))
    print("call status of those conversations:", dict(Counter(conv_status[r["ConversationId"]] for r in ue)))
    print("by hour UTC:", dict(sorted(Counter(r["time"][11:13] for r in ue).items())))
    print("all CALLBACK_REQUESTED calls:", sum(1 for s in conv_status.values() if s == "CALLBACK_REQUESTED"))

    h("Infra (from k8s_and_metrics.txt)")
    txt = (CASE / "data" / "k8s_and_metrics.txt").read_text()
    hourly = {m[0]: int(m[1]) for m in re.findall(r"^(\d\d):00\s+(\d+)$", txt, re.M)}
    leads_h = Counter(l["createdAt"][11:13] for l in leads.values())
    print("hour  handler_requests  leads_created")
    for hh in sorted(hourly):
        print(f"{hh}    {hourly[hh]:5}             {leads_h[hh]:5}")
    print("handler requests == leads per hour:", all(hourly[hh] == leads_h[hh] for hh in hourly))
    normal = [hourly[k] for k in hourly if k not in ("09", "10", "18")]
    print("median normal hour:", sorted(normal)[len(normal) // 2], " peak 09-10 avg:", (hourly["09"] + hourly["10"]) / 2)

    h("Speed to first call")
    from datetime import datetime
    P = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))  # noqa: E731
    d = sorted((P(cs[0]["key"]) - P(leads[a]["createdAt"])).total_seconds() / 60 for a, cs in by_lead.items())
    print(f"minutes: median {d[len(d)//2]:.1f}  max {d[-1]:.1f}")

    h("Contract metrics measured on 29 Sep")
    from datetime import timedelta
    all_calls = [c for cs in by_lead.values() for c in cs]
    on_time = [c for c in all_calls if c["delivered_at"] and P(c["delivered_at"]) - (
        P(c["key"]) + timedelta(seconds=c["item"]["callDuration"])) <= timedelta(minutes=15)]
    lat = sorted((P(c["delivered_at"]) - P(c["key"]) - timedelta(seconds=c["item"]["callDuration"])).total_seconds()
                 for c in all_calls if c["how"] == "ok")
    print(f"1 status delivery: {len(on_time)}/{len(all_calls)} = {len(on_time)/len(all_calls):.1%} within 15 min of call end"
          f"  (bot delivery latency max {lat[-1]:.1f} s)")
    window = [a for a in leads if "06:00:00" <= leads[a]["createdAt"][11:19] < "16:58:00"]
    fast = [a for a in window if a in by_lead and (P(by_lead[a][0]["key"]) - P(leads[a]["createdAt"])) <= timedelta(minutes=5)]
    print(f"2 speed to first call: {len(fast)}/{len(window)} = {len(fast)/len(window):.1%} within 5 min (leads 08:00-18:58 Warsaw)")

    h("Business: day of 29 Sep")
    n_calls = sum(len(cs) for cs in by_lead.values())
    print("call attempts:", n_calls, " per called lead:", round(n_calls / len(by_lead), 3))
    print(f"our attempt fees: {n_calls * PRICE_BOT:,.2f} PLN   same attempts at call center: {n_calls * PRICE_CC:,.2f} PLN"
          f"   saving: {n_calls * (PRICE_CC - PRICE_BOT):,.2f} PLN/day")
    contacted = [a for a, cs in by_lead.items() if any(c["item"]["callStatus"] in CONTACT for c in cs)]
    wrong_no = [a for a, cs in by_lead.items() if any(c["item"]["callStatus"] == "WRONG_NUMBER" for c in cs)]
    print(f"contract metric 3 (excl. {len(wrong_no)} WRONG_NUMBER leads): {len(contacted)/(len(by_lead)-len(wrong_no)):.1%}")
    interested = [a for a, cs in by_lead.items() if any(c["item"]["callStatus"] == "ANSWERED_INTERESTED" for c in cs)]
    print(f"contacted leads: {len(contacted)} = {len(contacted)/len(by_lead):.1%} of called, {len(contacted)/len(leads):.1%} of all")
    print(f"interested leads: {len(interested)} = {len(interested)/len(contacted):.1%} of contacted")
    per_int = CONV_RATE * MARGIN
    print(f"expected margin per interested lead: {per_int:.2f} PLN")
    extra_contacts = 4000 * (0.61 - 0.48)
    share = len(interested) / len(contacted)
    print(f"per month (21 days): fees {n_calls*PRICE_BOT*21:,.0f}  saving {n_calls*(PRICE_CC-PRICE_BOT)*21:,.0f}")
    print(f"README rates: extra contacts/day vs call center = 4000 x 13pp = {extra_contacts:.0f}"
          f" -> extra interested {extra_contacts*share:.0f} -> extra margin {extra_contacts*share*per_int:,.0f} PLN/day, {extra_contacts*share*per_int*21:,.0f}/month")

    h("Business: exposure")
    print("Marta: 1526 x 180 =", 1526 * 180)
    lost_int, late_int, overwritten_int = [], [], []
    for a, cs in by_lead.items():
        newest = cs[-1]
        if newest["item"]["callStatus"] != "ANSWERED_INTERESTED":
            continue
        if newest["delivered_at"] is None:
            lost_int.append(a)
        elif newest["how"] == "manual":
            late_int.append(a)
    overwritten_int = [a for a, n, c in overwritten if n["item"]["callStatus"] == "ANSWERED_INTERESTED"]
    print("interested leads whose INTERESTED status never arrived:", len(lost_int))
    print("interested leads whose status arrived late via manual resend:", len(late_int))
    print("interested leads hidden by the manual overwrite:", len(overwritten_int))
    at_risk = len(lost_int) + len(overwritten_int)
    print(f"upper bound if every one of them is lost: {at_risk} x {per_int:.2f} = {at_risk*per_int:,.2f} PLN")
    print(f"incl. late manual ones: {at_risk+len(late_int)} x {per_int:.2f} = {(at_risk+len(late_int))*per_int:,.2f} PLN")
    cb = [a for a, cs in by_lead.items() if cs[-1]["item"]["callStatus"] == "CALLBACK_REQUESTED" and cs[-1]["delivered_at"] is None]
    print("callback-requested leads whose status never arrived:", len(cb))
    affected_attempts = sum(1 for r in fetch if delivery_kind(r) == "fail")
    print(f"attempts with failed delivery: {affected_attempts} x {PRICE_BOT} = {affected_attempts*PRICE_BOT:,.2f} PLN")
    print(f"whole day's attempt fees: {n_calls*PRICE_BOT:,.2f} PLN")

    h("Household campaign capacity")
    print("normal auto hour ~", sorted(normal)[len(normal) // 2], "req/h; 29 Sep peak", max(hourly.values()), "req/h -> 14 OOMKills")
    k = (4000 + 2500) / 4000
    print(f"auto + household = {k:.3f} x today's volume, if household has the same daily shape")
    print(f"  normal hour: {sorted(normal)[len(normal) // 2]} -> {sorted(normal)[len(normal) // 2] * k:.0f} req/h")
    print(f"  a peak like 29 Sep: {max(hourly.values())} -> {max(hourly.values()) * k:.0f} req/h")
    print(f"  batching every 2 min instead of per lead: at most 30 req/h per campaign")


if __name__ == "__main__":
    main()
