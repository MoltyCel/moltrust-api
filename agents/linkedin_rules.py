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

ops CN2 (Lars, Chat 03.11.2026; word limit for the prompts decided 09.11.2026,
moltrust-security#1 issuecomment-6752137172). Five rules, LinkedIn only:

  1  no protocol name, field name or code identifier in the text
  2  at most one technical term, with a half explanation
  3  the first sentence names a person or organisation doing something
  4  the consequence is stated in money, liability or time
  5  at most MAX_WORDS words

Rules 1 and 5 are checked here (cn2_problems): rule 1 against the list in
config/linkedin_names.txt plus the shape of a code identifier, rule 5 by a
count. Rules 2-4 are not machine-checkable; DRAFTER_RULES puts them into every
LinkedIn drafter prompt. A draft that breaks rule 1 or 5 does not reach Lars,
and each refusal is one line in data/linkedin_refusals.jsonl, which the watch
message (agents/watchdog.py, ReplyRadar) counts per rule.
"""
from __future__ import annotations

import datetime
import email.utils
import json
import os
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


# ── ops CN2 ──

MAX_WORDS = 120
NAMES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "config", "linkedin_names.txt")
REFUSALS = "linkedin_refusals.jsonl"
# Window the watch message counts refusals over: the syndicate runs every 30
# minutes and the evergreen path twice a week, so a day shows every run of a
# day and a refusal stays visible until the next watch run after it.
REFUSAL_WINDOW_HOURS = 24

RULE_NAME = "CN2-name"
RULE_WORDS = "CN2-words"

# Code identifiers by shape, whatever their name: snake_case, lowerCamelCase,
# a call like verify(), anything in backticks.
_SNAKE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b")
_CAMEL = re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]*\b")
_CALL = re.compile(r"\b[A-Za-z_][A-Za-z0-9_.]*\(\)")
_BACKTICK = re.compile(r"`[^`\n]+`")


def load_names(path: str | None = None) -> list[str]:
    """The names of config/linkedin_names.txt, comments and blanks dropped.

    A missing list raises: a rule without its list would pass everything.
    """
    with open(path or NAMES_FILE, encoding="utf-8") as f:
        names = [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    if not names:
        raise ValueError(f"{path or NAMES_FILE}: no names")
    return names


def _name_pattern(name: str) -> re.Pattern:
    # Whole word, case-sensitive, optional plural "s". Lookarounds instead of
    # \b, because names like "did:web" or "ERC-8004" end in a non-word char.
    return re.compile(rf"(?<![A-Za-z0-9_:\-]){re.escape(name)}s?(?![A-Za-z0-9_\-])")


def name_problems(text: str, names: list[str] | None = None) -> list[str]:
    """CN2 rule 1: every listed name and every code identifier, quoted once."""
    prose = _URL.sub("", text)
    found: list[str] = []
    for name in names if names is not None else load_names():
        m = _name_pattern(name).search(prose)
        if m:
            found.append(m.group(0))
    for rx in (_BACKTICK, _CALL, _SNAKE, _CAMEL):
        found += [m.group(0) for m in rx.finditer(prose)]
    seen, out = set(), []
    for f in found:
        if f not in seen:
            seen.add(f)
            out.append(f"{RULE_NAME} Protokoll-, Feld- oder Codename: „{f}“")
    return out


def word_count(text: str) -> int:
    """Words of the post without its URL: the URL line is required by the
    prompt and is not prose."""
    return len(_URL.sub("", text).split())


def length_problems(text: str) -> list[str]:
    """CN2 rule 5."""
    n = word_count(text)
    return [f"{RULE_WORDS} {n} Wörter, höchstens {MAX_WORDS}"] if n > MAX_WORDS else []


def cn2_problems(text: str, names: list[str] | None = None) -> list[str]:
    """CN2 rules 1 and 5; empty when the text passes."""
    return name_problems(text, names) + length_problems(text)


def rules_of(problems: list[str]) -> list[str]:
    """The rule codes of a list of findings, each once, in order."""
    out: list[str] = []
    for p in problems:
        code = p.split(" ", 1)[0]
        if code not in out:
            out.append(code)
    return out


def drafter_rules(names: list[str] | None = None) -> str:
    """CN2 rules for a LinkedIn drafter prompt. Rules 2-4 live only here."""
    listed = ", ".join(names if names is not None else load_names())
    return (
        f"LinkedIn rules (these override anything above that conflicts):\n"
        f"- At most {MAX_WORDS} words, the URL not counted.\n"
        "- No protocol name, no field name, no code identifier in the text. "
        f"Never use any of these: {listed}. No snake_case, camelCase, "
        "function calls or backticks.\n"
        "- At most one technical term in the whole post, and give it a half "
        "explanation in plain words.\n"
        "- The first sentence names a person or an organisation and what they "
        "did.\n"
        "- State the consequence in money, liability or time.\n"
    )


def refusals_path() -> str:
    from app import paths
    return paths.data(REFUSALS)


def record_refusal(problems: list[str], where: str, key: str | None = None,
                   now: datetime.datetime | None = None) -> None:
    """One line per refused draft, read by refusal_counts()."""
    from app import paths
    now = now or datetime.datetime.now(datetime.timezone.utc)
    row = {"at": now.isoformat(), "rules": rules_of(problems), "where": where,
           "key": key, "problems": problems}
    path = paths.ensure(refusals_path())
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def refusal_counts(now: datetime.datetime,
                   hours: float = REFUSAL_WINDOW_HOURS) -> dict[str, int]:
    """Refusals per rule within the window. A missing file is zero refusals:
    the file is written on the first refusal, not before."""
    since = now - datetime.timedelta(hours=hours)
    out: dict[str, int] = {}
    try:
        f = open(refusals_path(), encoding="utf-8")
    except FileNotFoundError:
        return out
    with f:
        for line in f:
            try:
                row = json.loads(line)
                at = datetime.datetime.fromisoformat(row["at"])
            except (ValueError, KeyError, TypeError):
                continue
            if at.tzinfo is None:
                at = at.replace(tzinfo=datetime.timezone.utc)
            if at < since:
                continue
            for rule in row.get("rules") or []:
                out[rule] = out.get(rule, 0) + 1
    return out


def refusal_line(now: datetime.datetime) -> str:
    """"LinkedIn CN2 24 h: CN2-name 1 · CN2-words 2", or "… keine"."""
    counts = refusal_counts(now)
    head = f"LinkedIn CN2 {REFUSAL_WINDOW_HOURS} h"
    if not counts:
        return f"{head}: keine"
    return f"{head}: " + " · ".join(f"{k} {v}" for k, v in sorted(counts.items()))
