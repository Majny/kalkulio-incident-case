# Notes

## Next steps with more time

- **Verify after sending.** The tool trusts the API's `accepted`. A pass that GETs every sent lead and confirms `currentStatus` is what we sent would close the loop. Today the second `--apply` run does this indirectly (everything shows as already delivered).
- **Check all 4,000 leads against Kalkulio, not only the candidates.** A full compare would also catch anything our logs do not show.
- **Find the real limit of `outbound-api`.** 222 requests/h is fine and ~880/h is not. A load test would turn the household answer from "conditions" into a number.
- **Check the 309 interested and 95 callbacks with an LLM over the transcripts**, verified by hand on a sample, before Kalkulio's team works through them. The bot's classification is the one part of this data I took on trust.
- **Ask Kalkulio why 09:00 to 11:00 had 4x the normal volume** (campaign, mailing, a bug on their side?). If it repeats, it matters for capacity planning.
- **Confirm "socket hang up"** from Kalkulio's integration logs (DIAGNOSIS has the hypothesis).
- **Design the status step properly:** payload from flow data, an outbox with retries, only ever the newest attempt per lead, and a daily count check against `GET /callbot/leads`. Every client's status step has the same no retry design, so this belongs in the platform, with this incident as the case for it.
- **Rebuild our campaign store for 29 Sep** from the logs (1,580 missing final results) before anything reads it for reporting or billing.
- **The `{metadata}` bug:** find out what the flow meant to send and whether the 97 callbacks lost anything Kalkulio's agents need.
- Logs from the days before 29 Sep, to see the two restarts before 08:55 and that the 25 Sep switch ran clean until the spike.

## Assumptions

- The log export is complete for 29 Sep. Checked: every one of the 4,153 conversations has both its internal PATCH lines and exactly one status line.
- The payload in a "status delivery skipped" line is exactly what the bot would have sent. The mock preloads from the same lines, and the PATCH result matches the status in all 4,153 calls.
- The mock state equals Kalkulio's CRM on 30 Sep 08:30.
- A late bot status does not erase what Kalkulio's agents recorded themselves. Their API doc only talks about bot statuses, so the reply to Marta asks this before the 07:00 run; if the answer is yes, they get only the file.
- 07:00 Warsaw is 05:00 UTC, before dialing starts at 08:00 Warsaw, so no live status can arrive for these leads while the tool runs.
- Business numbers: README prices and rates; a month = 21 working days; the call center would need the same 4,153 attempts. The interested share of contacts (40.4 %) is from the 29 Sep data.
- No test or junk leads: all 4,000 IDs are unique, sequential, `auto`, and known to both sides. Phone numbers are masked to 3 digits, so they cannot be used to find duplicates.

## How I used AI tools

I worked in Claude Code: agents wrote most of the code and the first drafts, I set the direction, decided what not to send and checked the results.

Time spent: about 5 hours over three days (8 to 10 Nov), including the final review.

- **Delegated:** log parsing and the reconciliation script, the redelivery tool and its tests, first drafts of all documents, and a final review in which separate agents recomputed every number from the raw data without our code, reviewed the tool and played the hiring panel and Marta.
- **Checked myself:** every number in the documents against `out/reconcile.txt` and the tool runs in `out/`; the tool against the mock (dry run, canary, apply, second apply); the logic of what not to send.
- **On purpose, no LLM in the pipeline.** Reconciliation and redelivery must be exact and repeatable, so they are plain parsing and code. LLMs wrote and reviewed the code; they never decide what gets sent.
- **Where the tool got it wrong.** The first per lead table sorted each lead's calls by call time. In that view the 13:00 manual resend looked harmless: attempt 1 resent, attempt 2 delivered. Sorting by delivery time showed the opposite. For 7 leads the resend landed two hours after attempt 2 and, since Kalkulio's CRM shows the most recently received status, replaced it. Two of those leads are "interested" and now show NO_ANSWER. That is also why the tool never sends an older attempt.
- **And in its own code.** The first version's preflight check treated a 404 as "lead unknown", so a wrong base URL ended with "send: 0", exit 0: a colleague at 07:00 would read that as nothing to do. The review caught it; the tool now stops, and a test covers it.
- **Smaller ones:** the first capacity estimate spread the extra 2,500 household leads evenly over 11 dialing hours (+227/h), which ignores the peaks that actually killed the pod; replaced by scaling the observed hourly profile. An early check flagged 100 "duplicate" phone numbers, which turned out to be collisions of 3 digit masks, not duplicates.
