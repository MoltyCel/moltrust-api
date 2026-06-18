# Billing configuration (Stripe) — source of truth

> Stripe `prod_*` / `price_*` IDs are **not secrets** (they are public object
> references and safe to commit). API keys (`sk_live_…`), webhook secrets
> (`whsec_…`) and GitHub tokens are secrets and must never appear here.

Last updated: 2026-06-18.

## Model

Subscription pricing is **numeral-parity** across USD and EUR ($99/€99
Professional, $299/€299 Scale) with **USD as default** and EUR as the second
currency. There is **no auto-conversion** — each currency has its own
manually-set, round-number Stripe Price. Monthly credit grants are unchanged
across currencies.

`app/billing.py` does **not** hardcode Price IDs. `create_checkout` calls
`stripe.Price.list(currency=req.currency, active=True, expand=["data.product"])`
and matches a Price by **product name == TIERS[tier]["name"]**. So the only
requirement is that an active recurring-monthly Price exists per (product,
currency). Keep the table below in sync when prices change.

## Products

| Tier         | Product ID                |
|--------------|---------------------------|
| Professional | `prod_Ua6Z0dWZPEpSr7`     |
| Scale        | `prod_Ua6Zq7kxDOuqFV`     |

## Active prices (recurring, monthly)

| Tier         | Currency | Amount   | Price ID                          |
|--------------|----------|----------|-----------------------------------|
| Professional | USD      | 9900     | `price_1TjPJGAmsmnRdiKqx2dUeId9`  |
| Professional | EUR      | 9900     | `price_1TjPJHAmsmnRdiKqDnyOoAtl`  |
| Scale        | USD      | 29900    | `price_1TjPJHAmsmnRdiKq0edHO2Qn`  |
| Scale        | EUR      | 29900    | `price_1TjPJIAmsmnRdiKqUfc9TsAt`  |

`monthly_credits`: Professional 10,000 · Scale 30,000 (currency-independent).

## Archived prices (inactive — CHF, do not reactivate)

| Tier         | Currency | Amount   | Price ID                          |
|--------------|----------|----------|-----------------------------------|
| Professional | CHF      | 9900     | `price_1TawMgAmsmnRdiKqLmrE9hoV`  |
| Scale        | CHF      | 29900    | `price_1TawMhAmsmnRdiKqmDY25Hee`  |

CHF was the original launch currency; it was archived (`active=false`) once the
USD/EUR prices went live. The web surface no longer shows any CHF pricing.

## Checkout currency mapping

- Web (`/pricing.html`) sends `POST /billing/checkout` with `tier` ∈
  {`professional`, `scale`} and `currency` ∈ {`usd`, `eur`}, where `currency`
  is the **displayed** currency (USD default; EUR auto-detected for EU
  visitors; manual toggle). Numeral parity means the amount shown is identical
  in both currencies — only the Stripe Price (and symbol) differ.
- `Free` → signup (no checkout). `Enterprise` → `/enterprise/` (volume ladder
  + enquiry form, no Stripe).

## Adaptive Pricing

Enabled per Checkout Session in `app/billing.py` via
`adaptive_pricing={"enabled": True}` (verified accepted by the account
2026-06-18). The account is CH/CHF and tier prices are USD/EUR, so a buyer in
e.g. Switzerland sees Stripe's local-currency conversion on the hosted checkout
page while the website stays fixed (USD on /pricing, EUR on /compliance). No
extra Price objects are needed.

## Audit Evidence Bundle (one-off, `prod_UfJJN8g40Up0qn`)

| Currency | Amount | Price ID | Status |
|----------|--------|----------|--------|
| EUR | 199000 (€1,990) | `price_1TjeQ3AmsmnRdiKqOzkXtmG0` | active (2026-06-18, approved) |
| CHF | 199000 (CHF 1,990) | `price_1TfygdAmsmnRdiKqrUAcrBxe` | active (legacy) |

EUR payment link `https://buy.stripe.com/aFa4gyguB5v61yq43A0VO03` is wired on
`/compliance.html` ("Buy the bundle — €1,990"). No USD bundle price is created
(compliance is the EUR surface).
