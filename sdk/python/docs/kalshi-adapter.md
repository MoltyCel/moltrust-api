# Kalshi order guard — `moltrust_enforce.adapters.kalshi`

An agent that holds a Kalshi API key sends every order through `KalshiOrderGuard`. The guard
asks `POST /enforce/check` for a verdict and sends the order only on PERMIT, or on a PENDING
that `/enforce/ratify` approved. A DENY sends nothing. Every decision leaves a receipt that
`moltrust-verify` recomputes offline.

```python
from moltrust_enforce import EnforceClient
from moltrust_enforce.adapters.kalshi import (
    KalshiClient, KalshiOrderGuard, KalshiProfile, ed25519_attester)

profile = KalshiProfile(
    mandate_id="desk-1", principal={"did": DID, "public_key": PUB_HEX},
    series=("KXBTC15M", "KXETH15M"), categories=("Crypto",), sides=("bid",),
    max_contracts="10", min_price="0.05", max_price="0.95",
    max_market_day_exposure="20.00", hold_above="5.00",
    valid_from="2026-10-20T00:00:00Z", valid_until="2026-10-21T00:00:00Z")

guard = KalshiOrderGuard(EnforceClient("https://api.moltrust.ch", api_key=KEY), profile,
                         KalshiClient(KEY_ID, PEM),            # demo host by default
                         attest=ed25519_attester(DID, SIGNING_KEY), receipts_dir="receipts")
out = guard.place_order({"ticker": "KXBTC15M-...", "side": "bid", "count": "1",
                         "price": "0.4500", "time_in_force": "immediate_or_cancel",
                         "self_trade_prevention_type": "taker_at_cross"})
out.disposition, out.sent, out.receipt_path
```

## Mandate schema

| Profile field | Mandate constraint | Transaction field |
|---|---|---|
| `series` | `enum` | `series_ticker` |
| `categories` | `enum` | `category` (the series' primary category) |
| `sides` (default `("bid",)`) | `enum` | `side` (`bid` = buy YES, `ask` = sell YES) |
| `max_contracts` | `range 1..max` | `count_c`, hundredths of a contract |
| `min_price`, `max_price` | `range` | `price_e4`, ten-thousandths of a dollar |
| `max_market_day_exposure` | `range 0..cap` | `market_day_exposure_e6`, millionths of a dollar |
| `hold_above` | allow `0..hold`, hold `hold+1..cap` | `order_cost_e6` |
| `valid_from`, `valid_until` | `range` | `t_min`, epoch minutes |
| `forbid_politics` (default True) | `forbid` grant | action `market_class: politics` |
| `forbid_mentions` (default True) | `forbid` grant | action `market_class: mention` |

The action is `{"verb": "kalshi_order", "market_class": ...}`. Only `standard` (and a class
whose forbid flag is off) has allow and hold grants. `unclassified` has no grant at all and
is DENY as an unaddressed action. A value the guard cannot derive stays absent and fails its
constraint, so a malformed count, a float price or a missing price is a forbid.

Order cost is what the order can lose: `count * price` for a bid, `count * (1 - price)` for
an ask. Exposure per market and UTC day is the gross cost of the orders this guard sent to
that market that day, plus the new one (`ExposureLedger`, in memory or a JSON file). Fills,
cancels and settlements are not netted; the cap can only bind earlier than a net figure.

## Classification

From the series fields `category`, `categories`, `tags` and `ticker` (Get Series):

- **politics**: `Politics` or `Elections` in `category`/`categories`, or a tag in
  `POLITICS_TAGS` (`US Elections`, `Congress`, `Trump`, ...);
- **mention**: `Mentions` in `category`/`categories`, or `MENTION` in the series ticker;
- **standard**: any other series with a category;
- **unclassified**: no series, no ticker or no category.

Kalshi documents the fields, not a fixed set of values. The category names are therefore
defaults that a caller can override (`classify(..., politics_categories=...)`). Because
politics and mentions are forbidden and everything else must also match the profile's own
series and category lists, a misclassified series fails closed.

## Kalshi endpoints

Read on docs.kalshi.com on 2026-10-20:

- Orders: `POST /portfolio/events/orders` (Create Order V2). The legacy
  `POST /portfolio/orders` is deprecated since June 2026 and answers
  "Please switch to the V2 endpoints". `count` and `price` are fixed-point strings.
- Auth: `KALSHI-ACCESS-KEY` (key id), `KALSHI-ACCESS-TIMESTAMP` (ms),
  `KALSHI-ACCESS-SIGNATURE` = base64 of RSA-PSS/SHA-256 (or Ed25519) over
  `timestamp + METHOD + path`, path without the query string.
- Demo hosts: `https://external-api.demo.kalshi.co/trade-api/v2` and
  `https://demo-api.kalshi.co/trade-api/v2`. `KalshiClient` refuses every other host unless
  `allow_non_demo=True`.

## Holds and Telegram

`TelegramApprover(send, wait, authority_did=..., signing_key=...)` posts the hold with
Approve/Reject buttons (`kg|approve|<token>`, `kg|reject|<token>`) through the caller's
`send` and waits for the answer through `wait`. On approve it signs `APPROVED` with the
approver's key; the authority must be named in the mandate. No answer before the timeout
sends nothing. The package contains no Telegram client.

## Receipts

Each receipt holds `record`, `bundle`, `mandate` and `transaction`, so

```
moltrust-verify --input receipts/001-allow-<id>.json --trust-list trust.json
```

recomputes V1 to V4 offline. It also carries the server record, the ratification for a hold,
and Kalshi's answer when the order was sent.

## Demo run and latency

- `examples/kalshi/demo_run.py`: 26 orders on 15-minute crypto markets (KXBTC15M, KXETH15M;
  KXDOGE15M as the series outside the mandate), 7 forbid, 3 hold, every receipt recomputed.
  Needs `KALSHI_DEMO_KEY_ID` and `KALSHI_DEMO_PRIVATE_KEY_PATH`. `--telegram` only on the
  MolTrust server.
- `examples/kalshi/bench_check.py`: p50/p95 of `/enforce/check` as the guard sees it.

`tests/test_adapter_kalshi.py` and `tests/test_kalshi_demo_run.py` run both against the real
kernel and a mocked Kalshi API that checks every request signature.
