"""Verify-gate. If a draft carries a spec-section ref, hash/crypto mechanic, or a
quantitative claim, flag it. Where an authoritative source is known (AAE on IETF
Datatracker), live-fetch and mark it; otherwise mark UNVERIFIED. UNVERIFIED items
do NOT block queueing (they are surfaced), but they DO block the later manual
publish — that gate is enforced by the human reviewer, per WORKFLOW.md.
"""
import re

import httpx

from . import config

_SECTION = re.compile(r"(§\s?\d+(\.\d+)*|Section\s+\d+|RFC\s?\d+|draft-[a-z0-9-]+|"
                      r"\bJCS\b|RFC\s?8785)")
_CRYPTO = re.compile(r"(SHA-?256|Ed25519|ML-DSA(-\d+)?|Dilithium\d?|skeleton[- ]bind|"
                     r"canonicaliz|proofValue)", re.I)
_QUANT = re.compile(r"(\b\d+(\.\d+)?\s?%|\$\s?\d[\d,]*|\b\d+x\b|\b\d{3,}\b)")

# Known authoritative anchors we can actually check.
AAE_DATATRACKER = "https://datatracker.ietf.org/doc/draft-moltrust-aae/"


def _live(url: str) -> bool:
    try:
        with httpx.Client(timeout=15, follow_redirects=True,
                          headers={"User-Agent": config.USER_AGENT}) as c:
            return c.get(url).status_code == 200
    except Exception:
        return False


def run(draft_md: str) -> list:
    """Return a list of verify_status entries: {claim, status, source, date?}."""
    out, seen = [], set()

    def add(claim, status, source=""):
        key = claim.lower()[:80]
        if key in seen:
            return
        seen.add(key)
        out.append({"claim": claim[:200], "status": status, "source": source})

    for pat, kind in ((_SECTION, "spec-ref"), (_CRYPTO, "crypto-mechanic"),
                      (_QUANT, "quant-claim")):
        for m in pat.finditer(draft_md):
            frag = draft_md[max(0, m.start() - 40):m.end() + 40].replace("\n", " ").strip()
            # AAE section refs are checkable against Datatracker.
            if kind == "spec-ref" and re.search(r"\bAAE\b|draft-moltrust-aae", frag, re.I):
                add(frag, "verified" if _live(AAE_DATATRACKER) else "unverified",
                    AAE_DATATRACKER)
            else:
                add(frag, "unverified", "")

    return out


def summary(entries: list) -> str:
    if not entries:
        return "no spec/hash/quant claims detected"
    v = sum(1 for e in entries if e["status"] == "verified")
    u = len(entries) - v
    return f"{v} verified, {u} UNVERIFIED (block manual publish until resolved)"
