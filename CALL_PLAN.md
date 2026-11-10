# Call with Kalkulio, Wed 1 Oct, 10:00 Warsaw (45 min)

**Us:** me (Delta), Account Lead. Account manager joins for the commercial part.
**Them:** Marta, ideally someone from their integration team.
**Before the call:** file with the 1,286 results and the 7 manual fixes sent on Tuesday; Marta's answer on whether a late bot status replaces what her agents recorded; redelivery done at 07:00 (or only the file, if she said so) and the report checked; credit amount and the non-cash fallback approved by the Account Lead; other accounts on `outbound-api` checked by core product.

## What I want out of it

1. Marta agrees on what happened and which numbers are right, and leaves with something she can tell her CEO.
2. Their integration team agrees to batch pushes every 2 minutes until the core fix is live.
3. The credit note settled on our calculation, not on 275k.
4. Household: a launch date with conditions, not a yes or no.
5. Selling on the call parked into a separate scoping conversation, without killing it.

## Agenda

| Time | Topic | What I say or show |
|---|---|---|
| 0 to 5 | Apology, no excuses | We broke it, and our manual fix made 7 leads worse. The real time switch was agreed with our account manager; checking it was our job, so no blame on their side. |
| 5 to 15 | What happened, the numbers | One table: 4,000 = 3,697 called + 303 after hours. 2,474 is right. 5,121 was internal writes. Calls kept going within 2.5 min. |
| 15 to 20 | Data fixed | Morning redelivery report: what was sent, what we held back and why (185 older attempts that would overwrite newer ones; the 303 after-hours leads were called on 30 Sep, confirm their statuses arrived). Ask them to confirm counts in their CRM. |
| 20 to 25 | Stop it happening again | Ours, by Friday: retry and alert on status delivery, daily count check; until then an hourly check and resend while they call. Core product: memory alert and the real fix. Ask: batched push every 2 min from now (requested in the reply). Ask: integration changes go through our engineering first. Ask: CRM shows the newest call, not the newest received. |
| 25 to 32 | Credit note | 275k vs 5,000 to 6,600 worst case, line by line, and say the table leaves out her team's afternoon. We waive the 29 Sep attempt fees, 9,967.20 PLN. If pushed: first household week fee-free, capped at 500 leads/day (~6,700 PLN). |
| 32 to 40 | Household Monday | Yes, if batched pushes and our status retry with alert are live by Friday. Otherwise start at 500 per day and ramp up. |
| 40 to 45 | Selling on the call, next steps | Quote on the call, then a payment link by SMS or a warm transfer to a licensed agent; their compliance owns IDD and distance selling duties. Separate scoping session. Recap owners and dates in writing the same day. |

## What I will not agree to

- **Taking card details on the call.** Card numbers in audio, transcripts and logs put the whole voice stack into PCI DSS scope. Not something to say yes to on a sales call, and not on our side alone.
- **275k or any credit based on "every lead is a lost policy".** I can move on goodwill (the non-cash fallback), not on that formula.
- **Household at full volume on Monday with no conditions.** 1.6x the volume of the day that failed, and nothing between 222 and ~890 req/h is tested.
- **Dates for core product work** (the `outbound-api` fix). I will bring their date when I have it.
- **Rewriting the 7 overwritten statuses by sending fake timestamps** to get around their duplicate check. They fix those 7 by hand.

## If it goes badly

If Marta says she is moving the work back to the call center: ask for two weeks with the three contract metrics reported daily, then decide on data. A day at the call center costs her about 29,500 PLN more for the same attempts.
