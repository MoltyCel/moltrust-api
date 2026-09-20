# Polymarket Commitment Encoding — MolTrust Spec

**Status:** Draft 1 · **Created:** 2026-09-20 · **Chain:** Polygon (`eip155:137`)
**Depends on:** Polymarket CTF Exchange V2, verified 2026-09-20
**Analysis this comes from:** `~/Downloads/polymarket-binding.md`

This defines how a MolTrust commitment is carried in a Polymarket order, how it is
recognised on-chain, and what a verifier can and cannot conclude from it.

## Why the field works

The order a maker signs under EIP-712 carries `bytes32 metadata`, described in
`Structs.sol` as "the metadata associated with the order, hashed". The typehash covers it:

```
Order(uint256 salt,address maker,address signer,uint256 tokenId,uint256 makerAmount,
uint256 takerAmount,uint8 side,uint8 signatureType,uint256 timestamp,bytes32 metadata,
bytes32 builder)
```

keccak256 of that string is `0xbb86318a2138f5fa8ae32fbe8e659f8fcf13cc6ae4014a707893055433818589`,
which matches `ORDER_TYPEHASH` in the deployed contract. `Hashing.sol` hashes `0x180` bytes —
twelve words, the typehash plus eleven fields — so `metadata` is inside the signature.

`Events.sol` emits it verbatim as the last data word of `OrderFilled`. The signature that
authorises the trade therefore also binds the commitment hash to maker, tokenId, side,
amounts and the order's millisecond timestamp.

## Deployed addresses

| Contract | Address |
|---|---|
| CTFExchangeV2 | `0xE111180000d2663C0091e4f400237545B87B996B` |
| NegRiskCtfExchangeV2 | `0xe2222d279d744050d28e00520010520000310F59` |

The V1 exchange `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` is archived upstream and
produced zero fills in a 50-minute sample. Do not read it.

## Encoding

```
metadata[0..1]  = 0x4D54            magic, ASCII "MT"
metadata[2]     = 0x01              version
metadata[3..31] = keccak256(preimage)[0..28]      29 bytes, 232 bits
```

The prefix exists because the field is already in use by other parties, in at least three
shapes: full-entropy hashes, packed binary with a recurring `017b` tail, and ASCII —
`0x70726564` is "pred", `0x32303236` is "2026". A verifier that treats any non-zero
`metadata` as a MolTrust commitment would ingest other people's data. Of 13 populated
values observed on 2026-09-20, none began `0x4D5401`; a full-entropy hash hits that prefix
with probability 2⁻²⁴.

Truncating to 29 bytes leaves 232 bits. Second-preimage resistance is what a commitment
needs, and 232 bits supplies it with room to spare.

keccak256 rather than SHA-256, so a contract can recompute the digest later without an
extra precompile. This differs from the canonical skill hash, which is SHA-256; the two
serve different chains and are not interchangeable.

## Preimage

UTF-8, LF line endings, no trailing whitespace on any line, no trailing newline, NFC.
Field order is fixed. Addresses lowercase without checksum casing, integers decimal
without separators.

```
moltrust-pm-commit/1
did=did:moltrust:157224190be24072
signer=0x0000000000000000000000000000000000000000
maker=0x0000000000000000000000000000000000000000
chain=eip155:137
exchange=0xe111180000d2663c0091e4f400237545b87b996b
tokenId=109940776822972326436736251138122529787304609190841333433010412811874399854702
side=BUY
priceMinBps=4000
priceMaxBps=6000
notBeforeMs=1789930000000
notAfterMs=1789933600000
nonce=0x9f2c1a77
```

`priceMinBps`/`priceMaxBps` are a band, not a point. The execution price is set by the
match, so an agent that had to name it in advance could never keep its own promise. A band
is what a promise about a trade can honestly hold.

`signer` and `maker` are separate fields because they are separate addresses. `maker` is
the Polymarket proxy wallet that appears on-chain; `signer` is the key that authorises the
order and never appears in the event.

## The two things the chain alone cannot settle

### Priority — MolTrust as first anchor

`metadata` proves the maker fixed a hash when signing. It does not prove the preimage
existed publicly beforehand, so on its own the scheme is a receipt.

Closing it: MolTrust publishes the preimage and anchors its digest on Base before the
order is placed. A verifier then has two timestamps from two independent chains — the
anchor at T₀ on Base, the fill at T₁ on Polygon — and priority holds when T₀ < T₁.

Polygon reaches `finalized` one to two blocks behind `latest`, a median of three seconds,
so T₁ is settled almost immediately. Cross-chain timestamps carry the usual skew between
two validator sets, so a verifier should require a margin rather than a strict inequality,
in the manner of AAE §7.6. A commitment anchored seconds before its fill proves less than
one anchored minutes before, and the margin should be stated in the verdict rather than
assumed.

### Attribution — DID to signer

`OrderFilled` carries `maker` and `taker`. It does not carry `signer`. Anyone can deploy a
fresh proxy, so a reputation attached to `maker` survives only until its holder wants it
not to. Measured on 2026-09-20: of 3,252 makers active in a ten-minute window, 47.5 % had
also traded eighty minutes earlier. That number is an upper bound on rotation and not a
rotation rate — on-chain, a new wallet and an idle one look alike, which is the reason the
binding cannot rest on addresses.

Closing it, in two levels that a verifier should not confuse:

**Level 1, chain data only.** MolTrust anchors "DID D controls maker M from time T". A
fill's `maker` then resolves to a DID without any off-chain payload. Rotating the proxy
breaks this until the new address is anchored, and the gap is visible as an unanchored
maker rather than as silence.

**Level 2, with the order payload.** The DID document lists the signer key as a
`verificationMethod` with `blockchainAccountId` = `eip155:137:<signer>`. Given the order,
a verifier checks the EIP-712 signature against that key and reaches the DID regardless of
which proxy was used. Proxy rotation no longer matters; a signer key change is recorded in
the DID document with its own anchored timestamp.

Level 2 needs the order payload, which the public API does not return. Until an agent
supplies it, Level 1 is what a third party can check unaided, and a verdict should say
which level it rests on.

## Verification

1. Read `OrderFilled` on either exchange, filtered by `topics[2]` = maker.
2. Reject unless `metadata[0..2]` equals `0x4D5401`.
3. Fetch the preimage MolTrust published for that digest.
4. Recompute `keccak256(preimage)[0..28]` and compare with `metadata[3..31]`.
5. Check the preimage against the fill: `maker`, `tokenId`, `side`, and the implied price
   inside the band. Price is not stored — for BUY it is `makerAmountFilled /
   takerAmountFilled`, for SELL the inverse.
6. Check `notBeforeMs ≤ fill block timestamp ≤ notAfterMs`.
7. Check the anchor: T₀ on Base before T₁ on Polygon, by the stated margin.
8. For Level 2, verify the order signature against the DID document's verification method.

A failure at step 4 means the preimage does not belong to that fill. A failure at step 5
means the agent traded something other than what it promised, which is a finding rather
than an error.

## Non-claims

- The encoding does not prove an order was placed. An order that never fills never reaches
  the chain, so a commitment can be made and quietly abandoned. A record of commitments
  without fills is itself evidence and should be kept.
- It does not identify a counterparty. `taker` was the exchange itself in 539 of 1,525
  sampled fills.
- It does not distinguish an autonomous agent from a person operating a script. The chain
  shows frequency, not who runs the key.
- It does not carry the market question. `tokenId` is a 77-digit integer; resolving it to a
  question and outcome needs `data-api.polymarket.com`, which is not part of this scheme.

## Update Triggers

- `ORDER_TYPEHASH` changes, which the prototype checks on every run and treats as fatal.
- A new exchange address supersedes CTFExchangeV2.
- Another party adopts the `0x4D54` prefix.
