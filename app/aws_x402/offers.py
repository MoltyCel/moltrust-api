"""What the AWS Marketplace x402 entry point sells, and at what price.

AWS Marketplace takes one static EndpointUrl per Agentic Commerce product, with
no {param} placeholders (docs.aws.amazon.com/marketplace/latest/userguide/
agentic-commerce-products.html, read 20.11.2026). MolTrust's paid routes carry
path parameters (/guard/api/agent/score/{address}). So the product exposes one
URL, GET /aws/x402, and the operation and its argument travel in the query:

    GET /aws/x402?op=agent.score&address=0x…

Prices mirror the USDC x402 prices on moltrust.ch/pricing.html (read
20.11.2026). The USDC routes are not touched; this table only prices the AWS
path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_MARKET_ID = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")


@dataclass(frozen=True)
class Offer:
    op: str
    price_usd: str        # decimal string, at most 8 places (AWS limit)
    param: str            # the one query parameter the operation takes
    pattern: re.Pattern
    upstream_path: str    # moltguard path, {value} replaced by the parameter
    description: str


OFFERS: dict[str, Offer] = {o.op: o for o in (
    Offer("agent.score", "0.01", "address", _ADDRESS,
          "/api/agent/score/{value}", "Trust score of an agent wallet"),
    Offer("agent.detail", "0.01", "address", _ADDRESS,
          "/api/agent/detail/{value}", "Trust score with its components"),
    Offer("sybil.scan", "0.02", "address", _ADDRESS,
          "/api/sybil/scan/{value}", "Sybil cluster scan of a wallet"),
    Offer("market.check", "0.01", "marketId", _MARKET_ID,
          "/api/market/check/{value}", "Prediction-market integrity check"),
    Offer("prediction.integrity", "0.02", "market_id", _MARKET_ID,
          "/prediction/integrity/{value}", "Prediction integrity record"),
    Offer("radar.market", "0.01", "id", _MARKET_ID,
          "/radar/market/{value}", "Market radar entry"),
)}

# Sold on AWS unless AWS_X402_OPS names another set. The market operations stay
# out by default: their data sources carry their own licence terms, which have
# not been checked for resale through a third-party marketplace.
DEFAULT_OPS = ("agent.score", "agent.detail", "sybil.scan")


class OfferError(ValueError):
    """The request names no sellable operation, or its argument is malformed."""


def resolve(op: str | None, args: dict[str, str], enabled: frozenset[str]) -> tuple[Offer, str]:
    """The offer and its validated argument, or OfferError. Nothing is priced
    for a request this refuses, so a buyer never pays for a 400."""
    if not op:
        raise OfferError("missing op")
    offer = OFFERS.get(op)
    if offer is None or op not in enabled:
        raise OfferError(f"unknown op {op!r}")
    value = args.get(offer.param)
    if not isinstance(value, str) or not offer.pattern.match(value):
        raise OfferError(f"{offer.param} is missing or malformed")
    return offer, value
