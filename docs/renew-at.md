# renew_at, renewal, and the track-record score

Code: [`app/renewal.py`](../app/renewal.py),
[`app/track_record_score.py`](../app/track_record_score.py). Tests:
[`tests/test_renew_at.py`](../tests/test_renew_at.py). Background: ops G1 in the
growth strategy of 2026-10-16.

## The problem this answers

A credential runs 365 days ([`app/credentials.py`](../app/credentials.py)).
Nothing an agent ever received from us said so, and nothing said that renewing
it costs nothing. Of 525 agents in the registry on 2026-10-16, 241 made their
one independent call on the day they registered and were never seen again.

Three parts, in the order a caller meets them.

## 1 — `renew_at` in every answer

Every 200 and every 402 from a path under `/credentials/`,
`/identity/verify/` or `/vc/` carries a `renew_at` field. It is an ISO-8601
instant in UTC with a trailing `Z`, or `null`.

| Where | Shape |
|---|---|
| 200 | `"renew_at": "2027-09-15T08:12:00Z"` at the top level |
| 402 | `"moltrustGate": {"reason": "insufficient_credits", "renew_at": "…"}` |

`moltrustGate` is the same block name the gate in `moltguard` uses for what it
decided, so a caller that parses one parses the other.

**The date.** The earliest `expires_at` among the subject's credentials that are
not revoked, minus 30 days (`RENEW_LEAD_DAYS`). A credential runs a year, so a
month of lead is long enough for an agent that calls monthly to see the date
before it matters.

**The subject.** `GET /identity/verify/<did>` answers a question about the DID in
the path, so the date is that DID's. Every other path answers the caller, and
the date is the caller's — which means an unauthenticated caller gets `null`.

**Null is written out, not omitted.** A subject holding no credential has no
renewal date, and a parser cannot tell an omitted field from a forgotten one.

**A date in the past is published as it is.** An agent whose credential is
already due is the case the field exists for.

### What stays untouched

Status codes other than 200 and 402; bodies that are not JSON objects (a JSON
array has no place to put a field without changing what it is); bodies over
256 KB; paths outside the three prefixes. `/credentialsomething` is not
`/credentials/` and gets nothing.

## 2 — `POST /credentials/renew`

Costs 0 credits, listed as such in
[`app/credits.py`](../app/credits.py) rather than left to the default for
unpriced endpoints.

```
POST /credentials/renew
x-api-key: <the key that owns the DID>

{"did": "did:moltrust:0123456789abcdef", "credential_type": "TrustScoreCredential"}
```

`credential_type` is optional; omitted, the credential that falls due first is
renewed. The endpoint answers one of three ways:

- **Renewed.** `{"renewed": true, "credential": {…}, "renewed_from": "<id>", …}`.
  The new credential carries the claims of the old one plus `renewed_from`, and
  runs a fresh 365 days. Anchoring happens in the next batch, every two hours.
- **Nothing due.** `{"renewed": false, "reason": "nothing_due", …}`, when no
  credential of this DID falls inside the 30-day lead.
- **Refused for a track record.** `TrackRecordCredential` states what a wallet
  had done when it was measured. Renewing would put today's issuance date on an
  old reading, so the endpoint answers 400 `renew_not_applicable` and names
  `POST /credentials/track-record`, which measures again.

### Why it counts

The request logger writes a `request_log` row for every call, with the caller's
DID and the path. `/credentials/renew` is not in the `scripted(pats)` list of
[`app/sql/registry_export.sql`](../app/sql/registry_export.sql) — the endpoints
a bounty task text prescribed — so a renewal is an independent call and the
agent counts towards `activated_counted` in the registry proof. That is the
whole mechanism behind making it free: coming back has to be worth something to
the agent and has to be visible to us.

## 3 — The track-record score

`GET /identity/verify/<did>` carries a `track_record_score` object, or `null`
for a DID with no anchored credential. Null rather than zero: no record is not a
record that decayed.

```json
"track_record_score": {
  "score": 6.3,
  "max": 20.0,
  "first_anchor_at": "2026-05-02T09:14:31+00:00",
  "days_since_first_anchor": 167.2,
  "days_since_independent_call": 41.0,
  "independent_call_seen": true,
  "request_log_retention_days": 30,
  "curve": { "tenure_saturation_days": 365, "tenure_max_points": 20.0,
             "dormant_after_days": 30, "dormant_zero_days": 120,
             "formula": "…" }
}
```

Two curves, four constants, both published with every answer so a relying party
recomputes the figure instead of trusting it.

**Tenure grows.** Linear from 0 to 20 points over 365 days from the *first
anchoring transaction* — not from the registration row. A registration is a line
in a database; an anchor is a transaction on Base that was paid for and cannot
be backdated. Linear, because `days / 365 × 20` is arithmetic a reader does in
their head.

**Dormancy cuts.** A factor of 1.0 for the first 30 days since the last
independent call, then linear to 0.0 at 120 days. Thirty days is the window the
growth strategy measures returns in; ninety days of fall after that puts an
agent that stops at zero after a quarter.

`score = tenure_points × freshness_factor`, so 0 to 20.

### The limit of the figure, stated

`request_log` keeps 30 days. Where it holds no independent call for a DID,
dormancy is measured from the first anchor instead — the longest it could be —
and `independent_call_seen` is `false`. The score is then a lower bound. An
agent that called independently 45 days ago reads as fully dormant, because the
row that would say otherwise has been dropped.

## Probes

`tests/test_renew_at.py` tests each rule twice: the case that makes it fire and
the neighbouring case that has to stay quiet. The prefix that must not match,
the credential endpoints that keep their price, day 30 with no decay, the
saturation point that does not grow further, the body that already names the
date.
