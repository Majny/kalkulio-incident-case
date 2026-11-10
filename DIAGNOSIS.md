# Diagnosis: Kalkulio, 29 Sep 2026

Numbers from `tools/reconcile.py` (`out/reconcile.txt`), times UTC.

## What happened

- **Root cause of the outage (core product).** `outbound-api` (1 replica, 800Mi) loads the whole campaign document (~10k numbers) per pushed lead, appends one and saves it, so memory grows with concurrent pushes.
- **Root cause of the data loss (ours).** The status step reloads the call from `outbound-api` and, if that fails, logs "status delivery skipped" with no retry, though the payload was in the flow.
- **Trigger.** Since 25 Sep Kalkulio pushes each lead as its own request (before: ~12 req/h). 09:00 to 11:00: ~890 leads/h, 4x normal: 14 OOMKills, 1,611 deliveries failed.
- **Made it worse.** No restart alert (2 restarts by 08:55, 0 in 7 days before). Switch agreed without engineering. The 13:00 manual resend of 140 statuses put an older status on 7 leads. Also hit: our campaign store (1,580 results missing), likely other accounts.
- **Kalkulio's "socket hang up"** is not our status POSTs (those never left our side); likely their pushes hit the restarting pod and retries worked (every lead reached us).
- **Unrelated.** 97 of 278 callback calls failed `update_entity` on an unrendered `"{metadata}"` template.

## The numbers

| Number | Meaning |
|---|---|
| **4,000** | All real: 3,697 called (within 2.5 min) + 303 pushed from 16:58, too late to call. |
| **2,474** | **Marta is right**: 2,344 by the bot + 130 by the resend; 70 show an older attempt. |
| **1,526** | 1,223 called, status lost + 303 not called. To redeliver: 1,286 (1,223 + 63 attempt 2). |
| **5,121** | Internal writes (2,548 "call started" + 2,573 results), not statuses to Kalkulio. |
| **1,611** | Failed deliveries: 140 resent + 1,223 + 63 + 185 attempt 1 already superseded. |

## Fixes, in order

| # | Fix | Owner |
|---|---|---|
| 1 | Redeliver the 1,286 (Wed 07:00 Warsaw); Kalkulio fixes the 7 by hand. | Engagement |
| 2 | Status step: payload from flow, retry, alert, daily count check. Then platform default. | Engagement |
| 3 | Batched pushes every 2 min until fix 5. | Client, with us |
| 4 | OOM and restart alerts; more memory now; check other accounts. | Core product |
| 5 | Atomic append (before any replicas); load test at 2x the peak. | Core product |
| 6 | Integration changes on live accounts go through engineering. | AM, Engagement |
| 7 | CRM shows the newest call, not the newest received. | Client |
| 8 | Fix the `{metadata}` template. | Engagement |
