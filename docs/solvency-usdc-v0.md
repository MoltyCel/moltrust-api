# solvency_usdc_v0 — Recomputable On-Chain USDC Solvency

**Version:** 0.1.0
**Status:** minimal recomputable-solvency proof (one dimension only)
**Scope:** Path (C) — on-chain USDC deposits. Does **not** touch `wallet_score`
(0–20 attestation bonus) or the `/wallet/{address}` `shadow_score`. Those are
separate, server-side-derived signals and are out of scope here.

## What this is (and is not)

`solvency_usdc_v0` answers exactly one narrow, **independently verifiable**
question: *how much USDC has this DID provably sent on-chain to the MolTrust
deposit wallet on Base mainnet?*

- It is **recomputable**: every input is a public Base-mainnet transaction. A
  third party fetches the same `tx_hash`es from any Base RPC, decodes the USDC
  `Transfer` events, sums them, applies the published formula, and **must**
  arrive at the same number. No trust in MolTrust's database required.
- It is **not** a wallet-balance, wallet-age, or net-worth measure. It measures
  cumulative on-chain-verified USDC deposits to MolTrust only. This is a
  deliberately small, honest claim — chosen precisely because it is the part
  that can be reproduced from the chain alone.

## Formula

```
solvency_usdc_v0 = clamp(round(SUM(usdc_amount for each deposit
                                  with confirmations >= MIN_CONFIRMATIONS)),
                         0, CAP)
```

| Constant | Value | Meaning |
| :--- | :--- | :--- |
| `MIN_CONFIRMATIONS` | `5` | Min Base confirmations for a transfer to count (enforced at claim time and re-checkable on-chain). |
| `CAP` | `1_000_000` | USDC saturation ceiling. Documented constant; chosen high enough not to bind for normal accounts, so the value is effectively the rounded on-chain USDC sum. |

- `round()` is banker's-rounding-free standard half-up at integer precision
  (Python `round` on a float sum; for v0 the sum is small enough that this is
  unambiguous — amounts are USDC with 6 decimals).
- `clamp(x, 0, CAP) = max(0, min(CAP, x))`.

## Source of inputs

- **Network:** Base mainnet (Chain ID 8453).
- **Token:** USDC, contract `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` (6 decimals).
- **Deposit wallet (recipient):** `0x380238347e58435f40B4da1F1A045A271D5838F5`.
- **Transfer event topic:** `0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef`.

A "deposit" is a USDC `Transfer` event whose recipient is the deposit wallet,
confirmed at claim time via `app/usdc.py::verify_usdc_transfer` (which requires
≥ 5 confirmations) and recorded in the `usdc_deposits` table.

## Endpoint

```
GET /credits/solvency/{did}        (public, unauthenticated, read-only)
```

Returns the value **and** the raw inputs needed to reproduce it:

```jsonc
{
  "did": "did:moltrust:<...>",
  "version": "0.1.0",
  "solvency_usdc_v0": 6,            // clamp(round(sum), 0, CAP)
  "onchain_usdc_sum": 5.970416,     // raw sum, before round/clamp
  "cap": 1000000,
  "deposit_count": 1,
  "network": "Base mainnet (Chain ID 8453)",
  "deposit_wallet": "0x380238347e58435f40B4da1F1A045A271D5838F5",
  "token_contract": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
  "min_confirmations": 5,
  "inputs": [
    { "tx_hash": "0x...", "block_number": 1234567,
      "usdc_amount": 5.970416, "basescan_url": "https://basescan.org/tx/0x..." }
  ],
  "reproduce": { "formula": "...", "steps": [ ... ], "spec": "docs/solvency-usdc-v0.md",
                 "verify_script": "scripts/verify-solvency.py" }
}
```

The endpoint is intentionally **public** — third-party recomputability requires
read access to the inputs. (The older `/credits/deposits/{did}` is owner-API-key
gated and therefore unsuitable for independent verification.)

## How to reproduce the value yourself

1. `GET https://api.moltrust.ch/credits/solvency/{did}` → read `inputs[]`,
   `deposit_wallet`, `token_contract`, `min_confirmations`, `cap`,
   and the claimed `solvency_usdc_v0`.
2. For each `inputs[].tx_hash`, call `eth_getTransactionReceipt` on a Base RPC
   (e.g. `https://mainnet.base.org`).
3. In each receipt, keep `log`s where `address == token_contract` and
   `topics[0] == Transfer topic` and `topics[2]` (the `to` address, last 40 hex)
   `== deposit_wallet`. Amount = `int(log.data, 16) / 1e6`.
4. Require `currentBlock - receipt.blockNumber >= min_confirmations`.
5. Sum the amounts, apply `clamp(round(sum), 0, cap)`.
6. The result must equal the endpoint's `solvency_usdc_v0`.

A ready-to-run reference implementation (stdlib only, no MolTrust code) lives at
[`scripts/verify-solvency.py`](../scripts/verify-solvency.py):

```
python3 scripts/verify-solvency.py did:moltrust:<...>
```

## Versioning

This is `v0.1.0`. Any change to the formula, constants, or input semantics
bumps the version (and the `version` field in the endpoint response). Consumers
should pin the version they reproduce against.

## Honest limitations (v0)

- Measures only deposits **to MolTrust**, not the DID's wallet holdings.
- `CAP` is a chosen constant (1,000,000 USDC); adjust in a future version if a
  different ceiling is desired — it changes the published formula → version bump.
- As of this version the `usdc_deposits` table may be empty for all DIDs (no
  on-chain deposits recorded yet), in which case every DID returns
  `solvency_usdc_v0 = 0` with `inputs: []`. The endpoint and the recompute math
  are still correct; there is simply nothing to sum.
