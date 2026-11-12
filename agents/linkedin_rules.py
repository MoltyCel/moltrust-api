"""Rules a LinkedIn draft passes before it reaches Lars (ops CO-LI).

Herkunft: Lars, Issue #1 08.11.2026 (CO4, CO5) und Chat 09.11.2026 (CO7-CO9).
Checked in code, not recommended to the drafter:

  CO4/CO8  the draft hangs on a source (the blog post) whose date is at most
           SOURCE_MAX_DAYS old; no date or an older date is refused, not shown
  CO7      one statement per sentence: at most three sentences per paragraph,
           paragraphs separated by a blank line, at most one number per
           sentence except in the first sentence (the hook)
  CO9      compressed negation: verb + "nothing" ("says nothing", "adds
           nothing", ...) is refused; "doesn't say anything" passes

These run next to agents/voice_gate.py, not inside it: the voice gate's rules
live in the pre-send-scan spec of moltrust-web and cover every channel; these
are the LinkedIn rules Lars set.
"""
from __future__ import annotations

import datetime
import email.utils
import re

SOURCE_MAX_DAYS = 7
MAX_SENTENCES_PER_PARAGRAPH = 3

_URL = re.compile(r"https?://\S+")
# A number is a run of digits, with decimal or thousands separators and an
# optional percent sign. "4.2" and "1,000" count once.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*%?")
# Sentence end: . ! ? followed by whitespace or the end. Decimal points have a
# digit right after them and do not end a sentence.
_SENT_END = re.compile(r"(?<=[.!?])[\"'”’)\]]*(?:\s+|$)")

# CO9: verb + "nothing". The verbs are listed with their inflections, so that
# "for nothing", "almost nothing" and "is nothing" stay out of it.
_NOTHING_VERBS = (
    "say|says|said|do|does|did|mean|means|meant|add|adds|added|change|changes|"
    "changed|prove|proves|proved|tell|tells|told|show|shows|showed|offer|offers|"
    "offered|solve|solves|solved|fix|fixes|fixed|cost|costs|guarantee|guarantees|"
    "guaranteed|buy|buys|bought|settle|settles|settled|know|knows|knew|check|"
    "checks|checked|verify|verifies|verified|cover|covers|covered|protect|"
    "protects|protected|decide|decides|decided|contribute|contributes|contributed")
_COMPRESSED_NEGATION = re.compile(rf"\b(?:{_NOTHING_VERBS})\s+nothing\b", re.I)


def source_date(item: dict) -> datetime.datetime | None:
    """The source's date from the feed (RFC 822 pub_date), or None."""
    raw = (item.get("pub_date") or item.get("source_date") or "").strip()
    if not raw:
        return None
    try:
        d = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            d = datetime.datetime.fromisoformat(raw)
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d


def source_problem(item: dict, now: datetime.datetime | None = None) -> str | None:
    """CO4/CO8: why the source does not qualify, or None if it does."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if not (item.get("link") or item.get("source")):
        return "keine Quelle"
    d = source_date(item)
    if d is None:
        return "Quelle ohne Datum"
    age = now - d
    if age > datetime.timedelta(days=SOURCE_MAX_DAYS):
        return f"Quelle vom {d.date().isoformat()}, älter als {SOURCE_MAX_DAYS} Tage"
    return None


def _sentences(paragraph: str) -> list[str]:
    return [s for s in (x.strip() for x in _SENT_END.split(paragraph)) if s]


def density_problems(text: str) -> list[str]:
    """CO7: one finding per broken rule, empty when the text passes."""
    out = []
    paragraphs = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    first_sentence = True
    for n, para in enumerate(paragraphs, 1):
        lines = [ln for ln in para.splitlines() if ln.strip()]
        if len(lines) > 1:
            out.append(f"CO7 Absatz {n}: Zeilenumbruch ohne Leerzeile")
        prose = _URL.sub("", para).strip()
        if not prose:
            continue
        sents = _sentences(prose)
        if len(sents) > MAX_SENTENCES_PER_PARAGRAPH:
            out.append(f"CO7 Absatz {n}: {len(sents)} Sätze, höchstens "
                       f"{MAX_SENTENCES_PER_PARAGRAPH}")
        for s in sents:
            if not first_sentence and len(_NUMBER.findall(s)) > 1:
                out.append(f"CO7 zwei Zahlen in einem Satz: „{s[:80]}“")
            first_sentence = False
    return out


def negation_problems(text: str) -> list[str]:
    """CO9: every compressed negation, quoted."""
    return [f"CO9 verdichtete Verneinung: „{m.group(0)}“ → „doesn't … anything“"
            for m in _COMPRESSED_NEGATION.finditer(_URL.sub("", text))]


def text_problems(text: str) -> list[str]:
    return density_problems(text) + negation_problems(text)
