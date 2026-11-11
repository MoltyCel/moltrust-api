# 0008 — /a2a declares capabilities and executes no tasks

**Status:** Accepted (Lars, 2026-10-21, ops job A2A)

## Context

`https://api.moltrust.ch/a2a` has been public since #196 (30.06.2026) and listed
in the ARD manifest as `urn:air:api.moltrust.ch:a2a:moltrust-trust-registry` since
#798 (16.10.2026). Its executor answered every valid `SendMessage` with the same
success text. Live on 21.10.2026 (6c0ba18): "hello", a trust-score question and an
invented skill all came back as a result. The agent card listed 15 skills as if
A2A executed them. In the seven days before, 1272 POSTs from 34 user agents
reached the A2A paths, nearly all registry and reachability probes; none showed a
real task.

Two roles were open: execute tasks, or declare capabilities only. Either is
defensible. A fixed success answer to every task is not.

## Decision

Role B: the endpoint declares capabilities and executes no tasks.

- `SendMessage` and `SendStreamingMessage` are refused with
  `UnsupportedOperationError` (-32004). The message gives the reason and the way
  over REST; the error details carry the card and OpenAPI URLs. A malformed
  message still gets -32602 with the field at fault.
- The card says so in its description, and each skill names its REST route and
  whether it needs an X-API-Key. Every skill listed has a live route (checked
  21.10.2026). The sentence "Reference implementation of the Trust Registry
  Binding for A2A v1.0" is removed.
- Reads (`GetTask` and the like) stay with the SDK.
- `scripts/a2a_role_guard.py` checks both daily (`c-a2a-capabilities-only`):
  a valid task message is refused, and the served card verifies and says it.

Executing tasks over A2A (role A) is a separate job, taken up once a caller
actually uses it.

## Consequence

Probes that read the success answer as "working" now get an error. Some
registries may list the agent as not functional over A2A.
