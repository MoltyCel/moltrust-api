# Polymarket adapter — `moltrust_enforce.adapters.polymarket`

A MolCheck before every Polymarket order is signed. The guard turns the order an agent wants
to place into a mandate transaction, asks `POST /enforce/check` for a verdict and hands the
order to the caller's own signer only when the verdict lets it through:

| verdict | disposition | what happens |
|---|---|---|
| PERMIT | `allow` | the order goes to `submit(order)` |
| PENDING | `hold` | the order goes to `submit(order)` only after a ratification that comes back RATIFIED and APPROVED |
| DENY | `forbid` | nothing is handed over, nothing is signed |

Every decision, including every forbid, leaves a MolCheck record: an AER record and its
evidence bundle over the same mandate and transaction. `moltrust-verify` recomputes the
verdict from it offline, without the MolTrust server and without Polymarket.

The package ships no Polymarket client. It never sees a wallet key, builds no EIP-712
signature and sends nothing to a Polymarket endpoint. Further venues on request.

## What the mandate checks

One check per order, action `{"verb": "polymarket_order"}`. The profile fixes five things,
and an order has to satisfy all of them:

| check | transaction field | profile field |
|---|---|---|
| market | `token_id` (outcome token, decimal string) | `markets` |
| side | `side` (`BUY` / `SELL`) | `sides` (default `BUY` only) |
| size | `size_c` (hundredths of a share) | `max_size` |
| price band | `price_bps` (basis points of one USDC) | `min_price`, `max_price` |
| daily exposure | `market_day_exposure_e6`, `day_exposure_e6` | `max_market_day_exposure`, `max_day_exposure` |

plus the exchange (`exchanges`, CTF or NegRisk) and the validity window (`valid_from`,
`valid_until`). The order cost decides between the two grants: up to `hold_above` is
`allow`, above it up to `max_market_day_exposure` is `hold`.

The cost of an order is what it can lose: `size × price` for a BUY, `size × (1 − price)` for
a SELL. Exposure per market and UTC day is the cost of every order the guard handed over for
that token on that day plus this one; exposure per day is the same over all tokens. Both are
gross, so fills, cancels and settlements are not netted and a cap can only bind earlier.
With `ExposureLedger(path)` the ledger survives a restart.

A value the guard cannot derive stays out of the transaction, and a missing value fails its
constraint. A float price, a size with three decimals, a token id with a leading zero or a
`neg_risk` that is not a bool all end in `forbid`. The mandate is deny-by-default: nothing
outside the profile is allowed.

## Example without real money

`examples/polymarket/demo.py` runs six orders against one mandate with the kernel
in-process and a stub signer. No network, no wallet, no USDC:

```bash
pip install "moltrust-enforce[client]"
python examples/polymarket/demo.py --out ./demo-output
```

```
buy-in-band              allow  sent=True  verify=ok commitment=ok  grant[0] matched, ...
price-below-band         forbid sent=False verify=ok commitment=ok  price_bps 500 outside 1000..9000
market-not-in-mandate    forbid sent=False verify=ok commitment=ok  token_id '5568...' not allowed
sell-not-allowed         forbid sent=False verify=ok commitment=ok  side 'SELL' not allowed
hold-approved            hold   sent=True  verify=ok commitment=ok  grant[1] matched, ...
market-day-cap           forbid sent=False verify=ok commitment=ok  market_day_exposure_e6 32400000 outside 0..25000000; ...
2 order(s) handed to the stub signer, 0 verify failure(s)
```

Each scenario writes one file, and the same check runs from the shell:

```bash
moltrust-verify --input demo-output/001-allow-buy-in-band.json \
                --trust-list demo-output/trust.json
```

The core of it:

```python
from moltrust_enforce import EnforceClient
from moltrust_enforce.adapters.polymarket import (
    CommitmentParties, PolymarketOrderGuard, PolymarketProfile, ed25519_attester,
    ratification_proof,
)

profile = PolymarketProfile(
    mandate_id="pm-demo-1",
    principal={"did": AGENT_DID, "public_key": AGENT_PUBKEY_HEX},
    ratification_authorities=({"did": RISK_DID, "role": "risk_desk",
                               "public_key": RISK_PUBKEY_HEX},),
    markets=(YES_TOKEN, NO_TOKEN), sides=("BUY",), max_size="100",
    min_price="0.10", max_price="0.90",
    max_market_day_exposure="25.00", max_day_exposure="40.00", hold_above="10.00",
    valid_from="2026-11-01T00:00:00Z", valid_until="2026-12-31T23:59:59Z",
)

def submit(order):                 # your code: build, sign and post the order
    ...

guard = PolymarketOrderGuard(
    EnforceClient("https://api.moltrust.ch", api_key=KEY), profile, submit,
    commitment=CommitmentParties(signer=SIGNER, maker=MAKER),
    approver=lambda out: ratification_proof(out, ask_a_human(out), RISK_DID, RISK_KEY),
    attest=ed25519_attester(AGENT_DID, AGENT_KEY, keyid="agent-1"),
    receipts_dir="receipts")

outcome = guard.place_order({"token_id": YES_TOKEN, "side": "BUY",
                             "price": "0.42", "size": "20"})
outcome.disposition           # "allow" | "hold" | "forbid"
outcome.molcheck_record       # the MolCheck record (AER record)
outcome.aer_verify(trust)     # offline V1-V4
```

Prices and sizes go in as strings. `submit` gets the order unchanged except for
`client_order_id` and, with a commitment, `metadata`.

## Commitment in the order (`0x4D54`)

The signed Polymarket order carries `bytes32 metadata`, and the EIP-712 signature covers it.
With `commitment=CommitmentParties(signer, maker)` the guard fills it per the MolTrust
Polymarket commitment encoding:

```
metadata = 0x4D54 || 0x01 || keccak256(preimage)[0:29]
```

The preimage names the principal's DID, signer, maker, chain `eip155:137`, exchange, token,
side, the mandate's price band, a validity window (`ttl_s`, default one hour) and a nonce.
The metadata goes into the transaction, so the MolCheck record binds it, and into the order
handed to `submit` on allow or an approved hold. A forbidden order gets none.

Offline checks:

- `verify_commitment(preimage, metadata)` recomputes the digest and checks the prefix.
- `check_fill(preimage, maker=..., token=..., side=..., maker_amount_filled=...,
  taker_amount_filled=..., fill_timestamp_ms=..., metadata=...)` checks a fill against the
  preimage: maker, token, side, implied price inside the band (BUY `maker/taker`, SELL
  `taker/maker`, compared without division) and the commitment window.

What the guard does not do: it does not publish or anchor the preimage, and it does not tie
the signer key to the DID. Without a published, anchored preimage the metadata proves only
that the maker fixed a hash when signing, not that the promise existed beforehand. The
commitment also does not prove that an order was placed: an order that never fills never
reaches the chain.

## Holds

`approver(outcome) -> (decision, authority_proof) | None` is called for a hold. Return
`None` and the order stays unsent. `ratification_proof(outcome, approve, authority_did,
signing_key)` signs the decision with the approver's Ed25519 key; the authority must be the
principal or a role named in `ratification_authorities`, or `/enforce/ratify` rejects it.
How the human is asked is the caller's choice.

## Failure is forbid

- No server verdict (transport failure, error status, unreadable answer): `forbid`, with
  `no server verdict: ...` as the reason.
- A server verdict that does not recompute locally: `forbid`.
- `submit` raising: the exposure is already counted (the order may have left), the receipt
  is written with `sent: true`, and the exception propagates.
