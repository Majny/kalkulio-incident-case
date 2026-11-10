"""Reconcile Kalkulio's numbers with our logs and print every figure used in the docs.

Run: python3 tools/reconcile.py   (output saved in out/reconcile.txt)

Read-only. Each section below backs one part of DIAGNOSIS.md or BUSINESS.md.
"""
import re
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kalkulio_logs import (ENTITY, FETCH, METRICS, PATCH, REQUIRED,  # noqa: E402
                           calls_by_lead, crm_state_end_of_day, delivery_kind,
                           load_leads, load_records, parse_ts)

# From the case README and Marta's email.
PRICE_BOT, PRICE_CC = 2.40, 9.50          # PLN per call attempt
CONTACT_BOT, CONTACT_CC = 0.61, 0.48      # contact rates
CONV_RATE, MARGIN = 0.09, 180             # interested -> policy, PLN margin per policy
MARTA_MISSING = 1526                      # leads without a status, per Marta
HOUSEHOLD_LEADS = 2500                    # extra leads per day for household
WORKING_DAYS = 21                         # per month, our assumption

CONTACT = {"ANSWERED_INTERESTED", "ANSWERED_NOT_INTERESTED", "CALLBACK_REQUESTED"}
LAST_DIAL_UTC = "17:00:00"                # 19:00 Warsaw, dialing stops
PEAK_HOURS = ("09", "10")                 # the spike on 29 Sep
PARTIAL_HOURS = ("18",)                   # last hour of pushes, not a full hour
SPEED_WINDOW_END_UTC = "16:55:00"         # 18:55 Warsaw: 5 min before dialing stops


def section(title):
    print(f"\n== {title}")


def status_posts(recs, kind=None):
    rows = [r for r in recs if r["logger_name"] == FETCH]
    return [r for r in rows if kind is None or delivery_kind(r) == kind]


def first_item(rec):
    return rec["body"]["calls"][0]


def overwritten_by_resend(by_lead):
    """Leads whose newest status arrived, but an older one was resent by hand after it."""
    out = []
    for aid, cs in sorted(by_lead.items()):
        shown, newest = crm_state_end_of_day(cs), cs[-1]
        if shown and newest["delivered_at"] and shown is not newest:
            out.append((aid, newest, shown))
    return out


def record_types(recs):
    section("Record types in the log")
    for (lg, msg, st), n in Counter((r["logger_name"], r["msg"], r.get("httpStatus")) for r in recs).most_common():
        print(f"{n:6}  {lg}  {msg!r}  http={st}")


def lead_count(leads, recs, by_lead):
    section("Number 1: 4,000 leads")
    print("rows in leads_sent.csv:", len(leads), " distinct ids:", len(set(leads)))
    print("products:", dict(Counter(lead["product"] for lead in leads.values())))
    print("ids in logs not in csv:", len(set(by_lead) - set(leads)))
    late = [a for a in leads if a not in by_lead]
    print("leads with no call at all:", len(late))
    print("  of which created >= 17:00 UTC (19:00 Warsaw):",
          sum(leads[a]["createdAt"][11:19] >= LAST_DIAL_UTC for a in late))
    print("  earliest createdAt among them:", min(leads[a]["createdAt"] for a in late))
    print("  latest createdAt of a called lead:", max(leads[a]["createdAt"] for a in by_lead))
    print("last call started:", max(cs[-1]["key"] for cs in by_lead.values()))
    print("last status POST in log:", max(r["time"] for r in status_posts(recs)))


def leads_with_status(leads, by_lead):
    section("Number 2: 2,474 leads with a status")
    with_status = [a for a, cs in by_lead.items() if crm_state_end_of_day(cs)]
    late = len(leads) - len(by_lead)
    print("leads with >=1 delivered status (bot or manual):", len(with_status))
    print("leads called but no status delivered:", len(by_lead) - len(with_status))
    by_bot = [a for a, cs in by_lead.items() if any(c["how"] == "ok" for c in cs)]
    print("  delivered by the bot:", len(by_bot), " only via the manual resend:", len(with_status) - len(by_bot))
    print("4000 - 2474 =", len(leads) - len(with_status), "=",
          len(by_lead) - len(with_status), "(called, status lost) +", late, "(not called yet)")


def status_updates(leads, recs):
    section("Number 3: 5,121 'status updates'")
    patch = [r for r in recs if r["logger_name"] == PATCH]
    ok_patch = [r for r in patch if r.get("httpStatus") == 200]
    failed_patch = [r for r in patch if r.get("httpStatus") != 200]
    print("successful internal PATCH call-result to outbound-api:", len(ok_patch))
    print("  IN_PROGRESS:", sum(r["callStatus"] == "IN_PROGRESS" for r in ok_patch),
          " final result:", sum(r["callStatus"] != "IN_PROGRESS" for r in ok_patch))
    print("failed internal PATCH:", len(failed_patch))
    print("  IN_PROGRESS:", sum(r["callStatus"] == "IN_PROGRESS" for r in failed_patch),
          " final result:", sum(r["callStatus"] != "IN_PROGRESS" for r in failed_patch),
          "(our campaign store has no final result for these calls)")
    fetch = status_posts(recs)
    kinds = Counter(delivery_kind(r) for r in fetch)
    convs = {r["ConversationId"] for r in patch}
    print("calls (conversations):", len(convs))
    print("status POSTs to Kalkulio: bot ok", kinds["ok"], " bot skipped (failed)", kinds["fail"],
          " manual resend", kinds["manual"])
    print("successful POSTs to Kalkulio total:", kinds["ok"] + kinds["manual"])

    bot = [r for r in fetch if delivery_kind(r) != "manual"]
    bot_lines = Counter(r["ConversationId"] for r in bot)
    patch_lines = Counter((r["ConversationId"], r["callStatus"] == "IN_PROGRESS") for r in patch)
    print("every call has 1 IN_PROGRESS + 1 result PATCH line and exactly 1 status line:",
          all(patch_lines[(c, True)] == 1 and patch_lines[(c, False)] == 1 and bot_lines[c] == 1 for c in convs)
          and set(bot_lines) == convs)
    final = {r["ConversationId"]: r["callStatus"] for r in patch if r["callStatus"] != "IN_PROGRESS"}
    print("status in the Kalkulio payload differs from the PATCH result:",
          sum(final[r["ConversationId"]] != first_item(r)["callStatus"] for r in bot))
    print("items per POST:", dict(Counter(len(r["body"]["calls"]) for r in fetch)))
    bad = [c for r in fetch for c in r["body"]["calls"]
           if any(c.get(k) is None for k in REQUIRED) or c["assignmentId"] not in leads]
    print("status items Kalkulio would reject (missing field or unknown id):", len(bad))
    failed = status_posts(recs, "fail")
    print("failure reasons:", dict(Counter(r.get("error") for r in failed)))
    ft = sorted(r["time"] for r in failed)
    print("failed deliveries between", ft[0], "and", ft[-1])
    print("failed by hour UTC:", dict(sorted(Counter(t[11:13] for t in ft).items())))


def manual_resend(recs, by_lead):
    section("Manual resend at ~13:00 UTC")
    man = status_posts(recs, "manual")
    mk = [(first_item(r)["assignmentId"], first_item(r)["callInitDatetime"]) for r in man]
    failed_keys = {(first_item(r)["assignmentId"], first_item(r)["callInitDatetime"])
                   for r in status_posts(recs, "fail")}
    print("manual resends:", len(man), " distinct:", len(set(mk)), " all match a failed call:",
          all(k in failed_keys for k in mk), " window:", min(r["time"] for r in man), "-", max(r["time"] for r in man))
    overwritten = overwritten_by_resend(by_lead)
    print("leads where the manual resend replaced a NEWER status in their CRM:", len(overwritten))
    for a, newest, shown in overwritten:
        print(f"  {a}: real latest {newest['item']['callStatus']} (attempt {newest['attempt']}, delivered "
              f"{newest['delivered_at'][11:19]}) -> CRM now shows {shown['item']['callStatus']} "
              f"(attempt {shown['attempt']}, resent {shown['delivered_at'][11:19]})")
    print("  of which the visible status differs:",
          sum(n["item"]["callStatus"] != s["item"]["callStatus"] for _, n, s in overwritten))


def delivery_patterns(by_lead):
    section("Per-lead delivery patterns (called leads)")
    pat = Counter(tuple((c["attempt"], c["how"] or "lost") for c in cs) for cs in by_lead.values())
    for p, n in pat.most_common():
        print(f"{n:6}  {p}")


def still_missing(recs, by_lead):
    section("What is still missing (input for the redelivery tool)")
    to_send = [cs[-1] for cs in by_lead.values() if cs[-1]["delivered_at"] is None]
    superseded = [c for cs in by_lead.values() for c in cs[:-1] if c["delivered_at"] is None]
    shown = {c["item"]["assignmentId"]: crm_state_end_of_day(by_lead[c["item"]["assignmentId"]]) for c in to_send}
    older = [c for c in to_send if shown[c["item"]["assignmentId"]]]
    print("leads whose newest status never arrived:", len(to_send))
    print("  of which lead has no status at all:", len(to_send) - len(older))
    print("  of which lead shows an older attempt:", len(older))
    print("  by status:", dict(Counter(c["item"]["callStatus"] for c in to_send).most_common()))
    print("older undelivered statuses superseded by a newer delivered one (do not send):", len(superseded))

    overwritten = overwritten_by_resend(by_lead)
    differs = sum(c["item"]["callStatus"] != shown[c["item"]["assignmentId"]]["item"]["callStatus"] for c in older)
    differs_ow = sum(n["item"]["callStatus"] != s["item"]["callStatus"] for _, n, s in overwritten)
    print(f"leads 'with status' that show an older attempt: {len(older)} + {len(overwritten)} overwritten = "
          f"{len(older) + len(overwritten)}; visible status value differs: {differs} + {differs_ow} = {differs + differs_ow}")

    failed = len(status_posts(recs, "fail"))
    resent = len(status_posts(recs, "manual"))
    print(f"bridge, failed deliveries (attempts): {failed} = {resent} resent by hand + "
          f"{len(to_send) - len(older)} leads with no status + {len(older)} newest attempts lost + "
          f"{len(superseded)} superseded = {resent + len(to_send) + len(superseded)}")


def metadata_bug(recs):
    section("Unrelated: update_entity warnings")
    ue = [r for r in recs if r["logger_name"] == ENTITY]
    conv_status = {r["ConversationId"]: first_item(r)["callStatus"]
                   for r in status_posts(recs) if delivery_kind(r) != "manual"}
    print("count:", len(ue), " errors:", dict(Counter(r["error"] for r in ue)))
    print("metaData sent:", dict(Counter(str(r["request"]["metaData"]) for r in ue)))
    print("call status of those conversations:", dict(Counter(conv_status[r["ConversationId"]] for r in ue)))
    print("by hour UTC:", dict(sorted(Counter(r["time"][11:13] for r in ue).items())))
    print("all CALLBACK_REQUESTED calls:", sum(1 for s in conv_status.values() if s == "CALLBACK_REQUESTED"))


def infra(leads):
    """Returns (normal hour, peak hour) requests to outbound-api."""
    section("Infra (from k8s_and_metrics.txt)")
    hourly = {m[0]: int(m[1]) for m in re.findall(r"^(\d\d):00\s+(\d+)$", METRICS.read_text(), re.M)}
    leads_h = Counter(lead["createdAt"][11:13] for lead in leads.values())
    print("hour  handler_requests  leads_created")
    for hh in sorted(hourly):
        print(f"{hh}    {hourly[hh]:5}             {leads_h[hh]:5}")
    print("handler requests == leads per hour:", all(hourly[hh] == leads_h[hh] for hh in hourly))
    normal = sorted(hourly[k] for k in hourly if k not in PEAK_HOURS + PARTIAL_HOURS)
    median = normal[len(normal) // 2]
    print("median normal hour:", median, " peak 09-10 avg:", sum(hourly[h] for h in PEAK_HOURS) / len(PEAK_HOURS))
    return median, max(hourly.values())


def speed_to_first_call(leads, by_lead):
    section("Speed to first call")
    d = sorted((parse_ts(cs[0]["key"]) - parse_ts(leads[a]["createdAt"])).total_seconds() / 60
               for a, cs in by_lead.items())
    print(f"minutes: median {d[len(d)//2]:.1f}  max {d[-1]:.1f}")


def contract_metrics(leads, by_lead):
    section("Contract metrics measured on 29 Sep")
    all_calls = [c for cs in by_lead.values() for c in cs]

    def latency(c):
        return parse_ts(c["delivered_at"]) - parse_ts(c["key"]) - timedelta(seconds=c["item"]["callDuration"])

    def on_time(c):
        return c["delivered_at"] and latency(c) <= timedelta(minutes=15)

    # Per lead, newest call only: an older attempt superseded by a newer one is never sent on purpose.
    newest = [cs[-1] for cs in by_lead.values()]
    ok = sum(1 for c in newest if on_time(c))
    bot_lat = sorted(latency(c).total_seconds() for c in all_calls if c["how"] == "ok")
    print(f"1 status delivery: {ok}/{len(newest)} = {ok/len(newest):.1%} of called leads got their newest status"
          f" within 15 min of call end  (bot delivery latency max {bot_lat[-1]:.1f} s)")
    window = [a for a in leads if "06:00:00" <= leads[a]["createdAt"][11:19] < SPEED_WINDOW_END_UTC]
    fast = [a for a in window if a in by_lead
            and parse_ts(by_lead[a][0]["key"]) - parse_ts(leads[a]["createdAt"]) <= timedelta(minutes=5)]
    print(f"2 speed to first call: {len(fast)}/{len(window)} = {len(fast)/len(window):.1%} within 5 min"
          f" (leads pushed 08:00-18:55 Warsaw)")


def business(leads, by_lead, recs, hours):
    normal, peak = hours
    per_int = CONV_RATE * MARGIN
    n_calls = sum(len(cs) for cs in by_lead.values())

    section("Business: day of 29 Sep")
    print("call attempts:", n_calls, " per called lead:", round(n_calls / len(by_lead), 3))
    print(f"our attempt fees: {n_calls * PRICE_BOT:,.2f} PLN   same attempts at call center: {n_calls * PRICE_CC:,.2f} PLN"
          f"   saving: {n_calls * (PRICE_CC - PRICE_BOT):,.2f} PLN/day")
    contacted = [a for a, cs in by_lead.items() if any(c["item"]["callStatus"] in CONTACT for c in cs)]
    wrong_no = [a for a, cs in by_lead.items() if any(c["item"]["callStatus"] == "WRONG_NUMBER" for c in cs)]
    print(f"contract metric 3 (excl. {len(wrong_no)} WRONG_NUMBER leads): {len(contacted)/(len(by_lead)-len(wrong_no)):.1%}")
    interested = [a for a, cs in by_lead.items() if any(c["item"]["callStatus"] == "ANSWERED_INTERESTED" for c in cs)]
    print(f"contacted leads: {len(contacted)} = {len(contacted)/len(by_lead):.1%} of called, {len(contacted)/len(leads):.1%} of all")
    print(f"interested leads: {len(interested)} = {len(interested)/len(contacted):.1%} of contacted")
    print(f"expected margin per interested lead: {per_int:.2f} PLN")
    share = len(interested) / len(contacted)
    extra = len(leads) * (CONTACT_BOT - CONTACT_CC)
    print(f"per month ({WORKING_DAYS} days): fees {n_calls*PRICE_BOT*WORKING_DAYS:,.0f}"
          f"  saving {n_calls*(PRICE_CC-PRICE_BOT)*WORKING_DAYS:,.0f}")
    print(f"README rates: extra contacts/day vs call center = {len(leads)} x 13pp = {extra:.0f}"
          f" -> extra interested {extra*share:.0f} -> extra margin {extra*share*per_int:,.0f} PLN/day,"
          f" {extra*share*per_int*WORKING_DAYS:,.0f}/month")
    hh_fees = HOUSEHOLD_LEADS * n_calls / len(by_lead) * PRICE_BOT * WORKING_DAYS
    print(f"household upside: {HOUSEHOLD_LEADS} x {n_calls/len(by_lead):.3f} attempts x {PRICE_BOT} x {WORKING_DAYS}"
          f" = {hh_fees:,.0f} PLN/month in attempt fees")

    section("Business: exposure")
    print(f"Marta: {MARTA_MISSING} x {MARGIN} =", MARTA_MISSING * MARGIN)

    def lost(status):
        return [a for a, cs in by_lead.items()
                if cs[-1]["item"]["callStatus"] == status and cs[-1]["delivered_at"] is None]

    overwritten = overwritten_by_resend(by_lead)

    def hidden(status):
        return [a for a, n, _ in overwritten if n["item"]["callStatus"] == status]

    late_int = [a for a, cs in by_lead.items()
                if cs[-1]["item"]["callStatus"] == "ANSWERED_INTERESTED" and cs[-1]["how"] == "manual"]
    lost_int, hid_int = lost("ANSWERED_INTERESTED"), hidden("ANSWERED_INTERESTED")
    lost_cb, hid_cb = lost("CALLBACK_REQUESTED"), hidden("CALLBACK_REQUESTED")
    print("interested leads whose INTERESTED status never arrived:", len(lost_int))
    print("interested leads whose status arrived late via manual resend:", len(late_int))
    print("interested leads hidden by the manual overwrite:", len(hid_int))
    at_risk = len(lost_int) + len(hid_int)
    print(f"upper bound if every one of them is lost: {at_risk} x {per_int:.2f} = {at_risk*per_int:,.2f} PLN")
    print(f"incl. late manual ones: {at_risk+len(late_int)} x {per_int:.2f} = {(at_risk+len(late_int))*per_int:,.2f} PLN")
    print("callback-requested leads whose status never arrived:", len(lost_cb), " hidden by the overwrite:", len(hid_cb))
    wide = at_risk + len(lost_cb) + len(hid_cb)
    print(f"sensitivity, callbacks converting like interested: {wide} x {per_int:.2f} = {wide*per_int:,.2f} PLN")
    failed = len(status_posts(recs, "fail"))
    print(f"attempts with failed delivery: {failed} x {PRICE_BOT} = {failed*PRICE_BOT:,.2f} PLN")
    print(f"whole day's attempt fees: {n_calls*PRICE_BOT:,.2f} PLN")

    section("Household campaign capacity")
    k = (len(leads) + HOUSEHOLD_LEADS) / len(leads)
    print("normal auto hour ~", normal, "req/h; 29 Sep peak", peak, "req/h -> 14 OOMKills")
    print(f"auto + household = {k:.3f} x today's volume, if household has the same daily shape")
    print(f"  normal hour: {normal} -> {normal * k:.0f} req/h")
    print(f"  a peak like 29 Sep: {peak} -> {peak * k:.0f} req/h")
    print("  batching every 2 min instead of per lead: at most 30 req/h per campaign")


def main():
    leads = load_leads()
    recs = load_records()
    by_lead = calls_by_lead(recs)

    record_types(recs)
    lead_count(leads, recs, by_lead)
    leads_with_status(leads, by_lead)
    status_updates(leads, recs)
    manual_resend(recs, by_lead)
    delivery_patterns(by_lead)
    still_missing(recs, by_lead)
    metadata_bug(recs)
    hours = infra(leads)
    speed_to_first_call(leads, by_lead)
    contract_metrics(leads, by_lead)
    business(leads, by_lead, recs, hours)


if __name__ == "__main__":
    main()
