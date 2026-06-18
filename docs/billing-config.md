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

The two USD base prices carry now-inert flat `currency_options` (EUR/CHF/GBP 9900) —
overridden by forcing the session currency; see "Checkout currency presentment" below.

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
  matches the price for that currency hint AND forces the session currency
  (/pricing->USD $99, bank converts; /compliance->EUR €99 flat; see "Checkout
  currency presentment" below). The website display stays
  fixed (USD on /pricing, EUR on /compliance).
- `Free` → signup (no checkout). `Enterprise` → `/enterprise/` (volume ladder
  + enquiry form, no Stripe).

## Checkout currency presentment — USD-only on /pricing (Option B)

**Decision (2026-06-18): USD-only presentment on /pricing.** A buyer on /pricing
pays **$99 / $299 in USD** on the Stripe page regardless of location; their bank
does the FX (~€86 on a EUR statement). Fair, drift-free, no FX-refresh job.
**/compliance is unchanged** — it presents **EUR €99 / €299 flat** (intentional
EU-compliance pricing). Website display unchanged (USD /pricing, EUR /compliance).

`app/billing.py::create_checkout` matches the price for the requested surface
(`currency` hint: usd->USD price, eur->EUR price) **and forces that currency on
the Checkout Session** (`currency=...`). Forcing the currency is what makes a EU
buyer on /pricing see **$99 (USD)**, not a flat €99.

### Why flat currency_options (the previous approach) was WRONG
The USD prices were given `currency_options` EUR/CHF/GBP at **flat numeral parity**
(9900/29900). That made a EU buyer pay a **flat €99** — after FX **more than $99**
(~€86). Numeral parity is wrong for a USD-anchored plan. Per Stripe, defining a
`currency_option` for a currency also **disables Adaptive Pricing FX** for it, so
the flat amount always wins. `currency_options` **cannot be removed via the API**
("cannot be unset"; removal needs new Price objects), so billing.py **forces the
session currency to USD**, rendering the lingering options inert. **Do not stop
forcing the session currency, or the flat €99 returns.** **Option C** (FX-converted
flat amounts kept fresh by a refresh job) is explicitly **excluded** — not built.

### Adaptive Pricing (Option A) — NOT usable here
Would show a EU buyer the FX-converted ~€86 on the Stripe page, but is **not usable**:
- **CH is supported** (Stripe docs list CH under Europe), but
- it **requires the price currency to be a settlement currency** — this account
  settles **CHF only** (`balance.available=['chf']`) while plans are priced in
  **USD**, so it would not convert the USD prices;
- it is **Dashboard-only** to enable (`dashboard.stripe.com/settings/adaptive-pricing`),
  not API-enablable;
- `currency_options` on the prices **disable** its FX for EUR/CHF/GBP.

**To enable A later (Lars):** (1) enable it at the Dashboard URL above; (2) add
**USD as a settlement currency** (or re-price the plans in CHF); (3) recreate the
Pro/Scale prices **without** currency_options; (4) drop the `currency=` force in
billing.py. Until then, Option B (USD-only) is live.

### Rendered proof (2026-06-18, German egress IP)
Live `checkout.stripe.com`: **/pricing (USD price, forced usd) -> `$99.00`**;
**/compliance (EUR price, forced eur) -> `€99.00`**. Real presentment, not "API accepted".

## Audit Evidence Bundle (one-off, `prod_UfJJN8g40Up0qn`)

| Currency | Amount | Price ID | Status |
|----------|--------|----------|--------|
| EUR | 199000 (€1,990) | `price_1TjeQ3AmsmnRdiKqOzkXtmG0` | active (2026-06-18, approved) |
| CHF | 199000 (CHF 1,990) | `price_1TfygdAmsmnRdiKqrUAcrBxe` | active (legacy) |

EUR payment link `https://buy.stripe.com/aFa4gyguB5v61yq43A0VO03` is wired on
`/compliance.html` ("Buy the bundle — €1,990"). No USD bundle price is created
(compliance is the EUR surface).
