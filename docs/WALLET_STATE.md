# WALLET_STATE.md — MolTrust Base wallets (anchoring)

Which key controls which Base address, and the one unresolved discrepancy.
Read-only verified 2026-07-08. **Never commit private keys.**

## Wallets

| Purpose | Address | Key var | Status |
|---|---|---|---|
| Publication anchoring (dedicated) | `0xd8f5bB747f7459BF3e1cc1aD041E2cA57B946C38` | `BASE_ANCHOR_KEY` | NEW 2026-07-08 — anchoring only |
| Revenue / MoltGuard-Receiver | `0x380238347e58435f40B4da1F1A045A271D5838F5` | `BASE_WRITE_KEY` | funded |
| ERC-8004 | `0x9068E25d8EA247a24Abf9Ff42BfA084931B3bA91` | (`BASE_WALLET_ADDRESS`) | flagged burned 18.06.26 |

`anchor_publication.py` now uses `BASE_ANCHOR_KEY` only; IPR anchoring
(`app/provenance/anchor.py`) keeps `BASE_WRITE_KEY`.

## UNRESOLVED discrepancy — separate clarification point (do not guess)

As of 2026-07-08, in `~/.moltrust_secrets`:
- `BASE_WRITE_KEY` and `BASE_WALLET_KEY` hold the **same** value, deriving to
  `0x3802…38F5` (MoltGuard-Receiver).
- BUT `BASE_WALLET_ADDRESS` = `0x9068…B3bA91` (ERC-8004), which does **not** match
  what `BASE_WALLET_KEY` derives to.

So `BASE_WALLET_KEY` does not control its own declared `BASE_WALLET_ADDRESS`.
Memory records `BASE_WALLET_KEY` (ERC-8004 wallet) as burned 18.06.26. Two
readings, both unverified: (a) `BASE_WALLET_KEY` was rotated to the 0x3802 key
post-burn and `BASE_WALLET_ADDRESS` is stale; (b) the two vars are conflated.

**Action (separate):** clarify + realign the `BASE_WALLET_*` vars and sweep/retire
the burned `0x9068` wallet if not already done. Left untouched here — 0x3802/0x9068
not modified. Publication anchoring was moved to a fresh dedicated key to avoid
this ambiguity entirely.
