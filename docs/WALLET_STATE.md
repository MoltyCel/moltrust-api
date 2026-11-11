# WALLET_STATE.md — MolTrust Base wallets (anchoring)

Which key controls which Base address. Rows updated 2026-10-12 after the wallet
rotation (live `balanceOf`, nonce and balance reads), the `0xd8f5` row on
2026-10-20; the sections below the
table keep their history. First live-verified 2026-08-04 on
`api.moltrust.ch` (`Account.from_key` against the running env; key material never
printed). **Never commit private keys.**

## Wallets

| Purpose | Address | Key var | Status |
|---|---|---|---|
| Productive signer / MoltGuard-Receiver / ERC-8004 owner | `0xD63f988201A0Bd184deaB7D4e97b1717B25a5800` | `BASE_WRITE_KEY`; address in `BASE_ADDR` | since the rotation of 2026-10-12: holds all 8 ERC-8004 IDs, receives payments, signs IPR and `/anchors/batch` anchors |
| **Legacy** (former productive signer) | `0x380238347e58435f40B4da1F1A045A271D5838F5` | — (keystore kept until the 30-day watch ends) | retired 2026-10-12: 0 ERC-8004 IDs, 0 ETH, final nonce 1791 (live 2026-10-12). Incoming value watched daily until 2026-11-11 (`scripts/watch_old_wallet.py`). Retired because its key had been exposed on a command line. |
| Credential and publication anchoring (`vc_anchor_signer` in `app/chain_keys.py`); console test wallet (CLAUDE.md exception) | `0xd8f5bB747f7459BF3e1cc1aD041E2cA57B946C38` | `BASE_ANCHOR_KEY`; address in `BASE_ANCHOR_ADDR` | signs credential batch anchors (`MolTrust/VC/v1`, `scripts/anchor_iprs.sh`) and publication anchors (`anchor_publication.py`), latest TechSpec v0.10 on 2026-10-20 (tx `0xa9431675…c2ae2a`, block 52867703). Also pays funnel/x402 tests and bounties under the CLAUDE.md caps. 0.0000784 ETH, nonce 238 (live 2026-10-20, block 52867799) |
| — (none) | `0x9068E25d8EA247a24Abf9Ff42BfA084931B3bA91` | `BASE_WALLET_ADDRESS` | **stale string, no code reads it**; holds 2.000199 USDC / 0 ETH / nonce 0 |

`MOLTRUST_REGISTRY_PRIVATE_KEY` is **not** a Base wallet — it is a 32-byte
**Ed25519** key for agent-card/JWS signing (`app/registry_keys.py`,
kid `moltrust-registry-2026-v1`, annual rotation due Jan 2027). Feeding it to
`Account.from_key` yields a meaningless address; it has no on-chain role.

## The 2026-07-08 discrepancy — RESOLVED

The previous revision recorded two readings of why `BASE_WALLET_KEY` did not
match its declared `BASE_WALLET_ADDRESS`. Reading (a) is confirmed:

- `BASE_WALLET_KEY` derives to `0x3802…38F5`, not to `0x9068…B3bA91`.
- `BASE_WALLET_ADDRESS=0x9068…` is a leftover string. No code path in the prod
  checkout reads that variable — the signing paths derive the address from the
  key at runtime (`app/main.py:4305-4306`, `scripts/anchor_existing_keys.py:13-14`).
- `0x9068` is therefore not burned-and-in-use; it is burned-and-unreferenced.
  Independently recorded in `MoltyCel/moltrust-security:reports/2026-07_full_sweep_fable5.md:107`.

The variable is commented out in `~/.moltrust_secrets` as of 2026-08-04 (server
config, not repo-managed) so the dead value cannot re-seed the same wrong
inference.

## ERC-8004 registry position

- IdentityRegistry `0x8004A169FB4a3325136EB29fA0ceB6D2e539a432` (Base mainnet).
- **Since 2026-10-12:** `balanceOf(0xD63f…5800)` = 8, `balanceOf(0x3802…38F5)` = 0,
  `ownerOf(33553)` → `0xD63f…5800` (live 2026-10-12). The lines below are the
  state before the rotation.
- Agent ID `33553`, `ownerOf` → `0x3802…38F5` (until 2026-10-12), `tokenURI` →
  `https://moltrust.ch/.well-known/erc8004.json`.
- Registration tx `c51b8153…23cc9` from `0x3802…38F5`, block 43523900.
- The contract answers `supportsInterface(0x80ac58cd)` = true, so the agent ID is
  a transferable ERC-721. Migrating it needs **no re-registration** — but it does
  need an on-chain `transferFrom` signed by the current owner.
- `balanceOf(0x3802…)` = **4**, so a rotation moves four agent IDs, not one.
  `tokenOfOwnerByIndex` reverts (no ERC721Enumerable), so the other three must be
  enumerated from Transfer logs before any migration.
- `getApproved(33553)` = zero address — no outstanding approval.

## Rotation of 0x3802 — DONE 2026-10-12

The productive signer moved to `0xD63f…5800` on 2026-10-12: all 8 ERC-8004 IDs
transferred, payment addresses switched (`BASE_ADDR`, read at runtime since
#755), the first NEW-signed anchor is tx `0xa3b9d7f4…6941` (block 52527148) and
a test payment from `0xd8f5` to NEW is tx `0x84ff086a…da1c1a` (block 52527123).
`0x3802…38F5` is legacy. The history below explains why the rotation happened.

## Rotation trigger for 0x3802 (history)

The key was exposed on a shell command line: `src/services/skill.ts:985` in
`moltguard` builds `cast send --private-key ${BASE_WRITE_KEY} …` as a shell
string, putting the key in world-readable `/proc/<pid>/cmdline` for the ~30 s an
anchor runs. Still present in the running build
(`dist/services/skill.js:881`, built 2026-07-28); the viem in-process fix is
specified in `MoltyCel/moltrust-security:reports/2026-07_welle1_status.md` §1b.4 but not yet built.

Marginal exposure is low: reading `/proc/<pid>/cmdline` requires a local shell on
the host, and anyone holding one can already read `~/.moltrust_secrets` directly.
The leak is a hygiene defect, not a privilege-escalation path.

Assets at risk on `0x3802`: 0.002288 ETH, 0 USDC, four ERC-8004 agent IDs.

**Rotation stays deferred to the first-revenue trigger.** It is human-gated
(wallet/keys rule) and cannot be done without on-chain transactions signed by
Lars: fund a fresh EOA, `transferFrom` four agent IDs, sweep the remaining ETH,
then realign `BASE_WALLET_KEY` / `BASE_WRITE_KEY`. Doing it before there is
revenue buys nothing and spends gas twice.
