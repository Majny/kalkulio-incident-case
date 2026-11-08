# Kalkulio Call-Status API (v2)

*Provided by Kalkulio's integration team.*

Base URL (mock): `http://127.0.0.1:8099`
Auth: `Authorization: Bearer <token>`. For the mock, the token is
`local-test-token` (override with the `KALKULIO_TOKEN` env var when you start
the mock).

## POST /callbot/calls

Submit call statuses for leads.

```json
{
  "calls": [
    {
      "assignmentId": "A-100123",
      "callStatus": "ANSWERED_INTERESTED",
      "callDuration": 74,
      "callInitDatetime": "2026-09-29T09:41:12.004Z",
      "attempt": 1,
      "hlrErrorCode": null
    }
  ]
}
```

- **1–50 items per request.** A larger batch returns `413`.
- **Rate limit: 5 requests per second** per client. Over the limit returns
  `429` with a `Retry-After` header (in seconds).
- `503` means our CRM is temporarily unavailable. Retry later.
- Required fields: `assignmentId`, `callStatus`, `callDuration`,
  `callInitDatetime`.
- Response `200` returns one result per item:

```json
{ "results": [ { "assignmentId": "A-100123", "result": "accepted" } ] }
```

| result | meaning |
|---|---|
| `accepted` | stored |
| `duplicate` | we already have a status for this `assignmentId` + `callInitDatetime`; nothing changed |
| `rejected` | see `reason` (unknown assignmentId, missing field) |

**Important — how our CRM uses it:** the lead's *current status* in the CRM
is the status we **received most recently**. Sales agents work from the
current status. Every received status is also kept in the lead's history.

## GET /callbot/leads/{assignmentId}

Returns the lead's `currentStatus` and its `statusHistory` (in the order
received).

## Start the mock

```bash
python3 mock_api/kalkulio_mock.py
```

The mock has no persistence. Restart it to reset to the state of
30 Sep 08:30, i.e. everything Kalkulio actually received on 29 Sep.
