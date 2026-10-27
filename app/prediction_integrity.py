"""Free evidence route for prediction-market integrity: /prediction/integrity/{market_id}.

The ERC-8004 adapter (app/erc8004_reputation.py) writes `moltrust:prediction`
feedback with a feedbackURI under this path and a feedbackHash over the bytes
served there. Until this route existed the URI answered 404 (ops MG-URI). The
MoltGuard service computes the same figures at /guard/prediction/integrity/,
but that route sits behind x402 and a reader of the chain cannot follow it
without paying; the free MoltGuard check stamps every answer with the fetch
time and allows one call per ten minutes per IP. Neither lets a verifier
recompute the hash.

This route reads the same two MoltGuard tables (prediction_market_events,
prediction_wallets) and answers without payment. The body is deterministic:
no fetch time, a total order on the participant rows, sorted keys and fixed
separators. Two fetches return the same bytes, and therefore the same SHA-256,
for as long as the underlying rows do not change.

Only Polymarket wallets are read, the platform the integrity document was
built for.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Path, Response

router = APIRouter()

PLATFORM = "polymarket"
MAX_PARTICIPANTS = 20
TOP_PARTICIPANTS = 10
DOCUMENT_VERSION = 1

# Same selection as MoltGuard's integrity check, plus a tie-break on every
# selected column so the row order, and with it the hash, cannot vary between
# two reads of the same data.
_QUERY = """
SELECT pw.address, pw.prediction_score, pw.linked_did, pme.position, pme.amount_in
  FROM prediction_market_events pme
  JOIN prediction_wallets pw ON pw.address = pme.wallet_address
 WHERE pme.market_id = $1 AND pw.platform = $2
 ORDER BY pw.prediction_score DESC, pw.address ASC,
          pme.position ASC NULLS LAST, pme.amount_in ASC NULLS LAST
 LIMIT $3
"""


def _pool():
    from app.main import db_pool
    if db_pool is None:
        raise HTTPException(503, "Database unavailable")
    return db_pool


def _amount(v):
    return None if v is None else float(v)


def build_document(market_id: str, rows: list[dict]) -> dict:
    """The integrity document for one market, from rows ordered as in _QUERY."""
    total = len(rows)
    yes = sum(1 for r in rows if r["position"] in ("yes", "buy"))
    no = sum(1 for r in rows if r["position"] in ("no", "sell"))
    avg = round(sum(int(r["prediction_score"] or 0) for r in rows) / total) if total else 0
    return {
        "documentVersion": DOCUMENT_VERSION,
        "marketId": market_id,
        "platform": PLATFORM,
        "trackedWallets": total,
        "verifiedWallets": sum(1 for r in rows if r["linked_did"]),
        "averagePredictionScore": avg,
        "positionBreakdown": {"yes": yes, "no": no},
        # Herding: more than five wallets and over 80% of them on one side.
        "herdingDetected": total > 5 and (yes / total > 0.8 or no / total > 0.8),
        "topParticipants": [
            {
                "address": r["address"],
                "did": r["linked_did"],
                "predictionScore": r["prediction_score"],
                "position": r["position"],
                "amountIn": _amount(r["amount_in"]),
            }
            for r in rows[:TOP_PARTICIPANTS]
        ],
    }


def serialize(doc: dict) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


async def fetch_rows(market_id: str) -> list[dict]:
    async with _pool().acquire() as conn:
        records = await conn.fetch(_QUERY, market_id, PLATFORM, MAX_PARTICIPANTS)
    return [dict(r) for r in records]


@router.get("/prediction/integrity/{market_id}", tags=["Prediction"],
            summary="Prediction-market integrity for one market, free and hash-stable "
                    "(evidence document for moltrust:prediction ERC-8004 feedback)")
async def prediction_integrity(market_id: str = Path(..., min_length=1, max_length=256)) -> Response:
    try:
        rows = await fetch_rows(market_id)
    except HTTPException:
        raise
    except Exception:
        # An error must not be served as an empty document: its hash would look
        # like valid evidence of a market with no participants.
        raise HTTPException(503, "Prediction data unavailable")
    return Response(content=serialize(build_document(market_id, rows)),
                    media_type="application/json",
                    headers={"Cache-Control": "no-cache"})
