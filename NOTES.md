# Notes

## Next steps with more time

- **Verify after sending.** The tool trusts the API's `accepted`. A second pass that GETs every sent lead and confirms `currentStatus` is what we sent would close the loop. Today the second `--apply` run does this indirectly (everything shows as already delivered).
- **Check all 4,000 leads against Kalkulio, not only the candidates.** The tool GETs the 1,286 candidates and the 7 overwritten leads. A full compare would also catch anything our logs do not show.
- **Find the real limit of `outbound-api`.** We know 222 requests/h is fine and ~880/h is not. A load test would turn the household answer from "conditions" into a number.
- **Ask Kalkulio why 09:00 to 11:00 had 4x the normal volume** (campaign, mailing, a bug on their side?). If it repeats, it matters for capacity planning.
- **"socket hang up" in their dashboard.** Our failed statuses were never sent, so Kalkulio could not have seen errors from them. Most likely their lead pushes to us failed during the pod restarts and their side retried, since every lead pushed before 16:58 was called. Their integration logs would confirm it.
- **Design the status step properly:** payload from flow data, an outbox with retries, ordering by call time so an old status never overwrites a newer one, and a daily count check against `GET /callbot/leads`.
- **The `{metadata}` bug:** find out what the flow meant to send there and whether the 97 callbacks lost anything Kalkulio's agents need.
- Logs from the days before 29 Sep, to confirm the restart history and that the 25 Sep switch ran clean until the volume spike.

## Assumptions

- The log export is complete for 29 Sep. Checked: every one of the 4,153 conversations has both its internal PATCH lines and exactly one status line.
- The payload in a "status delivery skipped" line is exactly what the bot would have sent. The mock preloads from the same lines, and the PATCH result matches the status in all 4,153 calls.
- The mock state equals Kalkulio's CRM on 30 Sep 08:30.
- A status the bot sends late does not erase what Kalkulio's agents recorded themselves. Their API doc only talks about bot statuses. To confirm with them before the 07:00 run; the reply to Marta gives her the chance to say no.
- 07:00 Warsaw is 05:00 UTC, before dialing starts at 08:00 Warsaw, so no live status can arrive for these leads while the tool runs.
- Business numbers: README prices and rates; a month = 21 working days; the call center would need the same 4,153 attempts. The interested share of contacts (40.4 %) is from the 29 Sep data.
- No test or junk leads: all 4,000 IDs are unique, sequential, `auto`, and known to both sides. Phone numbers are masked to 3 digits, so they cannot be used to find duplicates.

## How I used AI tools

This was built with Claude Code agents, with me reviewing. *(Jakub: review and personalize this section before sending.)*

- **Delegated:** log parsing and the reconciliation script, the redelivery tool and its tests, first drafts of all documents.
- **Checked myself:** every number in the documents against `out/reconcile.txt` and the tool runs in `out/`; the tool against the mock (dry run, apply, second apply); the end to end test against the real mock with 20 % injected 503s and the rate limit; the logic of what not to send.
- **Where the tool got it wrong.** The first per lead table sorted each lead's calls by call time. In that view the 13:00 manual resend looked harmless: attempt 1 resent, attempt 2 delivered. Sorting by delivery time showed the opposite. For 7 leads the resend landed two hours after attempt 2 and, since Kalkulio's CRM shows the most recently received status, replaced it. Two of those leads are "interested" and now show NO_ANSWER. That is also why the tool never sends an older attempt.
- **Smaller ones:** the first capacity estimate spread the extra 2,500 household leads evenly over 11 dialing hours (+227/h), which ignores the peaks that actually killed the pod; replaced by scaling the observed hourly profile. An early check flagged 100 "duplicate" phone numbers, which turned out to be collisions of 3 digit masks, not duplicates.
