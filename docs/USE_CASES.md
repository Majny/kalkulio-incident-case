# Use cases and user stories

Diagrams are in [`diagrams/`](diagrams/): PlantUML sources (`.puml`) with rendered `.svg`.

| Diagram | Shows |
|---|---|
| [01 Incident architecture](diagrams/01_incident_architecture.svg) | Where 29 Sep broke: outage in core product, data loss in our status step |
| [02 Incident sequence](diagrams/02_incident_sequence.svg) | One lead during the outage, step by step |
| [03 Target architecture](diagrams/03_target_architecture.svg) | The same flow after fixes 2 to 5 and 7 |

## Actors

- **On-call engineer.** Runs the tool at 07:00 Warsaw. Knows the platform, not this incident.
- **Kalkulio ops (Marta).** Works the leads in the CRM. Needs the missing results and the leads to fix by hand.
- **Account Lead / account manager.** Owns the client relationship, the credit note and the new asks.
- **Kalkulio status API.** External system: `POST /callbot/calls`, `GET /callbot/leads/{id}`.

## User stories

Each story lists acceptance criteria and where they are checked.

**US1 Reconcile the numbers.** As the account manager, I want each disputed number explained from the logs, so that I can tell Kalkulio's CEO which figure is right.
- One command prints every number used in the documents, from the logs alone. *Check:* `python3 tools/reconcile.py` = `out/reconcile.txt`.
- 4,000, 2,474 and 5,121 each get a meaning; the 1,611 failed deliveries add up. *Check:* `out/reconcile.txt`, DIAGNOSIS.md.

**US2 Preview before sending.** As the on-call engineer, I want a dry run by default, so that I see what would be sent before anything reaches the client.
- Without `--apply` nothing is POSTed. *Test:* `test_dry_run_canary_apply_rerun`.
- Every lead the tool sends or holds back (1,781) appears in the plan CSV with a reason. *Check:* `out/redeliver_plan_dry_run.csv`.

**US3 Canary.** As the on-call engineer, I want to send a small batch first, so that a mistake hits 50 leads, not 1,286.
- `--limit N` sends exactly N, highest value first (interested, then callback). *Tests:* `test_dry_run_canary_apply_rerun`, `test_plan_rules`.
- The rest show as `held_back_by_limit`. *Test:* `test_dry_run_canary_apply_rerun`.

**US4 Redeliver only what is missing.** As Kalkulio ops, I want only the newest missing status per lead, so that no newer result in my CRM is overwritten.
- Older attempts are never sent; a lead whose newer call Kalkulio already holds is skipped. *Tests:* `test_plan_rules`, `test_decide`.
- After apply, every lead shows its newest call, except the 7 manual fixes. *Test:* `test_dry_run_canary_apply_rerun`.

**US5 Safe to run twice.** As the on-call engineer, I want a rerun to send nothing new, so that I can rerun after any failure without thinking.
- Second apply sends 0; Kalkulio counts no new accepted or duplicate items. *Test:* `test_dry_run_canary_apply_rerun`.
- Two runs at once are blocked by an OS lock. *Test:* `test_second_run_is_blocked_while_first_holds_the_lock`.

**US6 Fail loudly, not quietly.** As the on-call engineer, I want a wrong URL or token to stop the run, so that I never read "nothing to send" when the tool could not see Kalkulio.
- Preflight failure exits 1 with "preflight". *Test:* `test_wrong_base_url_stops_instead_of_reporting_nothing_to_do`.
- 429, 5xx and network errors are retried; other 4xx stop the run with a "fix the cause" message. *Tests:* `test_send_tells_refused_from_transient`, `test_retry_after_parsing`, `test_dry_run_canary_apply_rerun` (20 % of POSTs return 503).
- Exit codes: 0 done, 1 stopped, 2 rejected items.

**US7 A file for the client.** As Kalkulio ops, I want the missing results as a file today, so that my team can call interested leads before the CRM is updated.
- `out/for_kalkulio.csv`: 1,286 missing results and 7 manual fixes, in the API's field names. *Test:* `test_dry_run_canary_apply_rerun`.
- Built from the logs, so later runs and `--limit` do not change it. *Test:* same, last step.

**US8 Fix what the resend broke.** As Kalkulio ops, I want to know which leads show an older status, so that I can correct them by hand.
- The 7 leads are listed with the status they should show and the call time. *Check:* `out/run1_dry_run.txt`, `out/for_kalkulio.csv`.
- The tool never fakes `callInitDatetime` to get past the duplicate check (README).

**US9 Decide the commercial answer.** As the Account Lead, I want real exposure, a credit proposal and conditions for the new asks, so that the call ends with an agreement, not with 275k.
- Exposure, credit, household and selling answers come from the same numbers. *Check:* BUSINESS.md, CALL_PLAN.md.

## Out of scope

- Fixing `outbound-api` itself (core product; fix 5).
- Changing Kalkulio's CRM logic (client; fix 7).
- Re-checking the bot's call classification against transcripts (NOTES.md, next steps).
