# Funnel test — five ways in, measured from outside

Binding definition, 2026-09-19. It did not exist in any repo before this file;
earlier runs referenced a spec that was only ever in a chat.

## Why it runs from outside

Every class is executed **entirely from outside the production infrastructure** —
a GitHub Actions runner or a freshly cloned Hetzner box. Not from
`moltstack@api.moltrust.ch`, and not from a workstation that has already
authenticated to something.

A funnel measured from inside measures the wrong thing: the DNS is warm, a key
is already in the environment, a package is already installed. The question is
whether a stranger's agent gets to a 200 without a human, so the run has to start
where a stranger starts.

## The five classes

### K5 — Dev / SDK

PyPI `moltrust` → signup by email → 100 credits → `POST /credentials/issue`
(the first one is free).

### K2 — MCP client

Smithery listing → `tools/list` → `identity.register` → score → `mt_skill_audit`.

### K3 — A2A agent

`a2aregistry` agent card → `/a2a` JSON-RPC → register → credential.

### K4 — On-chain

ERC-8004 registration file → `POST /auth/signup-did` (wallet signature) → score →
402 → EIP-3009 via CDP → VC.

### K1 — OpenClaw

`openclaw skills install moltrust-vet` → first vet call → silent DID creation
(`platform='clawhub'`).

## What is measured, per class

| Measure | Note |
|---|---|
| Discovery | reachable yes/no, and seconds to find the entry point |
| Signup without a human | yes/no — any manual step fails the class |
| Time to first 200 | from cold start of the class |
| Credits afterwards | balance once the class is done |
| 402 → payment → 200 | K4 only |
| VC issued | yes/no |
| Anchoring tx | hash, or why there is none |
| Errors | with the log line, not a summary of it |

## Pass condition

All five classes complete **without manual intervention**. One break is a failed
run, not a partial success.

A break is followed by: an issue, a fix PR, and a fresh run of that class. The
report records the break even after the fix — a funnel that needed two attempts
did not pass on the first.

## Test agents

Every agent created by a run registers with `platform='test'` and is **revoked
once the run has finished** — not while it is still going. Revoking mid-run took
agents out from under a run that was still using them and put failures in the
record that were not the funnel's (2026-09-19, run 4). Revoked, never deleted: `agents` has no delete path, and
the counting rules in [`agent-counting.md`](agent-counting.md) treat
`revoked_at IS NOT NULL` as off the books.

K1 is the exception that proves the rule — its whole point is that the DID is
created silently with `platform='clawhub'`. That agent is revoked too, and the
report says so, because leaving it would inflate the very number this test is
meant to make trustworthy.

## Money

K4 spends from the test wallet carved out of the wallet/keys gate in
[`../CLAUDE.md`](../CLAUDE.md): `0xd8f5bB747f7459BF3e1cc1aD041E2cA57B946C38`,
capped at 11 USDC and 0.001 ETH cumulative across all runs. **Every transaction
goes into the report with its hash.** A transaction without a hash in the report
is a breach of that exception even when it succeeded.

No other address may be used, including for tests.

## Report

`~/Downloads/funnel-report.md` — a class × step table, the cost, and the breaks.
Breaks are listed even when fixed in the same session.

## Result of record: run 6, 2026-09-19

`passed: true` — [run 35475251285](https://github.com/MoltyCel/moltrust-api/actions/runs/35475251285),
`ubuntu-latest`. Four classes ran; K4 did not (see the money section).

| Class | Discovery | Signup without a human | First 200 | Credits after | VC | Anchoring tx |
|---|---|---|---|---|---|---|
| K5 Dev/SDK | 0.08 s | yes, email | 1.96 s | 100 | yes | none |
| K2 MCP | 0.37 s | yes, keyless | 2.78 s | 0 | n/a | none |
| K3 A2A | 0.73 s | yes, keyless | 1.16 s | 0 | yes | none |
| K1 OpenClaw | 2.03 s | no signup at all | 7.85 s | n/a | n/a | none |

Six runs were needed. Four breaks were the harness, two were the product: the
/24 registration cap, and the gap after `register-pop` where a DID has no key
and the paid endpoints answer as though it were out of credits. Both are
addressed in #361.

The anchoring column is empty for every class because issued credentials are not
anchored at all — `/credentials/issue` has no anchor path, `credentials` has no
anchor column, and the only anchoring cron covers IPRs. That is a finding, not a
measurement gap.

Full write-up: `~/Downloads/funnel-report.md`.
