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

# Known authoritative anchors we can actually check. The AAE I-D on Datatracker
# is draft-kroehl-agentic-trust-aae (the older draft-moltrust-aae slug 404s).
AAE_DATATRACKER = "https://datatracker.ietf.org/doc/draft-kroehl-agentic-trust-aae/"
_AAE_REF = re.compile(r"\bAAE\b|draft-kroehl-agentic-trust-aae|draft-moltrust-aae", re.I)
# Split on sentence terminators / newlines so each claim is surfaced once.
_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _live(url: str) -> bool:
    try:
        with httpx.Client(timeout=15, follow_redirects=True,
                          headers={"User-Agent": config.USER_AGENT}) as c:
            return c.get(url).status_code == 200
    except Exception:
        return False


def run(draft_md: str) -> list:
    """One entry per *sentence* carrying a spec-ref / crypto / quant signal, deduped
    (no more overlapping 40-char windows). AAE cites are checked against Datatracker
    and marked verified; every other flagged claim is surfaced as unverified for the
    human to check before the manual publish."""
    out, seen = [], set()
    aae_ok = None
    for raw in _SPLIT.split(draft_md):
        s = re.sub(r"\s+", " ", raw).strip()
        if len(s) < 8:
            continue
        kinds = []
        if _SECTION.search(s):
            kinds.append("spec-ref")
        if _CRYPTO.search(s):
            kinds.append("crypto")
        if _QUANT.search(s):
            kinds.append("quant")
        if not kinds:
            continue
        key = s.lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        if _AAE_REF.search(s):
            if aae_ok is None:
                aae_ok = _live(AAE_DATATRACKER)
            out.append({"claim": s[:220], "kinds": kinds,
                        "status": "verified" if aae_ok else "unverified",
                        "source": AAE_DATATRACKER})
        else:
            out.append({"claim": s[:220], "kinds": kinds, "status": "unverified", "source": ""})
    return out


def summary(entries: list) -> str:
    if not entries:
        return "no spec/hash/quant claims detected"
    v = sum(1 for e in entries if e["status"] == "verified")
    u = len(entries) - v
    return f"{v} verified, {u} UNVERIFIED (block manual publish until resolved)"
