"""In-memory stand-in for the few tables the ads plan and billing paths touch.

No database: the fake answers the exact statements app/ads_plan.py, app/billing.py
and app/accounts.py send, and returns None / [] for anything else (the credit and
request-log middlewares run their own statements and tolerate that).
"""
from __future__ import annotations

from contextlib import asynccontextmanager


class FakeDB:
    def __init__(self):
        self.api_keys: dict = {}      # key -> {"payer_ref", "email"}
        self.accounts: dict = {}      # payer_ref -> {"stripe_customer_id", "email"}
        self.subs: dict = {}          # stripe_subscription_id -> row dict
        self.meter: dict = {}         # (key_fp, month_key) -> verdicts
        self.payments: set = set()
        self.statements: list = []

    # -- helpers for tests
    def add_key(self, key, payer_ref=None, email="buyer@example.com", customer=None):
        self.api_keys[key] = {"payer_ref": payer_ref, "email": email}
        if payer_ref:
            self.accounts.setdefault(payer_ref, {"stripe_customer_id": customer, "email": email})

    def add_sub(self, sub_id, payer_ref, tier="ads_pro", quantity=1, active=True):
        self.subs[sub_id] = {"stripe_subscription_id": sub_id, "payer_ref": payer_ref,
                             "tier": tier, "quantity": quantity, "active": active}

    def pool(self):
        return FakePool(self)


class FakePool:
    def __init__(self, db):
        self.db = db

    @asynccontextmanager
    async def _cm(self):
        yield FakeConn(self.db)

    def acquire(self):
        return self._cm()


class FakeConn:
    def __init__(self, db: FakeDB):
        self.db = db

    async def fetchval(self, sql, *a):
        d = self.db
        d.statements.append(sql)
        if "SELECT payer_ref FROM api_keys WHERE key" in sql:
            return (d.api_keys.get(a[0]) or {}).get("payer_ref")
        if "COALESCE(SUM(COALESCE(quantity" in sql:
            return sum((r["quantity"] or 1) for r in d.subs.values()
                       if r["payer_ref"] == a[0] and r["active"] and r["tier"] == a[1])
        if "SELECT verdicts FROM ads_verdict_meter" in sql:
            return d.meter.get((a[0], a[1]))
        if "SELECT stripe_subscription_id FROM billing_subscriptions" in sql:
            for r in d.subs.values():
                if r["payer_ref"] == a[0] and r["tier"] == a[1] and r["active"]:
                    return r["stripe_subscription_id"]
            return None
        return None

    async def fetchrow(self, sql, *a):
        d = self.db
        d.statements.append(sql)
        if "FROM api_keys k LEFT JOIN accounts a" in sql:
            k = d.api_keys.get(a[0])
            if k is None:
                return None
            acc = d.accounts.get(k["payer_ref"]) or {}
            return {"payer_ref": k["payer_ref"],
                    "stripe_customer_id": acc.get("stripe_customer_id"),
                    "email": acc.get("email") or k["email"]}
        if "INSERT INTO ads_verdict_meter" in sql and "RETURNING verdicts" in sql:
            fp, mk, cap = a
            cur = d.meter.get((fp, mk))
            if cur is None:
                d.meter[(fp, mk)] = 1
                return {"verdicts": 1}
            if cur < cap:
                d.meter[(fp, mk)] = cur + 1
                return {"verdicts": cur + 1}
            return None
        if "INSERT INTO billing_payments" in sql:
            if a[0] in d.payments:
                return None
            d.payments.add(a[0])
            return {"stripe_invoice_id": a[0]}
        return None

    async def fetch(self, sql, *a):
        self.db.statements.append(sql)
        return []

    async def execute(self, sql, *a):
        d = self.db
        d.statements.append(sql)
        if "INSERT INTO ads_verdict_meter" in sql:
            d.meter[(a[0], a[1])] = d.meter.get((a[0], a[1]), 0) + 1
        elif "INSERT INTO billing_subscriptions" in sql:
            (sid, cust, tier, did, payer_ref, active, period_end, cape, ref, qty) = a
            row = d.subs.get(sid, {})
            row.update({"stripe_subscription_id": sid, "stripe_customer_id": cust,
                        "tier": tier, "payer_ref": row.get("payer_ref") or payer_ref,
                        "active": active, "current_period_end": period_end,
                        "cancel_at_period_end": cape, "quantity": qty})
            d.subs[sid] = row
        elif "UPDATE accounts SET stripe_customer_id" in sql:
            d.accounts.setdefault(a[1], {})["stripe_customer_id"] = a[0]
        elif "INSERT INTO accounts" in sql:
            d.accounts.setdefault(a[0], {"stripe_customer_id": None, "email": a[1]})
        elif "UPDATE api_keys SET payer_ref" in sql:
            d.api_keys.setdefault(a[1], {"email": None})["payer_ref"] = a[0]
        return "OK"
