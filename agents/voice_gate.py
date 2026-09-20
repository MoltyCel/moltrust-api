"""Pre-send scan (a)-(f) for anything this stack posts in LKK's name.

The voice profiles are prose and stay the single source of truth:
`anti-KI-Sprech.md` (negative side) and `my-voice-en.md` (positive side), both
in MoltyCel/moltrust-web, mirrored by workers/content_scout/guardrails.py. The
drafter is handed those files verbatim. This module is the mechanical half: six
checks that either pass or block, so a draft never reaches X on a maybe.

    (a) banned words        anti-KI-Sprech §1/§2 word and phrase list
    (b) structural tells    anti-KI-Sprech §3/§5 sentence patterns
    (c) contrast density    anti-KI-Sprech §3, max one contrast pair per tweet
    (d) opener              no self-reference or product name in the hook (§3)
    (e) link discipline     no link in the hook, exactly one link, in the last part
    (f) substance floor     a concrete number somewhere, every part within 280,
                            and every number traceable to the source when there
                            is one

The coded lists below are a derived subset of the prose docs, not a replacement.
When a rule is added to anti-KI-Sprech.md, mirror it here or the gate goes
quietly blind to it — `docs_fingerprint()` exists so a drift check can see that
the docs moved.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

WEB_DOCS = Path(os.path.expanduser("~/moltstack/workers/content_scout/.webdocs"))
DOC_ANTI_KI = WEB_DOCS / "anti-KI-Sprech.md"
DOC_MY_VOICE_EN = WEB_DOCS / "my-voice-en.md"

# (a) — anti-KI-Sprech §1 (DE) + §2 (EN). Whole words, case-insensitive.
BANNED_WORDS = [
    # EN
    "exactly", "great point", "fascinating", "indeed", "absolutely", "happy to",
    "excited to", "thrilled to", "delighted to", "passionate about",
    "thought leader", "game-changer", "game changer", "cutting-edge",
    "revolutionize", "revolutionise", "disrupt", "unlock", "empower", "leverage",
    "seamless", "holistic", "delve", "dive deep", "at the intersection of",
    "genuinely", "precisely", "simply", "actually", "really", "foster",
    "landscape", "realm", "tapestry", "at the heart of", "importantly",
    "crucially", "notably", "to be clear", "it is worth noting",
    "it is worth stating", "in today's fast-paced world", "in the age of",
    "elevate", "supercharge", "needless to say", "spot on",
    "couldn't agree more", "nails it", "ecosystem",
    # DE
    "genau", "großartig", "faszinierend", "in der tat", "spannend",
    "maßgeschneidert", "nahtlos", "ganzheitlich", "revolutionär",
    "bahnbrechend", "disruptiv", "leidenschaft", "synergie", "mehrwert",
    "eintauchen", "beleuchten", "wirklich", "tatsächlich", "letztlich",
    "navigieren", "im herzen von", "bemerkenswert", "es sei angemerkt",
    "es ist erwähnenswert",
]

# (b) — anti-KI-Sprech §3/§5 structural tells.
STRUCTURAL_TELLS = [
    (r"\bit'?s not (?:just )?\w[\w\s]{0,30}[—–-]\s*it'?s\b", "antithesis: It's not X — it's Y"),
    (r"\bnot (?:just )?\w[\w\s]{0,30},? but (?:rather )?\b", "antithesis: not X but Y"),
    (r"\bnicht \w[\w\s]{0,30},? sondern\b", "Antithese: nicht X, sondern Y"),
    (r"\bwhat \w+ (?:does|provides|matters|means) is\b", "pseudo-cleft: What X does is Y"),
    (r"\b(?:two|three|four) (?:things|properties|reasons|points)\b[^.?!]*[:.]",
     "signpost enumeration"),
    (r"\bthat(?:'s| is) the (?:claim|point|gap|question|problem)\b",
     "demonstrative summary close"),
    (r"\b(?:rather than|instead of) \w+[\w\s]{0,20}[,.]?\s*(?:it|they|this) \w+s\b",
     "X-rather-than-Y cadence"),
    (r"^\s*(?:ever wonder|ever wondered|what if)\b", "rhetorical-question opener"),
    (r"[.!?]\s+(?:and )?now there (?:is|'s)\b", "coda fragment"),
    (r"\bthe (?:through-line|common thread)\b", "vague coherence claim"),
]

# (c) — contrast markers; more than one in a single part is the §3 density tell.
CONTRAST_MARKERS = [
    r"\bnot\b[^.?!]{0,40}\bbut\b", r"\brather than\b", r"\binstead of\b",
    r"\bwhereas\b", r"\bwhile\b[^.?!]{0,40}\bis\b", r"\bversus\b", r"\bvs\.?\b",
]

# (d) — a hook may not open on us.
SELF_OPENERS = [
    r"^\s*we\b", r"^\s*our\b", r"^\s*wir\b", r"^\s*unser", r"^\s*moltrust\b",
    r"^\s*moltguard\b", r"^\s*introducing\b", r"^\s*announcing\b",
    r"^\s*i'?m (?:excited|proud|happy)\b",
]

URL_RE = re.compile(r"https?://\S+")
NUMBER_RE = re.compile(r"(?<![\w$])[\$€]?\d[\d,.]*\s*(?:%|k|m|bn|b|usdc|usd|eur)?\b", re.I)
TWEET_LIMIT = 280


def docs_fingerprint() -> dict:
    """sha256 (first 12) of each voice doc, so drift is visible in the report."""
    out = {}
    for name, path in (("anti_ki_sprech", DOC_ANTI_KI), ("my_voice_en", DOC_MY_VOICE_EN)):
        try:
            out[name] = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        except OSError:
            out[name] = "missing"
    return out


def load_voice_docs() -> dict:
    """The prose profiles, for the drafting prompt. Missing files are marked, not faked."""
    out = {}
    for name, path in (("anti_ki_sprech", DOC_ANTI_KI), ("my_voice_en", DOC_MY_VOICE_EN)):
        try:
            out[name] = path.read_text(encoding="utf-8")
        except OSError:
            out[name] = f"[voice doc missing: {path}]"
    return out


def _find_banned(text: str) -> list[str]:
    low = text.lower()
    hits = []
    for term in BANNED_WORDS:
        pattern = r"\b" + re.escape(term).replace(r"\ ", r"\s+") + r"\b"
        if re.search(pattern, low):
            hits.append(term)
    return hits


def _digit_runs(text: str) -> list[str]:
    """Digit groups of three or more, commas and dots removed.

    Three is the floor because two-digit counts ('24h', '70/100') collide with
    everything and would only produce noise.
    """
    stripped = URL_RE.sub(" ", text)
    runs = []
    for m in re.finditer(r"\d[\d.,]*", stripped):
        digits = re.sub(r"[.,]", "", m.group(0))
        if len(digits) >= 3:
            runs.append(digits)
    return runs


def scan(parts: list[str], source_text: str | None = None) -> dict:
    """Run (a)-(f) over a thread. Returns {'ok': bool, 'violations': [...], 'checks': {...}}.

    `parts` is the thread in order; a single post is a one-element list.
    `source_text`, when given, is the article the draft is based on: every
    number of three digits or more in the draft must appear in it, so an
    invented figure is caught before it goes out under LKK's name.
    """
    violations: list[str] = []
    checks: dict[str, str] = {}

    # (a) banned words
    banned = sorted({t for p in parts for t in _find_banned(p)})
    if banned:
        violations.append(f"(a) banned words: {', '.join(banned)}")
    checks["a_banned_words"] = "fail" if banned else "pass"

    # (b) structural tells
    tells = []
    for i, p in enumerate(parts, 1):
        for pattern, label in STRUCTURAL_TELLS:
            if re.search(pattern, p, re.I | re.M):
                tells.append(f"part {i}: {label}")
    if tells:
        violations.append("(b) structural tells: " + "; ".join(tells))
    checks["b_structural_tells"] = "fail" if tells else "pass"

    # (c) contrast-pair density, per part
    dense = []
    for i, p in enumerate(parts, 1):
        n = sum(1 for m in CONTRAST_MARKERS if re.search(m, p, re.I))
        if n > 1:
            dense.append(f"part {i}: {n} contrast pairs")
    if dense:
        violations.append("(c) contrast density: " + "; ".join(dense))
    checks["c_contrast_density"] = "fail" if dense else "pass"

    # (d) opener
    hook = parts[0] if parts else ""
    bad_opener = [p for p in SELF_OPENERS if re.search(p, hook, re.I)]
    if bad_opener:
        violations.append("(d) opener: hook opens on self/product")
    checks["d_opener"] = "fail" if bad_opener else "pass"

    # (e) link discipline. A single post is its own last part, so the
    # "no link in the hook" half only applies once there is more than one.
    link_issues = []
    if len(parts) > 1 and URL_RE.search(hook):
        link_issues.append("hook carries a link")
    total_links = sum(len(URL_RE.findall(p)) for p in parts)
    if total_links != 1:
        link_issues.append(f"{total_links} links in the thread, expected exactly 1")
    elif parts and not URL_RE.search(parts[-1]):
        link_issues.append("the single link is not in the last part")
    if link_issues:
        violations.append("(e) link discipline: " + "; ".join(link_issues))
    checks["e_link_discipline"] = "fail" if link_issues else "pass"

    # (f) substance floor
    substance = []
    if not any(NUMBER_RE.search(p) for p in parts):
        substance.append("no concrete number anywhere in the thread")
    over = [f"part {i} at {len(p)}" for i, p in enumerate(parts, 1) if len(p) > TWEET_LIMIT]
    if over:
        substance.append("over 280 chars: " + ", ".join(over))
    if not parts or not any(p.strip() for p in parts):
        substance.append("empty draft")
    if source_text:
        source_digits = set(_digit_runs(source_text))
        ungrounded = sorted({n for p in parts for n in _digit_runs(p)} - source_digits)
        if ungrounded:
            substance.append("numbers not found in the source: " + ", ".join(ungrounded))
    if substance:
        violations.append("(f) substance floor: " + "; ".join(substance))
    checks["f_substance_floor"] = "fail" if substance else "pass"

    return {"ok": not violations, "violations": violations, "checks": checks,
            "docs": docs_fingerprint()}


def format_report(result: dict) -> str:
    """One compact block for the log and for Telegram."""
    lines = ["Pre-send scan (a)-(f): " + ("PASS" if result["ok"] else "BLOCKED")]
    for key, val in result["checks"].items():
        lines.append(f"  {key}: {val}")
    for v in result["violations"]:
        lines.append(f"  ! {v}")
    docs = result.get("docs", {})
    lines.append(f"  voice docs: anti-KI {docs.get('anti_ki_sprech')} / "
                 f"my-voice-en {docs.get('my_voice_en')}")
    return "\n".join(lines)
