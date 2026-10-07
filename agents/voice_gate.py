"""Pre-send scan for anything this stack posts in LKK's name.

The rules are not in this file. They live in `docs/pre-send-scan.md` in
MoltyCel/moltrust-web, as yaml blocks next to the prose that explains them, and
this module parses them at runtime out of the shallow clone that
workers/content_scout/guardrails.py keeps current. Gate 2's word lists come
straight out of `anti-KI-Sprech.md` §1/§2 for the same reason: a copy drifts.

Two gates, both blocking:

    Gate 1  every sentence, in all three positions (opener, middle, coda) —
            counterpoint, validation opener, parallel negation, identity coda,
            evaluative copula, judgement filler without evidence, plus
            superlative chains, empty antithesis, rhetorical opener, triad into
            a question, and fragment codas.
    Gate 2  the post as a whole — banned words, structural tells, contrast
            density, opener, link discipline, substance floor, (g) every number
            of three digits or more traceable to the source, and (h) in reply
            mode, every checkable-looking claim traceable to a document the
            draft itself cited and the run actually fetched.

A blocked draft goes to Telegram, never silently softened.

Which rules run is decided per mode, by the positive list `mode_rules` in the
same spec (since 2026-10-07). A rule is loaded in a mode or it is not; there is
no "not applicable" result. Gate 1 (a)–(f) read only text that is not quoted
(blockquote lines), and rule (q) caps what a quote may carry.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path

import yaml
from app import gh

log = logging.getLogger("voice_gate")

WEB_DOCS = Path(os.path.expanduser("~/moltstack/workers/content_scout/.webdocs"))
DOC_SCAN = WEB_DOCS / "docs" / "pre-send-scan.md"
DOC_ANTI_KI = WEB_DOCS / "anti-KI-Sprech.md"
DOC_MY_VOICE_EN = WEB_DOCS / "my-voice-en.md"

URL_RE = re.compile(r"https?://\S+")
# The character class after the terminator is what lets a sentence ending in a
# quotation break at all. Without it, `." ` is not a boundary and the sentence
# merges with the next one, so any rule anchored at sentence_start reads the
# wrong opener and any rule that quotes a sentence quotes the wrong one.
SENT_SPLIT = re.compile(r"(?<=[.!?])[\"'\u201d\u2019\)\]]*[\s\n]+|\n{2,}")

# Models emit typographic punctuation, the rules are written with the plain
# forms, and `that’s` then slips a pattern that matches `that's`. Every rule
# sees the normalised text; the draft that goes out keeps its own punctuation.
PUNCT_MAP = str.maketrans({"’": "'", "‘": "'", "‛": "'", "´": "'", "`": "'",
                           "“": '"', "”": '"', "„": '"', "‟": '"',
                           " ": " ", " ": " ", " ": " "})
TWEET_LIMIT = 280
CONTRAST_FALLBACK = 2


# ── Loading the rules ──

def refresh_docs() -> None:
    """Pull moltrust-web main into the shallow clone, so a rule edited there
    takes effect on the next run. Silent on failure — a stale mirror still
    scans, and docs_fingerprint() shows which version ran."""
    try:
        from workers.content_scout import config as cs_config
        from workers.content_scout import guardrails
        token = gh.token()
        if not token:
            from app import notices
            msg = f"{gh.NAME} fehlt — der Spiegel kann nicht nachgezogen werden"
            log.warning(msg)
            notices.note("docs/mirror/fetch", msg)
            return
        guardrails.ensure_web_docs(token)
    except Exception as e:  # noqa: BLE001 — the scan still runs on the mirror
        # Best-effort for the scan, not for the record. Until 2026-10-05 this
        # warning was the only trace: when the token was revoked, refresh_docs
        # returned cleanly, the fingerprints stayed on the old commit, and
        # every signal this function produces said health. The finding now goes
        # into the queue the collected report reads.
        # guardrails.ensure_web_docs reports a failed git call itself, and it
        # never raises — so anything arriving here failed *before* that, inside
        # this function. A live probe on 2026-10-05 produced exactly that: a
        # missing import raised NameError, the except turned it into a log line
        # nobody reads, and the mirror question went unanswered a second time.
        log.warning(f"Could not refresh the voice docs mirror: {e}")
        from app import notices
        notices.note("docs/mirror/fetch",
                     f"Spiegel-Abruf brach vor dem git-Aufruf ab: "
                     f"{type(e).__name__}: {e} — das Gate erzwingt weiter "
                     f"den zuletzt geholten Stand")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def parse_spec(text: str) -> tuple[list[dict], dict]:
    """Return (rules, lexicons) from the yaml blocks in pre-send-scan.md."""
    rules, lexicons = [], {}
    for block in re.findall(r"```yaml\n(.*?)```", text, re.S):
        try:
            doc = yaml.safe_load(block)
        except yaml.YAMLError as e:
            log.error(f"Unparseable rule block in pre-send-scan.md: {e}")
            continue
        if not isinstance(doc, dict):
            continue
        if "id" in doc:
            rules.append(doc)
        elif "lexicon" in doc:
            terms = list(doc.get("terms_en") or []) + list(doc.get("terms_de") or [])
            lexicons[doc["lexicon"]] = [str(t).lower() for t in terms]
    return rules, lexicons


def parse_banned_words(anti_ki: str) -> list[str]:
    """Terms from anti-KI-Sprech.md §1 (DE) and §2 (EN).

    Bullet lines carry several terms at once, separated by `·` and `/`, some
    quoted, some behind a label ("Signpost-Wörter: „Wichtig:" · …"), some with a
    parenthetical gloss. Quoted material is taken as-is; the rest is split.
    """
    terms: set[str] = set()
    section = False
    for line in anti_ki.splitlines():
        if re.match(r"^##\s*[12]\s*—", line):
            section = True
            continue
        if line.startswith("## "):
            section = False
        if not section or not line.lstrip().startswith("- "):
            continue

        body = line.lstrip()[2:]
        body = re.sub(r"\([^)]*\)", " ", body)          # drop glosses
        quoted = re.findall(r"[„\"']([^„\"'”]+)[\"'”]", body)
        rest = re.sub(r"[„\"'][^„\"'”]+[\"'”]", " ", body)
        for chunk, from_quote in ([(q, True) for q in quoted]
                                  + [(r, False) for r in re.split(r"[·/]", rest)]):
            t = chunk.strip().strip("…").strip().strip(":").strip()
            t = re.sub(r"\s+", " ", t).lower()
            if not (3 <= len(t) <= 60) or t.endswith(":") or not re.search(r"[a-zäöüß]", t):
                continue
            # Unquoted tails are often the gloss that follows the term
            # ("„Reise" / „journey" als Metapher für Karriere oder Projekt").
            if not from_quote and (len(t.split()) > 5 or t.startswith("als ")):
                continue
            terms.add(t)
    return sorted(terms)


def parse_mode_rules(text: str) -> dict[str, list[str]]:
    """The `mode_rules` block: mode -> the rule ids that mode loads."""
    for block in re.findall(r"```yaml\n(.*?)```", text, re.S):
        try:
            doc = yaml.safe_load(block)
        except yaml.YAMLError:
            continue
        if isinstance(doc, dict) and isinstance(doc.get("mode_rules"), dict):
            return {str(m): [str(r) for r in (ids or [])]
                    for m, ids in doc["mode_rules"].items()}
    return {}


_CACHE: dict = {}


def load_rules(refresh: bool = True) -> dict:
    """Rules, lexicons and banned words, read once per process."""
    if _CACHE:
        return _CACHE
    if refresh:
        refresh_docs()
    spec_text = _read(DOC_SCAN)
    if not spec_text:
        raise RuntimeError(
            f"pre-send-scan.md not found at {DOC_SCAN}. The gate refuses to run "
            "without its rules rather than pass everything.")
    rules, lexicons = parse_spec(spec_text)
    mode_rules = parse_mode_rules(spec_text)
    if not mode_rules:
        # A spec without the per-mode list is the version before 2026-10-07.
        # Running it would mean deciding by negation which rules apply — the
        # silent fallback to an old rule set this check exists to refuse.
        raise RuntimeError(
            f"pre-send-scan.md at {DOC_SCAN} has no mode_rules block. The gate "
            "refuses to run on a spec that does not name its rules per mode.")
    known = {r["id"] for r in rules}
    for mode, ids in mode_rules.items():
        missing = [i for i in ids if i not in known]
        if missing:
            raise RuntimeError(f"mode_rules[{mode}] names unknown rules: {missing}")
    for r in rules:
        declared = r.get("modes")
        if declared:
            listed = {m for m, ids in mode_rules.items() if r["id"] in ids}
            if listed != set(declared):
                raise RuntimeError(
                    f"rule {r['id']}: modes {sorted(declared)} disagree with "
                    f"mode_rules {sorted(listed)}")
    _CACHE.update(rules=rules, lexicons=lexicons, mode_rules=mode_rules,
                  banned=parse_banned_words(_read(DOC_ANTI_KI)))
    return _CACHE


def docs_fingerprint() -> dict:
    out = {}
    for name, path in (("pre_send_scan", DOC_SCAN), ("anti_ki_sprech", DOC_ANTI_KI),
                       ("my_voice_en", DOC_MY_VOICE_EN)):
        data = _read(path)
        out[name] = hashlib.sha256(data.encode()).hexdigest()[:12] if data else "missing"
    return out


def load_voice_docs() -> dict:
    """The prose profiles, for the drafting prompt."""
    return {"anti_ki_sprech": _read(DOC_ANTI_KI) or "[anti-KI-Sprech.md missing]",
            "my_voice_en": _read(DOC_MY_VOICE_EN) or "[my-voice-en.md missing]",
            "pre_send_scan": _read(DOC_SCAN) or "[pre-send-scan.md missing]"}


# ── Text shaping ──

def normalise(text: str) -> str:
    """Fold typographic punctuation to the plain forms the rules are written in."""
    return (text or "").translate(PUNCT_MAP)


def sentences(part: str) -> list[str]:
    """Prose sentences of one tweet. Segments that are only a URL are dropped —
    they are not prose and must not become the opener or the coda."""
    out = []
    for raw in SENT_SPLIT.split(normalise(part)):
        s = raw.strip()
        if not s or not URL_RE.sub("", s).strip():
            continue
        out.append(s)
    return out


def positions_of(idx: int, total: int) -> set[str]:
    pos = set()
    if idx == 0:
        pos.add("opener")
    if idx == total - 1:
        pos.add("coda")
    if not pos:
        pos.add("middle")
    return pos


def _terms_pattern(terms: list[str], anchor_start: bool = False) -> re.Pattern | None:
    if not terms:
        return None
    alts = "|".join(re.escape(t).replace(r"\ ", r"\s+") for t in sorted(terms, key=len, reverse=True))
    prefix = r"^\s*(?:" if anchor_start else r"\b(?:"
    return re.compile(prefix + alts + r")\b", re.I)


# Fallback only. The list that counts is `imperative_verbs` in
# docs/pre-send-scan.md; this is what the gate falls back to when a spec
# predating that lexicon is loaded, so an old spec does not silently turn the
# imperative check off.
IMPERATIVE_HINTS = {
    "check", "verify", "run", "read", "compare", "recompute", "replay", "try",
    "open", "post", "file", "report", "measure", "test", "start", "see",
    "pruef", "prüfe", "prüft", "vergleich", "lies", "starte", "melde", "miss",
}


def _imperatives(lex: dict, name: str = "imperative_verbs") -> set[str]:
    return set(lex.get(name) or ()) or IMPERATIVE_HINTS


def _opens_imperative(sentence: str, lex: dict, name: str = "imperative_verbs") -> bool:
    """True when the sentence opens on a base-form verb.

    Position carries the decision. "Sign the voucher per call." is a sentence;
    "Voucher sign per call." is not, and a list searched anywhere in the
    sentence could not tell the two apart.
    """
    m = re.match(r"\s*([A-Za-zÄÖÜäöüß]+)", sentence)
    return bool(m and m.group(1).lower() in _imperatives(lex, name))


# A token and whether a comma follows it, which is what tells a list from a
# predicate: "Two registrations, no calls." against "The proof ends at the
# rack you operate."
_TOKEN_RE = re.compile(r"([\w’']+)(\s*,)?")


def _has_finite_verb(sentence: str, rule: dict, lex: dict) -> bool:
    """Whether a short coda is a clause rather than a noun phrase.

    Form first, list second. The list fell behind the language three times —
    `made` on 21.09, an imperative on 23.09, `ends` and `answers` on 01.10 —
    and each time the answer was to make the list longer.

    A verb candidate is, in order:

      1. the first word, when it is an imperative. "Sign the voucher per call."
         carries a finite verb; it just stands first and in the base form.
      2. a word ending in -ed/-en/-s/-es that is neither the first nor the last
         word and has no comma directly behind it. Position is the signal:
         `ends` sits mid-sentence with something after it, while `numbers` in
         "Just numbers." sits at the end with nothing after it — which is why
         the noun phrase stays blocked despite ending in -s.
      3. a word in `verb_hints`, the fallback for what the shape cannot see.
         "The agent held none." has no -ed/-en/-s word at all.
    """
    if _opens_imperative(sentence, lex,
                         rule.get("imperative_lexicon", "imperative_verbs")):
        return True

    matches = list(_TOKEN_RE.finditer(sentence))
    words = [m.group(1).lower() for m in matches]
    if not words:
        return False

    suffixes = tuple(str(x) for x in (rule.get("verb_suffixes")
                                      or ["ed", "en", "es", "s"]))
    for i, (word, m) in enumerate(zip(words, matches)):
        if i == 0 or i == len(words) - 1:
            continue                      # nothing before it, or nothing after
        if m.group(2):
            continue                      # a comma behind it: a list, not a verb
        if word.endswith(suffixes):
            return True

    hints = set(lex.get(rule.get("lexicon", ""), []))
    return any(w in hints for w in words)


def _has_evidence(sentence: str) -> bool:
    """A number, a quotation or a source in the same sentence counts as a belt."""
    return bool(re.search(r"\d", sentence) or URL_RE.search(sentence)
                or re.search(r"[\"„“”']", sentence))


# ── Quotes and articles ──

COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
BLOCKQUOTE_HTML_RE = re.compile(r"<blockquote\b[^>]*>(.*?)</blockquote>", re.S | re.I)
# Gate 1 (a)–(f) read text that is not quoted. Everything else reads all of it.
QUOTE_EXEMPT = {"g1a", "g1b", "g1c", "g1d", "g1e", "g1f"}


def _is_quote_line(line: str) -> bool:
    return line.lstrip().startswith(">")


def strip_quotes(text: str) -> str:
    """The text without its blockquote lines (markdown `>` or <blockquote>)."""
    text = BLOCKQUOTE_HTML_RE.sub(" ", text or "")
    return "\n".join(l for l in text.splitlines() if not _is_quote_line(l))


def quote_blocks(text: str) -> list[dict]:
    """Each blockquote block: its words and the two lines that follow it.

    A block is a run of consecutive `>` lines; a <blockquote> element counts as
    one block, and the two lines after its closing tag are its window.
    """
    blocks: list[dict] = []
    lines = (text or "").splitlines()
    i = 0
    while i < len(lines):
        if _is_quote_line(lines[i]):
            j = i
            body = []
            while j < len(lines) and _is_quote_line(lines[j]):
                body.append(lines[j].lstrip()[1:])
                j += 1
            after = [l for l in lines[j:] if l.strip()][:2]
            blocks.append({"text": " ".join(body), "after": after})
            i = j
        else:
            i += 1
    for m in BLOCKQUOTE_HTML_RE.finditer(text or ""):
        tail = (text or "")[m.end():].splitlines()
        after = [re.sub(r"<[^>]+>", " ", l) for l in tail if l.strip()][:2]
        blocks.append({"text": re.sub(r"<[^>]+>", " ", m.group(1)), "after": after})
    return blocks


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-zÄÖÜäöüß0-9][\w'’.-]*", text or "")


def article_view(parts: list[str]) -> tuple[list[str], str]:
    """Split an article into its prose paragraphs and its full visible text.

    HTML comments are removed first: they never reach the reader. Headings and
    table rows are not sentences, so they stay out of the prose paragraphs, but
    they are part of the visible text that the word list and the number check
    read.
    """
    visible = COMMENT_RE.sub("", "\n\n".join(parts or []))
    prose = []
    for para in re.split(r"\n\s*\n", visible):
        keep = [l for l in para.splitlines()
                if l.strip() and not l.lstrip().startswith("#")
                and not l.lstrip().startswith("|")]
        if keep:
            prose.append("\n".join(keep))
    return prose, visible


# ── Rule engine ──

def _compiled(rule: dict) -> list[re.Pattern]:
    key = f"_c_{rule['id']}"
    if key not in _CACHE:
        _CACHE[key] = [re.compile(p, re.I) for p in rule.get("patterns", [])]
    return _CACHE[key]


def _eval_sentence_rule(rule: dict, sentence: str, lex: dict) -> str | None:
    """Return a violation detail for one sentence, or None."""
    kind = rule.get("rule")

    if kind == "copula_evaluation":
        cop = "|".join(lex.get("copula", []))
        core = "|".join(re.escape(t) for t in lex.get("eval_core", []))
        ctx = "|".join(re.escape(t) for t in lex.get("eval_context", []))
        subj = "|".join(re.escape(t).replace(r"\ ", r"\s+")
                        for t in sorted(lex.get("judged_subjects", []), key=len, reverse=True))
        if core and cop:
            m = re.search(rf"\b(?:{cop})\s+(?:\w+\s+){{0,2}}\b({core})\b", sentence, re.I)
            if m:
                return f"evaluative copula: “…{m.group(0)}”"
        if ctx and cop and subj:
            m = re.search(rf"\b(?:{subj})\b[\w\s,'’]{{0,30}}\b(?:{cop})\s+"
                          rf"(?:\w+\s+){{0,2}}\b({ctx})\b", sentence, re.I)
            if m:
                return f"evaluative copula over the counterpart: “…{m.group(0)[:60]}”"
        return None

    if kind == "unsupported_judgement":
        pat = _terms_pattern(lex.get(rule.get("lexicon", ""), []))
        if pat:
            m = pat.search(sentence)
            if m and not _has_evidence(sentence):
                return f"judgement filler “{m.group(0)}” with no number, quote or source"
        return None

    if kind == "ends_with_question":
        return "opener ends on a question mark" if sentence.rstrip().endswith("?") else None

    if kind == "verbless_short_coda":
        limit = int(rule.get("max_chars", 50))
        if len(sentence) > limit:
            return None
        if _has_finite_verb(sentence, rule, lex):
            return None
        return f"short coda with no finite verb: “{sentence}”"

    if kind == "count_threshold":
        threshold = int(rule.get("threshold", 2))
        hits = [m.group(0) for p in _compiled(rule) for m in p.finditer(sentence)]
        if len(hits) >= threshold:
            return f"{len(hits)} hits: {', '.join(hits[:4])}"
        return None

    # Default: any pattern, or a lexicon anchored at the sentence start.
    if rule.get("lexicon") and not rule.get("patterns"):
        pat = _terms_pattern(lex.get(rule["lexicon"], []),
                             anchor_start=rule.get("anchor") == "sentence_start")
        if pat:
            m = pat.search(sentence)
            if m:
                return f"“{m.group(0)}”"
        return None
    for p in _compiled(rule):
        m = p.search(sentence)
        if m:
            return f"“{m.group(0)[:70]}”"
    return None


def banned_words_in(text: str) -> list[str]:
    """The anti-KI-Sprech words this text uses, lower-cased and sorted.

    Rule (a) already reports them inside its violation line. A caller that wants
    to act on them — naming them back to the drafting model, say — needs them as
    data rather than as a substring of a message, so it reads the same lexicon
    the rule reads. An empty list when the lexicon cannot be loaded, because a
    caller uses this to widen what it accepts and must not widen on a failure.
    """
    try:
        spec = load_rules(refresh=False)
    except Exception:
        return []
    pat = _terms_pattern(spec.get("banned", []))
    if not pat:
        return []
    return sorted({m.group(0).lower() for m in pat.finditer(normalise(text or ""))})


def _eval_part_rule(rule: dict, part: str, lex: dict) -> str | None:
    part = normalise(part)
    kind = rule.get("rule")

    if kind == "triad_then_question":
        sents = sentences(part)
        cap = int(rule.get("max_sentence_chars", 90))
        need = int(rule.get("min_run", 3))
        run = 0
        for s in sents:
            if s.rstrip().endswith("?") and run >= need:
                return f"{run} short parallel sentences running into a question"
            run = run + 1 if len(s) <= cap and not s.rstrip().endswith("?") else 0
        return None

    if kind == "thesis_recall_coda":
        # The closing sentence says the opening one again in other words. Judged
        # on shared content words rather than on meaning, because the two
        # sentences are deliberately not identical — that is the whole tell.
        #
        # A coda that carries something of its own runs through: a number, a
        # quotation, a link, or an imperative naming the next step. Replacing
        # the restatement with the next step is the remedy the rule asks for,
        # so the check must not block the remedy.
        sents = sentences(part)
        if len(sents) < 3:
            return None
        min_len = int(rule.get("min_word_len", 5))
        need = int(rule.get("min_shared_words", 3))
        opener, coda = sents[0], sents[-1]
        if _has_evidence(coda):
            return None
        if _opens_imperative(coda, lex,
                             rule.get("imperative_lexicon", "imperative_verbs")):
            return None

        def content(text: str) -> set[str]:
            return {w for w in re.findall(r"[\wÄÖÜäöüß'’-]+", text.lower())
                    if len(w) >= min_len}

        shared = content(opener) & content(coda)
        if len(shared) >= need:
            return (f"coda repeats the opener on {len(shared)} content words: "
                    f"{', '.join(sorted(shared)[:4])}")
        return None

    if kind == "banned_words_from_anti_ki":
        pat = _terms_pattern(_CACHE.get("banned", []))
        if pat:
            hits = sorted({m.group(0).lower() for m in pat.finditer(part)})
            if hits:
                return ", ".join(hits)
        return None

    if kind == "count_threshold":
        threshold = int(rule.get("threshold", CONTRAST_FALLBACK))
        hits = [m.group(0) for p in _compiled(rule) for m in p.finditer(part)]
        if len(hits) >= threshold:
            return f"{len(hits)} hits: {', '.join(hits[:4])}"
        return None

    for p in _compiled(rule):
        m = p.search(part)
        if m:
            return f"“{m.group(0)[:70]}”"
    return None


# ── The two gates ──

def _eval_quote_budget(rule: dict, text: str) -> list[str]:
    """Rule (q): at most N blocks, a source in the window after each, a word cap."""
    hits = []
    blocks = quote_blocks(text)
    max_blocks = int(rule.get("max_blocks", 2))
    if len(blocks) > max_blocks:
        hits.append(f"{len(blocks)} blockquote blocks, at most {max_blocks}")
    pats = [re.compile(p, re.I) for p in rule.get("source_patterns", [])]
    for n, b in enumerate(blocks, 1):
        window = " ".join(b["after"])
        has_id = any(p.search(window) for p in pats)
        has_name = bool(re.search(r"[A-Za-z]{3,}", window))
        if not (has_id and has_name):
            hits.append(f"block {n} has no source (document plus date or ID) in "
                        f"the {rule.get('source_window_lines', 2)} lines after it")
    total = len(_words(strip_quotes(text))) + sum(len(_words(b["text"])) for b in blocks)
    quoted = sum(len(_words(b["text"])) for b in blocks)
    cap = float(rule.get("max_quoted_share", 0.05))
    if total and quoted / total > cap:
        hits.append(f"quoted words {quoted}/{total} = {quoted / total:.1%}, "
                    f"at most {cap:.0%}")
    return hits


def _gate1(parts: list[str], rules: list[dict], lex: dict,
           full_text: str = "") -> tuple[dict, list[str]]:
    checks, violations = {}, []
    unquoted = [strip_quotes(p) for p in parts]
    for rule in [r for r in rules if r.get("gate") == 1]:
        hits = []
        if rule.get("rule") == "quote_budget":
            hits = _eval_quote_budget(rule, full_text or "\n\n".join(parts))
            checks[rule["id"]] = "fail" if hits else "pass"
            if hits:
                violations.append(f"{rule['id']} {rule['label']} — " + "; ".join(hits[:4]))
            continue
        source = unquoted if rule["id"] in QUOTE_EXEMPT else parts
        wanted = set(rule.get("positions") or ["opener", "middle", "coda"])
        for pi, part in enumerate(source, 1):
            if rule.get("scope") == "part":
                d = _eval_part_rule(rule, part, lex)
                if d:
                    hits.append(f"tweet {pi}: {d}")
                continue
            sents = sentences(part)
            for si, s in enumerate(sents):
                if not (positions_of(si, len(sents)) & wanted):
                    continue
                d = _eval_sentence_rule(rule, s, lex)
                if d:
                    where = "/".join(sorted(positions_of(si, len(sents))))
                    hits.append(f"tweet {pi} {where}: {d}")
        checks[rule["id"]] = "fail" if hits else "pass"
        if hits:
            violations.append(f"{rule['id']} {rule['label']} — " + "; ".join(hits[:4]))
    return checks, violations


def claims_in(text: str, patterns: list[re.Pattern], min_digits: int) -> list[str]:
    """Everything in the draft that looks like a checkable assertion."""
    found = {m.group(0).strip() for p in patterns for m in p.finditer(text)}
    found |= {shown for _v, shown in _numbers(text, min_digits)}
    return sorted(found)


def _gate2(parts: list[str], rules: list[dict], lex: dict,
           source_text: str | None, expected_links: int,
           mode: str = "thread",
           sources: dict[str, str] | None = None,
           full_parts: list[str] | None = None) -> tuple[dict, list[str]]:
    """Gate 2 over the rules the mode loaded. `full_parts` is the visible text
    including headings and tables (article mode); word list and number check
    read that, everything else reads the prose `parts`."""
    checks, violations = {}, []
    hook = normalise(parts[0]) if parts else ""
    full_parts = full_parts if full_parts is not None else parts

    for rule in [r for r in rules if r.get("gate") == 2]:
        kind = rule.get("rule")
        hits: list[str] = []

        if kind == "opener_self_reference":
            for p in _compiled(rule):
                if p.search(hook):
                    hits.append("the hook opens on us or the product")
                    break

        elif kind == "link_discipline":
            total = sum(len(URL_RE.findall(p)) for p in parts)
            if expected_links == 0:
                if total:
                    hits.append(f"{total} links in a reply, expected none")
            else:
                if len(parts) > 1 and URL_RE.search(hook):
                    hits.append("the hook carries a link")
                if total != expected_links:
                    hits.append(f"{total} links, expected exactly {expected_links}")
                elif parts and not URL_RE.search(parts[-1]):
                    hits.append("the single link is not in the last tweet")

        elif kind == "substance_floor":
            limit = int(rule.get("max_chars", TWEET_LIMIT))
            if not parts or not any(p.strip() for p in parts):
                hits.append("empty draft")
            if not any(re.search(r"\d", p) for p in parts):
                hits.append("no concrete number anywhere")
            over = [f"tweet {i} at {len(p)}" for i, p in enumerate(parts, 1) if len(p) > limit]
            if over:
                hits.append(f"over {limit} chars: " + ", ".join(over))

        elif kind == "sources_grounded":
            md = int(rule.get("min_digits", 3))
            tol = float(rule.get("tolerance", 0.02))
            pats = [re.compile(p, re.I) for p in rule.get("claim_patterns", [])]
            draft = " ".join(parts)
            claims = claims_in(draft, pats, md)
            if not claims:
                checks[rule["id"]] = "pass"
                continue
            corpus = "\n".join((sources or {}).values())
            if not corpus.strip():
                hits.append(f"{len(claims)} checkable claim(s) and no source "
                            f"fetched this run: " + ", ".join(claims[:6]))
            else:
                loose = [c for c in claims if not _supported(c, corpus, md, tol)]
                if loose:
                    hits.append("not found in any cited source: " + ", ".join(loose[:6]))

        elif kind == "numbers_grounded":
            # scan() loads this rule only when a source text was supplied.
            loose = ungrounded_numbers(full_parts, source_text or "",
                                       int(rule.get("min_digits", 3)),
                                       float(rule.get("tolerance", 0.02)))
            if loose:
                hits.append("not supported by the source: " + ", ".join(loose))

        elif kind == "banned_words_from_anti_ki":
            for pi, part in enumerate(full_parts, 1):
                d = _eval_part_rule(rule, part, lex)
                if d:
                    hits.append(f"tweet {pi}: {d}")

        else:
            for pi, part in enumerate(parts, 1):
                d = _eval_part_rule(rule, part, lex)
                if d:
                    hits.append(f"tweet {pi}: {d}")

        checks[rule["id"]] = "fail" if hits else "pass"
        if hits:
            violations.append(f"{rule['id']} {rule['label']} — " + "; ".join(hits[:4]))
    return checks, violations


SCALE = {"k": 1e3, "tsd": 1e3, "thousand": 1e3,
         "m": 1e6, "mio": 1e6, "million": 1e6, "millions": 1e6,
         "b": 1e9, "bn": 1e9, "mrd": 1e9, "billion": 1e9, "billions": 1e9}
NUM_RE = re.compile(r"(\d[\d.,]*)\s*(k|m|bn|b|tsd|mio|mrd|thousand|million|millions"
                    r"|billion|billions)?\b", re.I)


def _to_float(raw: str) -> float | None:
    """Parse a written number. Whichever separator comes last is the decimal one;
    a lone separator in groups of three is a thousands separator."""
    s = raw.strip().rstrip(".,")
    try:
        if "," in s and "." in s:
            dec = "," if s.rfind(",") > s.rfind(".") else "."
            s = s.replace("." if dec == "," else ",", "").replace(dec, ".")
        elif "," in s:
            s = s.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", s) else s.replace(",", ".")
        elif "." in s and re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "")
        return float(s)
    except ValueError:
        return None


def _numbers(text: str, min_digits: int = 3, claims_only: bool = True) -> list[tuple]:
    """(value, printed form) for every number worth grounding.

    A scaled figure counts however few digits it shows — "$9.9M" is a claim
    about 9,900,000 and has to be traceable just like "9900000".
    """
    out = []
    for m in NUM_RE.finditer(URL_RE.sub(" ", text)):
        raw, suffix = m.group(1), (m.group(2) or "").lower()
        digits = re.sub(r"[.,]", "", raw)
        if claims_only and not suffix and len(digits) < min_digits:
            continue
        val = _to_float(raw)
        if val is None:
            continue
        out.append((val * SCALE.get(suffix, 1.0), m.group(0).strip()))
    return out


def _digit_runs(text: str, min_digits: int = 3) -> list[str]:
    return [re.sub(r"[.,]", "", m.group(0))
            for m in re.finditer(r"\d[\d.,]*", URL_RE.sub(" ", text))
            if len(re.sub(r"[.,]", "", m.group(0))) >= min_digits]


def _supported(claim: str, corpus: str, min_digits: int, tolerance: float) -> bool:
    """Is this claim carried by the corpus?

    A numeric claim is matched by value, so a source that writes 6,188,051 and
    a draft that writes $6.2M agree. Everything else is matched as text,
    case-insensitively and with whitespace collapsed, because a case name or an
    RFC number is either quoted correctly or it is not.
    """
    nums = _numbers(claim, min_digits)
    if nums:
        return not ungrounded_numbers([claim], corpus, min_digits, tolerance)
    needle = re.sub(r"\s+", " ", claim).strip().lower()
    return needle in re.sub(r"\s+", " ", corpus).lower()


def ungrounded_numbers(parts: list[str], source_text: str,
                       min_digits: int = 3, tolerance: float = 0.02) -> list[str]:
    """Figures in the draft that no number in the source supports.

    Exact digit strings match identifiers and years; everything else is matched
    by value within `tolerance`, so a draft may round 6,188,051 to $6.2M without
    being blocked for it.
    """
    src_vals = [v for v, _ in _numbers(source_text, min_digits, claims_only=False)]
    src_digits = set(_digit_runs(source_text, min_digits))
    loose = []
    for val, shown in _numbers(parts and " ".join(parts) or "", min_digits):
        if re.sub(r"[.,]", "", shown) in src_digits:
            continue
        if any(abs(val - s) <= tolerance * max(abs(val), abs(s), 1.0) for s in src_vals):
            continue
        loose.append(shown)
    return sorted(set(loose))


MODES = ("thread", "post", "reply", "article")


def scan(parts: list[str], source_text: str | None = None,
         mode: str = "thread", refresh: bool = True,
         sources: dict[str, str] | None = None,
         max_chars: int | None = None) -> dict:
    """Run both gates over the rules the mode loads.

    `mode` is "thread" (one link, in the last tweet), "post" (a single tweet
    carrying its own link), "reply" (no links at all) or "article" (a blog post
    as markdown, passed as one part; comments are dropped, prose is judged per
    paragraph, headings and tables only by the word list and the number check).

    Which rules run is the positive list `mode_rules` in the spec. A rule is
    loaded or it is not, and the result of a loaded rule is pass or fail.
    The number check (g2g) needs a source text: without one it is not loaded in
    thread, post and reply, and article mode refuses to run, because a post
    on the site is exactly where every figure has to be traceable.

    `sources` is {url: fetched text} for rule (h): a reply has no source
    document of its own, so it has to name the documents it leant on and they
    have to have been fetched in the same run.

    `max_chars` overrides rule (f)'s length limit. The rules are written for X,
    where 280 is the platform's own limit; on Moltbook a comment of 1500
    characters is ordinary, and applying the tweet limit there blocked every
    reply the ambassador wrote. Everything else about (f) still holds — a draft
    with no number is still empty of substance on either network.
    """
    spec = load_rules(refresh=refresh)
    all_rules, lex = spec["rules"], spec["lexicons"]
    mode_rules = spec["mode_rules"]
    if mode not in mode_rules:
        raise ValueError(f"unknown mode {mode!r}; the spec lists {sorted(mode_rules)}")
    listed = set(mode_rules[mode])
    raw_parts = [p for p in (parts or [])]

    if mode == "article":
        if not source_text:
            raise ValueError("article mode needs source_text: g2g is loaded there")
        parts, visible = article_view(raw_parts)
        full_parts, full_text = [visible], visible
    else:
        parts, full_parts = raw_parts, raw_parts
        full_text = "\n\n".join(raw_parts)
    expected = 0 if mode == "reply" else 1

    loaded, table = [], []
    for r in all_rules:
        reason = None
        if r["id"] not in listed:
            reason = f"not in mode_rules[{mode}]"
        elif r.get("rule") == "numbers_grounded" and not source_text:
            reason = "no source_text supplied"
        if reason is None:
            rule = r
            if r.get("rule") == "substance_floor":
                limit = max_chars or (r.get("max_chars_by_mode") or {}).get(mode)
                if limit:
                    rule = dict(r, max_chars=limit)
            loaded.append(rule)
        table.append({"id": r["id"], "gate": r.get("gate"), "mode": mode,
                      "loaded": reason is None, "reason": reason})

    c1, v1 = _gate1(parts, loaded, lex, full_text)
    c2, v2 = _gate2(parts, loaded, lex, source_text, expected, mode, sources,
                    full_parts)
    results = {**c1, **c2}
    for row in table:
        row["result"] = results.get(row["id"]) if row["loaded"] else None
    return {"ok": not (v1 or v2), "mode": mode,
            "gate1": c1, "gate2": c2, "rules": table,
            "violations": v1 + v2, "docs": docs_fingerprint()}


def format_report(result: dict) -> str:
    mode = result.get("mode", "thread")
    lines = [f"Pre-send scan [{mode}]: " + ("PASS" if result["ok"] else "BLOCKED")]
    for gate, key in (("Gate 1 (sentence)", "gate1"), ("Gate 2 (post)", "gate2")):
        states = result.get(key, {})
        failed = [k for k, v in states.items() if v == "fail"]
        lines.append(f"  {gate}: {len(states) - len(failed)}/{len(states)} pass"
                     + (f" — failing: {', '.join(failed)}" if failed else ""))
    for row in result.get("rules", []):
        state = row["result"] if row["loaded"] else "-"
        why = f"  ({row['reason']})" if row.get("reason") else ""
        lines.append(f"    {row['id']:<28} mode={row['mode']:<8} "
                     f"loaded={'yes' if row['loaded'] else 'no ':<3} result={state}{why}")
    for v in result.get("violations", []):
        lines.append(f"  ! {v}")
    d = result.get("docs", {})
    lines.append(f"  docs: scan {d.get('pre_send_scan')} / anti-KI {d.get('anti_ki_sprech')}"
                 f" / my-voice-en {d.get('my_voice_en')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """`python -m agents.voice_gate --mode article --source FILE DRAFT`.

    Exit 0 on PASS, 1 on BLOCKED. Reads the rules from the mirror unless
    --docs points at a moltrust-web checkout.
    """
    import argparse
    ap = argparse.ArgumentParser(prog="voice_gate")
    ap.add_argument("draft")
    ap.add_argument("--mode", default="article", choices=MODES)
    ap.add_argument("--source", help="source text for the number check (g2g)")
    ap.add_argument("--docs", help="moltrust-web checkout to read the rules from")
    ap.add_argument("--no-refresh", action="store_true")
    a = ap.parse_args(argv)
    global DOC_SCAN, DOC_ANTI_KI, DOC_MY_VOICE_EN
    if a.docs:
        root = Path(a.docs)
        DOC_SCAN = root / "docs" / "pre-send-scan.md"
        DOC_ANTI_KI = root / "anti-KI-Sprech.md"
        DOC_MY_VOICE_EN = root / "my-voice-en.md"
    draft = Path(a.draft).read_text(encoding="utf-8")
    source = Path(a.source).read_text(encoding="utf-8") if a.source else None
    parts = [draft] if a.mode == "article" else [p for p in re.split(r"\n\s*\n", draft) if p.strip()]
    result = scan(parts, source_text=source, mode=a.mode,
                  refresh=not (a.no_refresh or a.docs))
    print(format_report(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
