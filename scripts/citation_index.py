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
        figs = citable(re.sub(r"[#*`>|-]", " ", text), limit=4)
        if figs:
            out.append({"title": f"spec-fakten/{name}",
                        "url": f"docs/spec-fakten/{name}", "figures": figs})
    return out


def build() -> dict:
    posts, specs = from_feed(), from_spec()
    return {"posts": posts, "specs": specs,
            "counts": {"posts": len(posts), "specs": len(specs),
                       "figures": sum(len(p["figures"]) for p in posts + specs)}}


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
