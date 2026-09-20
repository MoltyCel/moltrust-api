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
            density, opener, link discipline, substance floor, and (g) every
            number of three digits or more traceable to the source.

A blocked draft goes to Telegram, never silently softened.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path

import yaml

log = logging.getLogger("voice_gate")

WEB_DOCS = Path(os.path.expanduser("~/moltstack/workers/content_scout/.webdocs"))
DOC_SCAN = WEB_DOCS / "docs" / "pre-send-scan.md"
DOC_ANTI_KI = WEB_DOCS / "anti-KI-Sprech.md"
DOC_MY_VOICE_EN = WEB_DOCS / "my-voice-en.md"

URL_RE = re.compile(r"https?://\S+")
SENT_SPLIT = re.compile(r"(?<=[.!?])[\s\n]+|\n{2,}")
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
        token = cs_config.load_secrets().get("GH_TOKEN", "") or os.getenv("GH_TOKEN", "")
        if token:
            guardrails.ensure_web_docs(token)
    except Exception as e:  # noqa: BLE001 — refresh is best-effort by design
        log.warning(f"Could not refresh the voice docs mirror: {e}")


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
    _CACHE.update(rules=rules, lexicons=lexicons,
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

def sentences(part: str) -> list[str]:
    """Prose sentences of one tweet. Segments that are only a URL are dropped —
    they are not prose and must not become the opener or the coda."""
    out = []
    for raw in SENT_SPLIT.split(part or ""):
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


def _has_evidence(sentence: str) -> bool:
    """A number, a quotation or a source in the same sentence counts as a belt."""
    return bool(re.search(r"\d", sentence) or URL_RE.search(sentence)
                or re.search(r"[\"„“”']", sentence))


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
        hints = set(lex.get(rule.get("lexicon", ""), []))
        words = re.findall(r"[\w’']+", sentence.lower())
        if any(w in hints or w.endswith(("ed", "en")) for w in words):
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


def _eval_part_rule(rule: dict, part: str, lex: dict) -> str | None:
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

def _gate1(parts: list[str], rules: list[dict], lex: dict) -> tuple[dict, list[str]]:
    checks, violations = {}, []
    for rule in [r for r in rules if r.get("gate") == 1]:
        wanted = set(rule.get("positions") or ["opener", "middle", "coda"])
        hits = []
        for pi, part in enumerate(parts, 1):
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


def _gate2(parts: list[str], rules: list[dict], lex: dict,
           source_text: str | None, expected_links: int) -> tuple[dict, list[str]]:
    checks, violations = {}, []
    hook = parts[0] if parts else ""

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

        elif kind == "numbers_grounded":
            if source_text:
                loose = ungrounded_numbers(parts, source_text,
                                           int(rule.get("min_digits", 3)),
                                           float(rule.get("tolerance", 0.02)))
                if loose:
                    hits.append("not supported by the source: " + ", ".join(loose))
            else:
                checks[rule["id"]] = "skipped (no source)"
                continue

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


def scan(parts: list[str], source_text: str | None = None,
         mode: str = "thread", refresh: bool = True) -> dict:
    """Run both gates. `mode` is "thread" (one link, in the last tweet), "post"
    (a single tweet carrying its own link) or "reply" (no links at all)."""
    spec = load_rules(refresh=refresh)
    rules, lex = spec["rules"], spec["lexicons"]
    parts = [p for p in (parts or [])]
    expected = 0 if mode == "reply" else 1

    c1, v1 = _gate1(parts, rules, lex)
    c2, v2 = _gate2(parts, rules, lex, source_text, expected)
    return {"ok": not (v1 or v2), "mode": mode,
            "gate1": c1, "gate2": c2,
            "violations": v1 + v2, "docs": docs_fingerprint()}


def format_report(result: dict) -> str:
    lines = [f"Pre-send scan [{result.get('mode', 'thread')}]: "
             + ("PASS" if result["ok"] else "BLOCKED")]
    for gate, key in (("Gate 1 (sentence)", "gate1"), ("Gate 2 (post)", "gate2")):
        states = result.get(key, {})
        failed = [k for k, v in states.items() if v == "fail"]
        lines.append(f"  {gate}: {len(states) - len(failed)}/{len(states)} pass"
                     + (f" — failing: {', '.join(failed)}" if failed else ""))
    for v in result.get("violations", []):
        lines.append(f"  ! {v}")
    d = result.get("docs", {})
    lines.append(f"  docs: scan {d.get('pre_send_scan')} / anti-KI {d.get('anti_ki_sprech')}"
                 f" / my-voice-en {d.get('my_voice_en')}")
    return "\n".join(lines)
