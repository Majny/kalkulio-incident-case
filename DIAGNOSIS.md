# Diagnosis: Kalkulio, 29 Sep 2026

Every number comes from `python3 tools/reconcile.py` (`out/reconcile.txt`). Times UTC.

## What happened

- **Root cause.** `outbound-api` (1 replica, 800Mi limit) handles each pushed lead by loading the whole campaign document (~10k numbers), appending one number and saving it back. Memory scales with push rate.
- **Why it became data loss.** Kalkulio's status step loads the call record from `outbound-api` before posting, and when that fails it logs "status delivery skipped" with no retry. The payload was already complete in the flow (it is in the failed log lines), so the load was not needed.
- **Trigger.** Since 25 Sep Kalkulio pushes every lead as its own request (before: ~12 req/h). On 29 Sep 09:00 to 11:00 they sent 878 and 892 leads/h, 4x a normal hour (222). Handler requests equal leads in every hour. 14 OOMKills between 08:55 and 11:05. 1,611 status deliveries failed, all between 09:02 and 11:05.
- **Made it worse.** No alert on OOMKilled or restarts. The switch to real time push was agreed without engineering. The 13:00 manual resend sent 140 old statuses without checking order and put an older status on 7 leads that already had a newer one (2 of them were interested).
- **Unrelated.** 97 `update_entity` errors all day (06 to 16 h), all on CALLBACK_REQUESTED calls: `metaData` is sent as the literal string `"{metadata}"`, an unrendered template variable.

Dialing itself worked: every lead pushed before 16:58 was called within 2.5 min (median 1.6).

## The numbers

| Number | Meaning |
|---|---|
| **4,000** | Leads pushed. All IDs unique, known to us, `auto`; no test rows found. 3,697 called + 303 pushed after 16:58, after the last dialing slot (19:00 Warsaw), called on 30 Sep. |
| **2,474** | Leads with a status in Kalkulio's CRM. **Marta is right**, our logs give exactly 2,474: 2,344 delivered by the bot + 130 only by the manual resend. No delivered status would be rejected (all fields present, all IDs known). |
| **1,526** | 1,223 called, status lost + 303 not called yet. |
| **5,121** | Successful internal writes to `outbound-api` (2,548 "call started" + 2,573 results). Not statuses to Kalkulio. |
| 4,153 | Actual calls: 2,542 statuses delivered + 1,611 skipped. Plus 140 manual resends = 2,682 delivered POSTs. |

The account manager quoted a real number with the wrong meaning. And 70 of the 2,474 show the wrong status: 63 show attempt 1 because attempt 2 was lost, 7 were overwritten by the resend.

**Still missing:** the newest status of 1,286 leads (1,223 + 63); 309 interested, 95 callback requests.

## Fixes, in order

| # | Fix | Owner |
|---|---|---|
| 1 | Redeliver the 1,286 (`tools/redeliver.py`, Wed 07:00). Kalkulio corrects the 7 overwritten leads by hand. | Engagement |
| 2 | Status step: payload from flow data, retry with backoff, queue failures, alert, daily count check against Kalkulio. Before household. | Engagement |
| 3 | Push in batches every 1 to 2 min until fix 5 ships. | Client, agreed with us |
| 4 | Alerts on OOMKilled and restarts; higher memory limit as a stopgap. | Core product |
| 5 | Append atomically instead of load, modify, save (also needed before adding replicas, or parallel saves overwrite each other); load test at 2x the 29 Sep peak. | Core product |
| 6 | Integration changes on live accounts go through engineering first. | Engagement + account manager |
| 7 | Fix the `{metadata}` template in the callback flow. | Engagement |
