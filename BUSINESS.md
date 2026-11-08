# Business view

README prices and rates; 29 Sep volumes from `tools/reconcile.py`. A month = 21 working days.

## Worth fighting for? Yes.

| Per day (29 Sep: 4,153 call attempts) | PLN |
|---|---|
| Our attempt fees, x 2.40 | 9,967 |
| Same attempts at the call center, x 9.50 | 39,454 |
| **Kalkulio saves** | **29,486** (~619k/month) |
| Extra margin from contact rate 61 % vs 48 %: 520 more contacts x 40.4 % interested (29 Sep) x 9 % x 180 | ~3,400 (~71k/month) |

We earn ~209k PLN/month in attempt fees plus the subscription. Kalkulio pays a quarter per attempt, reaches more people and gets called leads in 1.6 min instead of ~40. The call center's only argument right now is our outage. The economics survive one bad day, the relationship may not survive a second, so the fixes matter more than the credit.

## Real exposure vs the 275k claim

Marta's 1,526 x 180 = 274,680 PLN assumes every lead without a status was a sold policy. But 303 of them were never affected (after hours), margin only comes from interested leads, only 9 % of those buy, and the leads were not lost: Kalkulio had them and its team called them that afternoon.

| | Leads | x 16.20 PLN (9 % x 180) |
|---|---|---|
| Interested, status never arrived | 309 | |
| Interested, hidden by the manual overwrite | 2 | |
| **Upper bound, if all are lost** | **311** | **5,038 PLN** |
| Also counting 35 interested that arrived 2 to 4 h late | 346 | 5,605 PLN |

Worst case about 5,000 PLN, roughly 2 % of the claim. The real loss is lower, since most were still called by a person the same day.

## Credit note

Waive all attempt fees for 29 Sep: 4,153 x 2.40 = **9,967.20 PLN**. It covers the 1,611 attempts whose status never arrived (3,866.40 PLN) and is about twice the worst case margin loss. Simple to explain: you do not pay for the day we broke. We do not negotiate down from 275k; we show the calculation. Needs the Account Lead's sign off. Future incidents: service credits tied to metric 1 below.

## The two new asks

**Household next Monday.** +2,500 leads/day is 1.625x today: a normal hour goes from 222 to ~361 requests to `outbound-api`, a 29 Sep style peak from 892 to ~1,450. 222/h is fine, 880/h killed the pod 14 times, nothing in between is tested. Monday works if by Friday (1) Kalkulio pushes in batches every 1 to 2 min (max 30 requests/h per campaign), (2) status retry is live, (3) the OOM alert is live. Otherwise household starts at 500 leads/day and ramps up. No dates promised for the core product fix.

**Selling on the call.** Not agreed on this call. Card numbers spoken to a bot end up in audio, transcripts, logs and speech vendors, which pulls the whole voice stack into PCI DSS scope, and selling insurance by phone brings regulatory duties Kalkulio's compliance must own. Offer instead: the bot quotes the price from Kalkulio's quote API and sends an SMS payment link to their existing checkout, so card data never touches the call. A scoped, paid pilot after a few stable weeks, measured against their human callback.

## Three contract metrics

| Metric | Definition | Target (29 Sep) |
|---|---|---|
| **Status delivery** | Call attempts ending in the month for which Kalkulio's API returned `accepted` or `duplicate` within 15 min of call end (`callInitDatetime + callDuration`), divided by all call attempts ending in the month. From our delivery log, checked daily against Kalkulio's API. | 99.5 % in 15 min, 100 % in 24 h (61.2 %) |
| **Speed to first call** | Leads acknowledged by our API between 08:00 and 18:58 Warsaw on dialing days whose first attempt starts within 5 min, divided by all such leads. Leads outside those hours count as on time if called by 08:15 the next dialing day. | 98 % (100 %) |
| **Contact rate** | Leads with at least one ANSWERED_INTERESTED, ANSWERED_NOT_INTERESTED or CALLBACK_REQUESTED within 24 h of the push, divided by leads called, excluding leads that ended WRONG_NUMBER (number quality is Kalkulio's side). | 58 %, call center 48 % (64.9 %) |
