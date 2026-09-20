#!/usr/bin/env python3
"""Recompute every anchored batch against the root in its own transaction.

Two things can go wrong after an anchor, and they look different:

  - a record's fields change, so its leaf no longer follows from it. The proof
    still replays, because the proof is about the leaf it was issued for. Only
    the link breaks. This is what happened on 2026-04-20.
  - a proof is wrong, so it does not walk back to the root at all. That was the
    padding bug in merkle_proof.

Neither shows up in anchor_status, which is why this runs weekly and reports to
Telegram rather than waiting to be asked.

    python3 scripts/anchor_audit.py            # report
    python3 scripts/anchor_audit.py --quiet    # only speak up on drift
"""
import argparse, hashlib, json, os, sys
import asyncio
import asyncpg
from web3 import Web3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.provenance.anchor import compute_leaf, merkle_root, merkle_proof  # noqa: E402

IPR_PREFIX = "MolTrust/IPR/v1/"
VC_PREFIX = "MolTrust/VC/v1/"


def _as_dict(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return None
    return v if isinstance(v, dict) else None


def _replay(leaf_hex, siblings):
    cur = bytes.fromhex(leaf_hex)
    for st in siblings:
        sib = bytes.fromhex(st["hash"])
        cur = hashlib.sha256(sib + cur if st.get("position") == "left" else cur + sib).digest()
    return cur.hex()


def _chain_root(w3, tx, prefix):
    try:
        data = w3.to_text(w3.eth.get_transaction(tx)["input"])
    except Exception:
        return None
    return data[len(prefix):] if data.startswith(prefix) else None


async def audit_iprs(conn, w3):
    # A mismatch that is already marked integrity_mismatch is a recorded fact,
    # not news. Counting it as drift every week is how a weekly alert becomes
    # something people scroll past.
    out = {"kind": "IPR", "batches": 0, "records": 0, "new_root_mismatch": 0,
           "known_mismatch": 0, "leaf_drift": 0, "proof_broken": 0,
           "chain_unreadable": 0, "batches_affected": []}
    txs = [r["anchor_tx"] for r in await conn.fetch(
        "SELECT DISTINCT anchor_tx FROM interaction_proof_records "
        "WHERE anchor_status='anchored' AND anchor_tx IS NOT NULL ORDER BY 1")]
    for tx in txs:
        rows = await conn.fetch(
            "SELECT id, agent_did, output_hash, produced_at, confidence, merkle_proof, integrity_status "
            "FROM interaction_proof_records WHERE anchor_tx=$1 ORDER BY created_at ASC", tx)
        recs = [dict(r) for r in rows]
        out["batches"] += 1
        out["records"] += len(recs)
        leaves = [compute_leaf(r["output_hash"], r["agent_did"],
                  r["produced_at"] if isinstance(r["produced_at"], str) else r["produced_at"].isoformat(),
                  r["confidence"]) for r in recs]
        root = merkle_root([bytes.fromhex(h) for h in leaves]).hex()
        chain = _chain_root(w3, tx, IPR_PREFIX)
        if chain is None:
            out["chain_unreadable"] += len(recs)
            continue
        # A batch already known to be broken is counted, not re-reported.
        known = sum(1 for r in recs if r["integrity_status"])
        if chain != root:
            unmarked = len(recs) - known
            out["known_mismatch"] += known
            out["new_root_mismatch"] += unmarked
            if unmarked:
                out["batches_affected"].append({"tx": tx, "records": len(recs), "unmarked": unmarked})
            continue
        for i, r in enumerate(recs):
            d = _as_dict(r["merkle_proof"]) or {}
            if d.get("leaf") and d["leaf"] != leaves[i]:
                out["leaf_drift"] += 1
            sib = d.get("siblings") or d.get("path")
            if not isinstance(sib, list) or _replay(leaves[i], sib) != root:
                out["proof_broken"] += 1
    return out


async def audit_credentials(conn, w3):
    out = {"kind": "VC", "batches": 0, "records": 0, "new_root_mismatch": 0,
           "known_mismatch": 0, "proof_broken": 0, "chain_unreadable": 0, "batches_affected": []}
    txs = [r["tx_hash"] for r in await conn.fetch(
        "SELECT DISTINCT tx_hash FROM credential_anchors ORDER BY 1")]
    for tx in txs:
        rows = await conn.fetch(
            """SELECT c.id, c.subject_did, c.credential_type, c.issued_at, c.proof_value,
                      a.merkle_proof, a.merkle_root
                 FROM credential_anchors a JOIN credentials c ON c.id = a.credential_id
                WHERE a.tx_hash = $1 ORDER BY c.issued_at ASC""", tx)
        recs = [dict(r) for r in rows]
        out["batches"] += 1
        out["records"] += len(recs)
        chain = _chain_root(w3, tx, VC_PREFIX)
        if chain is None:
            out["chain_unreadable"] += len(recs)
            continue
        stored_root = recs[0]["merkle_root"] if recs else None
        if stored_root != chain:
            out["new_root_mismatch"] += len(recs)
            out["batches_affected"].append({"tx": tx, "records": len(recs)})
            continue
        for r in recs:
            d = _as_dict(r["merkle_proof"]) or {}
            sib = d.get("path") or d.get("siblings")
            if not d.get("leaf") or not isinstance(sib, list) or _replay(d["leaf"], sib) != chain:
                out["proof_broken"] += 1
    return out


def telegram(text):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("telegram: no token/chat, skipped")
        return
    import httpx
    try:
        httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                   data={"chat_id": chat, "text": text}, timeout=20)
    except Exception as e:
        print(f"telegram failed: {type(e).__name__}")


async def main(quiet):
    w3 = Web3(Web3.HTTPProvider(os.getenv("BASE_RPC", "https://mainnet.base.org")))
    conn = await asyncpg.connect(host="localhost", user="moltstack", database="moltstack")
    reports = [await audit_iprs(conn, w3), await audit_credentials(conn, w3)]
    await conn.close()

    drift = [r for r in reports
             if r["new_root_mismatch"] or r["proof_broken"] or r.get("leaf_drift") or r["chain_unreadable"]]
    print(json.dumps(reports, indent=1))

    if drift:
        lines = ["MolTrust — Anchor-Audit: Abweichung", ""]
        for r in drift:
            lines.append(f"{r['kind']}: {r['records']} Datensaetze in {r['batches']} Batches")
            if r["new_root_mismatch"]:
                lines.append(f"  Wurzel != Kette : {r['new_root_mismatch']} (neu)")
            if r.get("leaf_drift"):
                lines.append(f"  Blatt gedriftet : {r['leaf_drift']}")
            if r["proof_broken"]:
                lines.append(f"  Proof kaputt    : {r['proof_broken']}")
            if r["chain_unreadable"]:
                lines.append(f"  Kette unlesbar  : {r['chain_unreadable']}")
            for b in r["batches_affected"][:5]:
                lines.append(f"  {b['tx'][:20]}… {b.get('unmarked', b['records'])} unmarkiert")
        lines.append("")
        lines.append("Ein gedriftetes Blatt heisst: ein Datensatz wurde nach seiner")
        lines.append("Verankerung geaendert. Der Proof kann dabei intakt bleiben.")
        telegram("\n".join(lines))
        return 1

    if not quiet:
        tot = sum(r["records"] for r in reports)
        known = sum(r["known_mismatch"] for r in reports)
        note = f", {known} bekannte Abweichungen unveraendert" if known else ""
        telegram(f"MolTrust — Anchor-Audit: {tot} Datensaetze gegen die Kette geprueft, "
                 f"keine neue Abweichung{note}.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="only report on drift")
    raise SystemExit(asyncio.run(main(ap.parse_args().quiet)))
