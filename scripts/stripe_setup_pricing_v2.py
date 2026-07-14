#!/usr/bin/env python3
"""Stripe pricing v2 provisioning (USD) — Beschluss Lars 12.07.2026 (E1–E5).

New public catalogue, all USD:
  Base      $19/mo (incl. 2 production slots)  + $190/yr (10x)
  Slot      $9/mo add-on production slot        + $90/yr
  Scale     $299/mo (incl. 75 slots)  + $2990/yr  + $3.50/slot metered overage
  One-off   $29 workspace + $12/agent (one-time)
  Compliance Archive  $99/mo (SMB) · $299/mo (Pro) · $1,990 one-time bundle
  Free      no Stripe entity (enforced in API)
  Wholesale $4 — NOT in this catalogue, never a public self-serve price.

Idempotent by Price lookup_key (mt_v2_* — never collides with the legacy CHF keys).
DRY-RUN by default. `--live` creates. `--deactivate-legacy` archives the old CHF
prices (active=False) — existing subscriptions on them CONTINUE (no migration,
per the decision); only new checkouts are blocked. Archiving is reversible.

Requires STRIPE_SECRET_KEY in env (source ~/.moltrust_secrets).
"""
from __future__ import annotations
import argparse, json, os, sys

USD = "usd"

def cents(a: float) -> int:
    return int(round(a * 100))

# --- v2 catalogue -----------------------------------------------------------
TIERS = [
    {"key": "base", "product_name": "MolTrust Base",
     "product_description": "$19/mo. Includes 2 active production slots. Renewals, issuance and anchoring are bundled (no per-event billing).",
     "metadata": {"included_slots": "2", "tier": "base"},
     "prices": [
        {"lookup_key": "mt_v2_base_monthly", "nickname": "Base monthly", "amount": 19.00, "recurring": {"interval": "month"}},
        {"lookup_key": "mt_v2_base_annual",  "nickname": "Base annual (10x)", "amount": 190.00, "recurring": {"interval": "year"}},
     ]},
    {"key": "slot", "product_name": "MolTrust Production Slot",
     "product_description": "$9/mo add-on production slot. A slot = one active production agent per month (5 key rotations/slot included).",
     "metadata": {"tier": "slot_addon"},
     "prices": [
        {"lookup_key": "mt_v2_slot_monthly", "nickname": "Slot monthly", "amount": 9.00, "recurring": {"interval": "month"}},
        {"lookup_key": "mt_v2_slot_annual",  "nickname": "Slot annual (10x)", "amount": 90.00, "recurring": {"interval": "year"}},
     ]},
    {"key": "scale", "product_name": "MolTrust Scale",
     "product_description": "$299/mo. Includes 75 active production slots; overage $3.50/slot. Renewals/issuance/anchoring bundled.",
     "metadata": {"included_slots": "75", "overage_per_slot": "3.50", "tier": "scale"},
     "prices": [
        {"lookup_key": "mt_v2_scale_monthly", "nickname": "Scale monthly", "amount": 299.00, "recurring": {"interval": "month"}},
        {"lookup_key": "mt_v2_scale_annual",  "nickname": "Scale annual (10x)", "amount": 2990.00, "recurring": {"interval": "year"}},
        {"lookup_key": "mt_v2_scale_overage_slot", "nickname": "Scale slot overage (per slot/mo)", "amount": 3.50,
         "recurring": {"interval": "month"}},
     ]},
    {"key": "oneoff", "product_name": "MolTrust One-off Workspace",
     "product_description": "One-time audit/handover workspace: $29 workspace + $12 per agent.",
     "metadata": {"tier": "oneoff"},
     "prices": [
        {"lookup_key": "mt_v2_oneoff_workspace", "nickname": "One-off workspace", "amount": 29.00, "recurring": None},
        {"lookup_key": "mt_v2_oneoff_agent",     "nickname": "One-off per agent",  "amount": 12.00, "recurring": None},
     ]},
    {"key": "compliance_archive", "product_name": "MolTrust Compliance Archive",
     "product_description": "Recurring compliance archive. SMB $99/mo (20 agents, 3-yr retention) · Pro $299/mo (100 agents, 7-yr). One-time evidence bundle $1,990.",
     "metadata": {"tier": "compliance_archive"},
     "prices": [
        {"lookup_key": "mt_v2_compliance_smb_monthly", "nickname": "Compliance Archive SMB", "amount": 99.00, "recurring": {"interval": "month"}},
        {"lookup_key": "mt_v2_compliance_pro_monthly", "nickname": "Compliance Archive Pro", "amount": 299.00, "recurring": {"interval": "month"}},
     ]},
    {"key": "audit_bundle", "product_name": "MolTrust Audit Evidence Bundle",
     "product_description": "One-time signed audit evidence bundle (entry SKU). $1,990.",
     "metadata": {"tier": "audit_bundle"},
     "prices": [
        {"lookup_key": "mt_v2_audit_bundle_usd", "nickname": "Audit evidence bundle (USD)", "amount": 1990.00, "recurring": None},
     ]},
]

# legacy prices to archive on --deactivate-legacy (explicit IDs from live inventory 2026-07-12).
# Archiving blocks NEW checkouts; existing subscriptions continue (no migration).
LEGACY_PRICE_IDS = {
    "price_1TjeQ3AmsmnRdiKqOzkXtmG0": "Audit Bundle EUR 1990",
    "price_1TfygdAmsmnRdiKqrUAcrBxe": "Audit Bundle CHF 1990",
    "price_1TjPJIAmsmnRdiKqUfc9TsAt": "Scale EUR 299 (old)",
    "price_1TjPJHAmsmnRdiKq0edHO2Qn": "Scale USD 299 (old)",
    "price_1TjPJHAmsmnRdiKqDnyOoAtl": "Professional EUR 99",
    "price_1TjPJGAmsmnRdiKqx2dUeId9": "Professional USD 99",
    "price_1TawMfAmsmnRdiKqWonvYyM5": "PayPerUse compliance_export CHF",
    "price_1TawMeAmsmnRdiKqcUDdmQCR": "PayPerUse anchor CHF",
    "price_1TawMdAmsmnRdiKqFUk6dpal": "PayPerUse issuance CHF",
    "price_1TawMcAmsmnRdiKq7n3puRBP": "PayPerUse renewal CHF",
    "price_1TMPJaAmsmnRdiKqdUQwSYHy": "Developer CHF 29",
    "price_1TMPJZAmsmnRdiKq2bP2reXA": "Startup CHF 149",
    "price_1TMPJZAmsmnRdiKqMNMduBHo": "Business CHF 499",
}

def find_product(stripe, name):
    for p in stripe.Product.list(active=True, limit=100).auto_paging_iter():
        if p["name"] == name:
            return p
    return None

def find_price(stripe, lookup_key):
    r = stripe.Price.list(lookup_keys=[lookup_key], limit=1)
    return r["data"][0] if r["data"] else None

def ensure_product(stripe, tier, dry):
    name = tier["product_name"]
    ex = find_product(stripe, name)
    if ex: return {"id": ex["id"], "name": name, "action": "exists"}
    if dry: return {"id": "<dry-run>", "name": name, "action": "would-create"}
    c = stripe.Product.create(name=name, description=tier.get("product_description", ""), metadata=tier.get("metadata", {}))
    return {"id": c["id"], "name": name, "action": "created"}

def ensure_price(stripe, product_id, spec, dry):
    ex = find_price(stripe, spec["lookup_key"])
    if ex: return {"id": ex["id"], "lookup_key": spec["lookup_key"], "amount_usd": spec["amount"], "action": "exists"}
    if dry: return {"id": "<dry-run>", "lookup_key": spec["lookup_key"], "amount_usd": spec["amount"], "action": "would-create"}
    payload = {"product": product_id, "currency": USD, "lookup_key": spec["lookup_key"],
               "nickname": spec["nickname"], "unit_amount": cents(spec["amount"])}
    if spec.get("recurring"):
        payload["recurring"] = spec["recurring"]
    c = stripe.Price.create(**payload)
    return {"id": c["id"], "lookup_key": spec["lookup_key"], "amount_usd": spec["amount"], "action": "created"}

def deactivate_legacy(stripe, dry):
    out = []
    for pid, label in LEGACY_PRICE_IDS.items():
        try:
            p = stripe.Price.retrieve(pid)
        except Exception as e:
            out.append({"id": pid, "label": label, "action": f"error:{e}"}); continue
        if not p["active"]:
            out.append({"id": pid, "label": label, "action": "already-inactive"}); continue
        if dry:
            out.append({"id": pid, "label": label, "action": "would-archive"}); continue
        stripe.Price.modify(pid, active=False)
        out.append({"id": pid, "label": label, "action": "archived"})
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--deactivate-legacy", action="store_true")
    args = ap.parse_args()
    if not os.environ.get("STRIPE_SECRET_KEY"):
        print("ERROR: STRIPE_SECRET_KEY not set.", file=sys.stderr); sys.exit(2)
    import stripe
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    dry = not args.live
    out = {"mode": "live" if args.live else "dry-run", "tier_free": "no_stripe_entity", "tiers": {}}
    for t in TIERS:
        prod = ensure_product(stripe, t, dry)
        prices = [ensure_price(stripe, prod["id"], s, dry) for s in t["prices"]]
        out["tiers"][t["key"]] = {"product": prod, "prices": prices}
    if args.deactivate_legacy:
        out["legacy_deactivation"] = deactivate_legacy(stripe, dry)
    print(json.dumps(out, indent=2))
    if dry: print("\n(dry-run — pass --live to apply)", file=sys.stderr)

if __name__ == "__main__":
    main()
