# Kalkulio, 29 Sep incident: work sample

| File | What |
|---|---|
| [DIAGNOSIS.md](DIAGNOSIS.md) | What happened, the numbers reconciled, fixes with owners |
| [REPLY_TO_MARTA.md](REPLY_TO_MARTA.md) | Reply, ready to send |
| [CALL_PLAN.md](CALL_PLAN.md) | Plan for the 10:00 call |
| [BUSINESS.md](BUSINESS.md) | Account value, real exposure, credit note, new asks, contract metrics |
| [NOTES.md](NOTES.md) | Next steps, assumptions, how AI tools were used |
| `tools/reconcile.py` | Prints every number used in the documents (`out/reconcile.txt`) |
| `tools/redeliver.py` | The redelivery tool |
| `tests/` | Plan rules + end to end run against the real mock |
| `case/` | Copy of the case data, API doc and mock, unchanged |
| `out/` | Outputs of the runs below |

Python 3.10+, stdlib only.

## Redelivery tool

```bash
python3 case/mock_api/kalkulio_mock.py &        # mock on 127.0.0.1:8099
export KALKULIO_TOKEN=local-test-token

python3 tools/redeliver.py                      # 1. dry run: reads Kalkulio, sends nothing
python3 tools/redeliver.py --apply --limit 50   # 2. optional canary
python3 tools/redeliver.py --apply              # 3. send the rest
python3 tools/redeliver.py --apply              # 4. rerun: sends 0
```

**What it sends.** For each lead, the newest call status in our logs, if that status never reached Kalkulio. Before sending, it GETs the lead from Kalkulio and skips it if Kalkulio already holds that call or something newer.

**What it does not send, and why**

| Reason | Count | Why |
|---|---|---|
| `not_called_yet` | 303 | Pushed after the last dialing slot. Called on 30 Sep, the status flows normally. |
| `superseded_by_newer_attempt` | 185 | Attempt 1 was lost but attempt 2 arrived. Kalkulio shows the status received last, so sending attempt 1 now would overwrite the newer one. |
| `crm_shows_older_status_needs_manual_fix` | 7 | The 13:00 manual resend put attempt 1 on top of attempt 2. Resending attempt 2 returns `duplicate` and changes nothing. Kalkulio fixes these by hand. We do not fake timestamps to get past their duplicate check. |
| `already_delivered` / `kalkulio_has_newer_status` | 0 on first run | Safety checks against Kalkulio's live state. On a rerun everything lands in `already_delivered`. |

**Safety.** Dry run unless `--apply`. Token only from the environment. Lock file against two runs at once. Max 50 items per request and 4 requests per second (the API allows 5). `429` waits `Retry-After`, `503` and network errors back off exponentially, up to 6 retries, then the run stops and can be started again. Retrying is safe because Kalkulio dedupes on `assignmentId` + `callInitDatetime`. Interested and callback leads go first. Each run writes a plan CSV and appends every request and result to `out/redeliver_log.jsonl`. `--limit N` sends only N, for a canary.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The end to end test starts the real mock in process with 20 % of POSTs answered `503`, runs dry run, apply and apply again faster than the rate limit allows, and checks that the dry run sends nothing, every lead ends with its newest status except the 7 manual fixes, nothing is rejected, and the rerun sends nothing.

## Output of the runs against the mock

The mock was restarted fresh before run 1 (2,474 leads with a status). Every request and per item result is in `out/redeliver_log.jsonl`; the plan of the last apply run overwrites `out/redeliver_plan_apply.csv`. The dry run plan `out/redeliver_plan_dry_run.csv` is the file for Kalkulio (all 1,286 results plus the 7 manual fixes).

**1. Dry run** (`out/run1_dry_run.txt`)

```
$ python3 tools/redeliver.py 
redeliver 20261108T132557Z-e8f9af  mode: DRY RUN (nothing will be sent)  api: http://127.0.0.1:8099
logs: 4000 leads, 3697 called, 1286 with newest status missing per logs
  checked 250/1286 leads in Kalkulio
  checked 500/1286 leads in Kalkulio
  checked 750/1286 leads in Kalkulio
  checked 1000/1286 leads in Kalkulio
  checked 1250/1286 leads in Kalkulio

PLAN
  send: 1286  by status: {'ANSWERED_NOT_INTERESTED': 419, 'ANSWERED_INTERESTED': 309, 'NO_ANSWER': 195, 'VOICEMAIL': 169, 'CALLBACK_REQUESTED': 95, 'BUSY': 53, 'WRONG_NUMBER': 46}
  not sending:
      303  not_called_yet
      185  superseded_by_newer_attempt
        7  crm_shows_older_status_needs_manual_fix
  ask Kalkulio to set these by hand (a resend would only be a 'duplicate'):
    A-100679: should be ANSWERED_INTERESTED (call 2026-09-29T11:04:53.252Z)
    A-100695: should be NO_ANSWER (call 2026-09-29T11:10:39.467Z)
    A-100710: should be NO_ANSWER (call 2026-09-29T11:05:37.441Z)
    A-100746: should be CALLBACK_REQUESTED (call 2026-09-29T11:15:03.258Z)
    A-100805: should be ANSWERED_NOT_INTERESTED (call 2026-09-29T11:16:54.394Z)
    A-100811: should be CALLBACK_REQUESTED (call 2026-09-29T11:02:26.163Z)
    A-100820: should be ANSWERED_INTERESTED (call 2026-09-29T11:05:06.479Z)

Dry run. Rerun with --apply to send.
plan: /Users/majny/Desktop/MyProjects/interview-tasks/born-digital/solution/out/redeliver_plan_dry_run.csv
log:  /Users/majny/Desktop/MyProjects/interview-tasks/born-digital/solution/out/redeliver_log.jsonl
(exit 0)
```

**2. Apply** (`out/run2_apply.txt`)

```
$ python3 tools/redeliver.py --apply
redeliver 20261108T133125Z-252865  mode: APPLY  api: http://127.0.0.1:8099
logs: 4000 leads, 3697 called, 1286 with newest status missing per logs
  checked 250/1286 leads in Kalkulio
  checked 500/1286 leads in Kalkulio
  checked 750/1286 leads in Kalkulio
  checked 1000/1286 leads in Kalkulio
  checked 1250/1286 leads in Kalkulio
  sent 50/1286
  sent 100/1286
  sent 150/1286
  sent 200/1286
  sent 250/1286
  sent 300/1286
  sent 350/1286
  sent 400/1286
  sent 450/1286
  sent 500/1286
  sent 550/1286
  sent 600/1286
  sent 650/1286
  sent 700/1286
  sent 750/1286
  sent 800/1286
  sent 850/1286
  sent 900/1286
  sent 950/1286
  sent 1000/1286
  sent 1050/1286
  sent 1100/1286
  sent 1150/1286
  sent 1200/1286
  sent 1250/1286
  sent 1286/1286

PLAN
  send: 1286  by status: {'ANSWERED_NOT_INTERESTED': 419, 'ANSWERED_INTERESTED': 309, 'NO_ANSWER': 195, 'VOICEMAIL': 169, 'CALLBACK_REQUESTED': 95, 'BUSY': 53, 'WRONG_NUMBER': 46}
  not sending:
      303  not_called_yet
      185  superseded_by_newer_attempt
        7  crm_shows_older_status_needs_manual_fix
  ask Kalkulio to set these by hand (a resend would only be a 'duplicate'):
    A-100679: should be ANSWERED_INTERESTED (call 2026-09-29T11:04:53.252Z)
    A-100695: should be NO_ANSWER (call 2026-09-29T11:10:39.467Z)
    A-100710: should be NO_ANSWER (call 2026-09-29T11:05:37.441Z)
    A-100746: should be CALLBACK_REQUESTED (call 2026-09-29T11:15:03.258Z)
    A-100805: should be ANSWERED_NOT_INTERESTED (call 2026-09-29T11:16:54.394Z)
    A-100811: should be CALLBACK_REQUESTED (call 2026-09-29T11:02:26.163Z)
    A-100820: should be ANSWERED_INTERESTED (call 2026-09-29T11:05:06.479Z)

RESULT  {'accepted': 1286}   retries: none
plan: /Users/majny/Desktop/MyProjects/interview-tasks/born-digital/solution/out/redeliver_plan_apply.csv
log:  /Users/majny/Desktop/MyProjects/interview-tasks/born-digital/solution/out/redeliver_log.jsonl
(exit 0)
```

**3. Apply again: nothing re-sent** (`out/run3_apply_again.txt`)

```
$ python3 tools/redeliver.py --apply
redeliver 20261108T133701Z-66ae25  mode: APPLY  api: http://127.0.0.1:8099
logs: 4000 leads, 3697 called, 1286 with newest status missing per logs
  checked 250/1286 leads in Kalkulio
  checked 500/1286 leads in Kalkulio
  checked 750/1286 leads in Kalkulio
  checked 1000/1286 leads in Kalkulio
  checked 1250/1286 leads in Kalkulio

PLAN
  send: 0  by status: {}
  not sending:
     1286  already_delivered
      303  not_called_yet
      185  superseded_by_newer_attempt
        7  crm_shows_older_status_needs_manual_fix
  ask Kalkulio to set these by hand (a resend would only be a 'duplicate'):
    A-100679: should be ANSWERED_INTERESTED (call 2026-09-29T11:04:53.252Z)
    A-100695: should be NO_ANSWER (call 2026-09-29T11:10:39.467Z)
    A-100710: should be NO_ANSWER (call 2026-09-29T11:05:37.441Z)
    A-100746: should be CALLBACK_REQUESTED (call 2026-09-29T11:15:03.258Z)
    A-100805: should be ANSWERED_NOT_INTERESTED (call 2026-09-29T11:16:54.394Z)
    A-100811: should be CALLBACK_REQUESTED (call 2026-09-29T11:02:26.163Z)
    A-100820: should be ANSWERED_INTERESTED (call 2026-09-29T11:05:06.479Z)

RESULT  nothing sent   retries: none
plan: /Users/majny/Desktop/MyProjects/interview-tasks/born-digital/solution/out/redeliver_plan_apply.csv
log:  /Users/majny/Desktop/MyProjects/interview-tasks/born-digital/solution/out/redeliver_log.jsonl
(exit 0)
```
