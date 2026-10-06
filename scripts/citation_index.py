"""What we can actually cite: title, URL, and the figures that are on the page.

The drafter's knowledge base is four site pages plus the six newest blog posts,
excerpted at 1200 characters each. There are sixty-nine posts. So when it needs
a figure it reaches into its own memory — "EU AI Act Article 12" recurs across
the drafts gate 2 (h) blocked on 2026-10-04, recalled rather than read.

This builds the index that closes that gap: for every post and every pinned
spec fact, the title, the URL, and **the sentences that carry a number or a
named specification, verbatim**. Verbatim matters: gate (h) checks that a claim
appears in a page the draft named, so an index that paraphrased would hand the
drafter figures it cannot then stand behind.

Sources, both local so this costs nothing and is exact:

    moltrust-web/blog/feed.xml  + the post files beside it
    moltrust-api/docs/spec-fakten/*.md

    python3 scripts/citation_index.py            # the prompt block
    python3 scripts/citation_index.py --json     # the structured form
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Filled by from_spec(); build() reports it so an exclusion is visible rather
# than inferred from a missing entry.
SKIPPED: list[dict] = []

WEB = os.path.expanduser("~/moltrust-web")
SPEC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "docs", "spec-fakten")

# A sentence is citable when it carries something checkable: a figure, a
# percentage, a money amount, or a named specification. "Agents are the future"
# is not a source for anything.
FIGURE = re.compile(
    r"(?:\b\d[\d,.]*\s?(?:%|percent|USD|\$|EUR|k\b|m\b|million|billion)"
    r"|\$\s?\d"
    r"|\b\d{1,3}(?:,\d{3})+\b"
    r"|\b(?:ERC-?\d+|RFC\s?\d+|CVE-\d{4}-\d+|EIP-?\d+|SOC\s?2|ISO\s?\d+)"
    r"|\bArticle\s+\d+\b"
    r"|\b\d+\s+(?:of|von)\s+\d+\b"
    r"|\b\d{2,}\b)")
# Chrome that carries digits and says nothing.
NOISE = re.compile(r"(?:nav|menu|cookie|©|all rights reserved|min read"
                   r"|skip to|copyright|\bv\d+\.\d+\.\d+\b)", re.I)
MAX_PER_PAGE = 3
MAX_SENTENCE = 220


# ── what must never become a citation ───────────────────────────────────────
#
# CLAUDE.md, SPEC-FAKTEN-PIN: the local superseded working revision was
# content-identical to the published -00 and is no longer a source; the
# candidate input log in docs/spec-fakten/ says of itself that it is "not a
# citation source" and is marked UNVERIFIED BY DESIGN in the README. ADR-0002
# adds the other shape: a third party's text referenced as an occasion, "nicht
# zitiert", because it has not been read.
#
# Until this guard existed, that candidate log was indexed like any other file
# in docs/spec-fakten/ — the drafter was handed unverified candidate material
# as something it could point at.
#
# The reserved identifiers are assembled from parts, the same way
# .github/scripts/reserved_names_guard.py does it, so this file does not carry
# what it exists to keep out.
_A, _DR, _N = "a" + "ae", "dr" + "aft", "0" + "4"

EXCLUDE_PATH = (
    (re.compile(r"kandidaten|candidates?[-_]log|-%s-" % _N, re.I),
     "candidate log / superseded-revision input material, not a citation source"),
)
EXCLUDE_TEXT = (
    (re.compile(r"\b%s-%s\b|%s-[\w-]+-%s\b|revision\s+-?%s\b"
                % (_A, _N, _DR, _N, _N), re.I),
     "refers to the superseded revision"),
    (re.compile(r"UNVERIFIED BY DESIGN|not a citation source|"
                r"KEINE? Zitierquelle|NIE Quelle|never a citation source", re.I),
     "the file says of itself that it is not a source"),
    (re.compile(r"\bunver(?:ö|oe)ffentlicht|\bunpublished (?:draft|revision)|"
                r"\bpre-?print\b|\bnicht ver(?:ö|oe)ffentlicht", re.I),
     "unpublished revision"),
    (re.compile(r"\b(?:E-?Mail|Mail|DM|Nachricht|correspondence|Korrespondenz)\b"
                r"[^\n]{0,40}(?:von|from|an|to)\s+[A-Z]", re.I),
     "verbatim third-party correspondence"),
)


def excluded(path: str, text: str) -> str | None:
    """The reason this file must not be indexed, or None.

    Path first: a candidate log is excluded by what it is, before anything is
    read out of it.
    """
    name = os.path.basename(path)
    for pat, why in EXCLUDE_PATH:
        if pat.search(name):
            return why
    for pat, why in EXCLUDE_TEXT:
        if pat.search(text):
            return why
    return None


def strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def citable(text: str, limit: int = MAX_PER_PAGE) -> list[str]:
    out = []
    for s in sentences(text):
        if len(s) < 40 or len(s) > MAX_SENTENCE:
            continue
        if NOISE.search(s) or not FIGURE.search(s):
            continue
        out.append(s)
        if len(out) >= limit:
            break
    return out


def from_feed() -> list[dict]:
    feed = os.path.join(WEB, "blog", "feed.xml")
    try:
        xml = open(feed, errors="replace").read()
    except OSError as e:
        print(f"feed nicht lesbar: {e}", file=sys.stderr)
        return []
    out = []
    for item in re.findall(r"(?s)<item>(.*?)</item>", xml):
        link = (re.search(r"<link>\s*([^<\s]+)\s*</link>", item) or [None, ""])[1]
        title = (re.search(r"(?s)<title>(.*?)</title>", item) or [None, ""])[1]
        if not link.endswith(".html"):
            continue
        page = os.path.join(WEB, "blog", link.rsplit("/", 1)[-1])
        try:
            body = strip_html(open(page, errors="replace").read())
        except OSError:
            continue
        figs = citable(body)
        if not figs:
            # No figure, no entry. A title alone is not a citation, and listing
            # it would invite exactly the move this index exists to stop.
            continue
        out.append({"title": html.unescape(title).strip(), "url": link,
                    "figures": figs})
    return out


def from_spec() -> list[dict]:
    out = []
    SKIPPED.clear()
    try:
        names = sorted(os.listdir(SPEC))
    except OSError as e:
        # A missing pinned-facts directory degrades the index; it must not take
        # the index down. The blog half still carries most of the figures.
        print(f"spec-fakten nicht lesbar: {type(e).__name__}", file=sys.stderr)
        return out
    for name in names:
        if not name.endswith(".md") or name == "README.md":
            continue
        try:
            text = open(os.path.join(SPEC, name), errors="replace").read()
        except OSError:
            continue
        why = excluded(name, text)
        if why:
            SKIPPED.append({"source": f"docs/spec-fakten/{name}", "reason": why})
            continue
        figs = citable(re.sub(r"[#*`>|-]", " ", text), limit=4)
        if figs:
            out.append({"title": f"spec-fakten/{name}",
                        "url": f"docs/spec-fakten/{name}", "figures": figs,
                        "source": f"docs/spec-fakten/{name}"})
    return out


def build() -> dict:
    posts, specs = from_feed(), from_spec()
    return {"posts": posts, "specs": specs, "excluded": list(SKIPPED),
            "counts": {"posts": len(posts), "specs": len(specs),
                       "excluded": len(SKIPPED),
                       "figures": sum(len(p["figures"]) for p in posts + specs)}}


def excluded_sources() -> dict[str, str]:
    """{identifier: reason} for everything the index refused.

    The identifiers are what a draft would name if it cited the file — the
    repo path and the bare filename — so a draft can be checked against them
    without the index being rebuilt.
    """
    out = {}
    for e in SKIPPED or build().get("excluded", []):
        out[e["source"]] = e["reason"]
        out[os.path.basename(e["source"])] = e["reason"]
        out[os.path.splitext(os.path.basename(e["source"]))[0]] = e["reason"]
    return out


def cites_excluded(text: str) -> list[tuple[str, str]]:
    """Which forbidden sources a draft names. Empty is the normal case.

    Two legs, because the first one alone is only as good as today's file list.
    A draft that names the candidate log still has to be blocked after the file
    is deleted — and it was deleted on 2026-10-06, while this guard was being
    written. The drafter does not read the repo; it recalls, and a name it
    recalls outlives the file.
    """
    hits = []
    # Leg one: the identifier patterns, which hold whether or not the file is
    # still in the tree.
    body = text or ""
    for pat, why in EXCLUDE_PATH + EXCLUDE_TEXT:
        m = pat.search(body)
        if m and len(m.group(0)) >= 6:
            hits.append((m.group(0).strip(), why))
    # Leg two: what this run actually refused, by path and by file name.
    for ident, why in excluded_sources().items():
        if len(ident) < 8:          # too short to be a citation on its own
            continue
        if ident.lower() in (text or "").lower():
            hits.append((ident, why))
    # The longest identifier wins: docs/spec-fakten/x.md and x.md are one hit.
    best = {}
    for ident, why in sorted(hits, key=lambda h: -len(h[0])):
        if not any(ident in seen for seen in best):
            best[ident] = why
    return sorted(best.items())


def as_prompt(idx: dict) -> str:
    """The block the drafter sees. Compact, because it competes for attention."""
    L = ["=== citation index: what we can point at ===",
         "Every line below is on the page named above it, verbatim. Cite the URL",
         "and use the figure as written. If your claim is not in this index and",
         "not in the post you are answering, you have no source and must SKIP.",
         ""]
    for group, label in ((idx["posts"], "our posts"), (idx["specs"], "pinned spec facts")):
        if not group:
            continue
        L.append(f"-- {label} --")
        for e in group:
            L.append(f"{e['title']}  <{e['url']}>")
            for f in e["figures"]:
                L.append(f"   · {f}")
        L.append("")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    idx = build()
    if a.json:
        print(json.dumps(idx, indent=1, ensure_ascii=False))
        return 0
    block = as_prompt(idx)
    print(block)
    print(f"\n[{idx['counts']['posts']} Posts, {idx['counts']['specs']} Spec-Seiten, "
          f"{idx['counts']['figures']} Zahlen, {len(block)} Zeichen]",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
