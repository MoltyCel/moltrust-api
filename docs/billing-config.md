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

The two USD base prices also carry `currency_options` (EUR/CHF/GBP at the same
numeral amount, no FX) for local-currency presentment at Checkout — see
"Multi-currency presentment" below.

## Archived prices (inactive — CHF, do not reactivate)

| Tier         | Currency | Amount   | Price ID                          |
|--------------|----------|----------|-----------------------------------|
| Professional | CHF      | 9900     | `price_1TawMgAmsmnRdiKqLmrE9hoV`  |
| Scale        | CHF      | 29900    | `price_1TawMhAmsmnRdiKqmDY25Hee`  |

CHF was the original launch currency; it was archived (`active=false`) once the
USD/EUR prices went live. The web surface no longer shows any CHF pricing.

## Checkout currency mapping

- Web sends `POST /billing/checkout` with `tier` ∈ {`professional`, `scale`}
  (plus a `currency` display hint). The hint is **display-only**: checkout
  always uses the USD base price and lets Stripe auto-present the buyer's local
  currency (see "Multi-currency presentment" below). The website display stays
  fixed (USD on /pricing, EUR on /compliance).
- `Free` → signup (no checkout). `Enterprise` → `/enterprise/` (volume ladder
  + enquiry form, no Stripe).

## Multi-currency presentment at Checkout (currency_options) — WORKS

Adaptive Pricing is **not available** for this CH account, so local-currency
presentment uses **manual currency prices** (`currency_options`) instead. The
two USD base prices carry explicit per-currency amounts at **numeral parity
(no FX conversion)**:

| Tier         | USD (base) | EUR   | CHF   | GBP   |
|--------------|------------|-------|-------|-------|
| Professional | 9900       | 9900  | 9900  | 9900  |
| Scale        | 29900      | 29900 | 29900 | 29900 |

Added to the existing active prices via `Price.modify(..., currency_options=…)`
on 2026-06-18 — **no new Price objects** (the update endpoint accepts
`currency_options`).

`app/billing.py::create_checkout` now **always uses the USD base price** and
does **not** force a session `currency` (and no longer sets `adaptive_pricing`).
With `currency_options` present and no forced currency, **Stripe Checkout
auto-detects the buyer IP and presents their local currency**, falling back to
USD. The website display stays fixed (USD on /pricing, EUR on /compliance) —
only the Stripe page localizes.

**Verified by real presentment (2026-06-18)** — rendering live Checkout pages:
forced `usd`/`eur`/`chf` sessions render **$99.00 / €99.00 / CHF 99.00**; an
**AUTO** session (no forced currency = production behaviour) auto-presented
**€99.00** from a EUR-zone egress IP. So geo-presentment genuinely works (not
just "API accepted"); a US IP falls back to $99.

The separate EUR Price objects (`price_1TjPJHAmsmnRdiKqDnyOoAtl`,
`price_1TjPJIAmsmnRdiKqUfc9TsAt`) are now unused by checkout but left active.
The old CHF tier + bundle Payment Links are already inactive; the EUR €1,990
bundle link (`buy.stripe.com/aFa4…VO03`) stays.

## Audit Evidence Bundle (one-off, `prod_UfJJN8g40Up0qn`)

| Currency | Amount | Price ID | Status |
|----------|--------|----------|--------|
| EUR | 199000 (€1,990) | `price_1TjeQ3AmsmnRdiKqOzkXtmG0` | active (2026-06-18, approved) |
| CHF | 199000 (CHF 1,990) | `price_1TfygdAmsmnRdiKqrUAcrBxe` | active (legacy) |

EUR payment link `https://buy.stripe.com/aFa4gyguB5v61yq43A0VO03` is wired on
`/compliance.html` ("Buy the bundle — €1,990"). No USD bundle price is created
(compliance is the EUR surface).
