"""Content-Scout pipeline (LEAD model — surfaces review leads, never composes or posts).

ingest -> classify (Haiku, ALL)
       -> [PASS] content-pull -> hold-list check (local) -> reach filter (GitHub)
                 -> [pending_review] one-line verifiable POINT (Haiku)
       -> queue a LEAD (thread + point + reach, always UNVERIFIED) -> Telegram push

Routing of a PASS item (route_pass_item):
  1. hold list (local regex match over title + pulled thread text; the list is never
     sent to an LLM) -> state 'held': never a post candidate, no Telegram card, queue
     row only. Missing hold list => every PASS item is held (fail safe).
  2. reach (stars + contributors): below threshold -> 'discarded' with the reason
     recorded; >= high-reach threshold or standards org -> pending_review labelled
     'deferred-high'; GitHub API failure -> pending_review labelled 'reach-unknown'.
WATCH items get the hold check on the title only (no pull, no reach lookup). DROP
items are not checked.

Worker output per lead: (a) thread + one-line verifiable point, (b) a primary-source pointer
marked ALWAYS ⚠️ UNVERIFIED (the ✅/❌ is done in review — the worker never confirms), (c) reach
context. It does NOT compose the comment and does NOT post — Lars writes and posts in review.

Run:  python -m workers.content_scout.pipeline --dry-run
"""
import argparse
import asyncio
import json
import logging
import re

import asyncpg

from . import config, db, hold, llm, prompts, pull, reach, telegram

log = logging.getLogger("content_scout.pipeline")

STATE_HELD = "held"


def _slug_title(url: str) -> str:
    m = re.search(r"/([^/?#]+)/?$", url or "")
    return (m.group(1).replace("-", " ").replace("_", " ")[:120] if m else url)[:120]


def ingest(seen: set) -> list:
    """Build the candidate list from both feeds, deduped against the queue."""
    cands = []
    # Discovery — reuse the bot's file + its seen/pruned state; do NOT re-scan.
    if config.DISCOVERY_FEED.exists():
        d = json.loads(config.DISCOVERY_FEED.read_text(encoding="utf-8"))
        for c in d.get("candidates", []):
            url = c.get("url")
            if url and url not in seen:
                cands.append({"source": "discovery", "ref": url,
                              "title": c.get("title", ""), "target": f"{c.get('repo')}#{c.get('number')}"})
    # NewsScout — hashed url_key()s today, not URLs; yields 0 until it persists real URLs.
    if config.NEWSSCOUT_ARTIFACT.exists():
        urls = json.loads(config.NEWSSCOUT_ARTIFACT.read_text(encoding="utf-8"))
        for url in (urls if isinstance(urls, list) else []):
            if isinstance(url, str) and url.startswith("http") and url not in seen:
                cands.append({"source": "newsscout", "ref": url,
                              "title": _slug_title(url), "target": _slug_title(url)})
    return cands[:config.MAX_CANDIDATES_PER_RUN]


def route_pass_item(target: str, text: str, hold_list, reach_cache,
                    text_ok: bool = True) -> dict:
    """Decide the queue state of a PASS item. Pure apart from the (cached) reach lookup.

    Returns {'state': 'held'|'discarded'|'pending_review', 'reach': <record>} where
    the record carries stars / contributors / tier / label / reason and, for held
    items, 'hold' = the matched entry ID (or a fail-safe marker) — never the text.
    """
    rec = reach_cache.lookup(target)
    if hold_list is None:
        hold_id = hold.LIST_MISSING
    elif not text_ok:
        hold_id = hold.TEXT_UNAVAILABLE
    else:
        hold_id = hold_list.match(text)
    rec["hold"] = hold_id
    if hold_id:
        return {"state": STATE_HELD, "reach": rec}
    if rec["tier"] == reach.TIER_LOW:
        return {"state": "discarded", "reach": rec}
    return {"state": "pending_review", "reach": rec}


def _pull(c: dict, gh_token: str):
    """Pull thread text for a PASS item. Returns (text, ok)."""
    try:
        if c["source"] == "discovery":
            content = pull.pull_discovery(c["ref"], gh_token)
            return content, bool(content)
        final_url, content = pull.pull_article(c["ref"])
        c["ref"] = final_url
        return content, not content.startswith("[article fetch failed")
    except Exception as e:
        log.error("content pull failed for %s: %s", c["ref"], type(e).__name__)
        return "", False


async def run(dry_run: bool = True) -> dict:
    secrets = config.load_secrets()
    gh_token = secrets.get("GH_TOKEN", "")
    client = llm.make_client(config.anthropic_key(secrets))
    llm.reset_spend()

    # Balance gate: on an unhealthy API, classify only (no lead-point generation this cycle).
    classify_only = not llm.balance_ok(client)
    if classify_only:
        telegram.send_summary(secrets,
            "⚠️ MolTrust Content-Scout: Anthropic API unhealthy (quota/credit?) — "
            "classify-only this cycle, no lead points. Check credits.")

    hold_list = hold.load(config.hold_list_path())
    reach_cache = reach.ReachCache(gh_token)

    conn = await db.connect(secrets)
    seen = await db.seen_refs(conn)
    cands = ingest(seen)

    tally = {"pass": 0, "watch": 0, "drop": 0, "lead": 0, "held": 0, "below_reach": 0,
             "deferred_high": 0, "reach_unknown": 0,
             "classified": 0, "classify_only": classify_only,
             "hold_list_loaded": hold_list is not None, "rows": []}

    for c in cands:
        cin = prompts.classifier_input(c["source"], c["ref"], c["title"], "")
        verdict = llm.classify(client, prompts.CLASSIFIER_SYSTEM, cin)
        tally["classified"] += 1
        v = verdict["verdict"].lower()
        tally[v] += 1
        row = {"source": c["source"], "source_ref": c["ref"], "classification": v,
               "class_reason": verdict["reason"], "draft_type": "none",
               "target": c["target"], "draft_md": None, "lead_point": None,
               "model_used": config.MODEL_CLASSIFY, "reach": None}

        if v == "pass":
            content, text_ok = _pull(c, gh_token)
            row["source_ref"] = c["ref"]
            decision = route_pass_item(c["target"], f"{c['title']}\n{content}",
                                       hold_list, reach_cache, text_ok=text_ok)
            rec = decision["reach"]
            row["reach"] = rec
            if decision["state"] == STATE_HELD:
                # Queue row only: no lead point, no card, never a post candidate.
                row["state_override"] = STATE_HELD
                tally["held"] += 1
                tally["rows"].append({"cls": "HELD", **_short(c, verdict), "hold": rec["hold"]})
            elif decision["state"] == "discarded":
                row["state_override"] = "discarded"
                tally["below_reach"] += 1
                tally["rows"].append({"cls": "BELOW-REACH", **_short(c, verdict),
                                      "reach": rec["reason"]})
            else:
                if rec["tier"] == reach.TIER_HIGH:
                    tally["deferred_high"] += 1
                elif rec["tier"] == reach.TIER_UNKNOWN:
                    tally["reach_unknown"] += 1
                if not classify_only:
                    # A ONE-LINE verifiable point (Haiku). No composed comment, no
                    # verify verdict — the worker only surfaces the lead.
                    point, model = llm.point(
                        client, prompts.POINT_SYSTEM,
                        prompts.point_user(c["source"], c["ref"], c["title"], content, c["target"]))
                    row.update(draft_type="gh_lead", lead_point=point, model_used=model)
                    tally["lead"] += 1
                    tally["rows"].append({"cls": "LEAD", **_short(c, verdict), "point": point,
                                          "reach": rec["label"]})
                else:
                    tally["rows"].append({"cls": "PASS", **_short(c, verdict), "reach": rec["label"]})
        elif v == "watch":
            # Cheap hold check on the title only; WATCH is never a card either way.
            hold_id = hold_list.match(c["title"]) if hold_list is not None else None
            if hold_id:
                row["state_override"] = STATE_HELD
                row["reach"] = {"hold": hold_id}
                tally["held"] += 1
                tally["rows"].append({"cls": "HELD", **_short(c, verdict), "hold": hold_id})
            else:
                tally["rows"].append({"cls": "WATCH", **_short(c, verdict)})
        else:  # drop -> auto-discarded, stored only for idempotency (dedup)
            row["state_override"] = "discarded"
            tally["rows"].append({"cls": "DROP", **_short(c, verdict)})

        sp = llm.spend()
        row["tokens_in"], row["tokens_out"], row["cost_est"] = (
            sp["tokens_in"], sp["tokens_out"], round(sp["cost"], 5))
        await _persist_or_alert(conn, row, secrets)

    spend = llm.spend()
    summary = (f"🔎 Content-Scout: {tally['lead']} lead(s) "
               f"({tally['deferred_high']} deferred-high, {tally['reach_unknown']} reach-unknown), "
               f"{tally['watch']} watch · {tally['below_reach']} below reach · "
               f"{tally['held']} held (queue only) · "
               f"classified {tally['classified']} · run cost ~${spend['cost']:.2f}"
               + (" · CLASSIFY-ONLY" if classify_only else "")
               + ("" if hold_list is not None else " · HOLD LIST MISSING — all PASS held"))
    telegram.send_summary(secrets, summary)
    tally["spend"] = spend
    tally["summary"] = summary
    tally["candidates"] = len(cands)
    tally["notified"] = await notify_new_leads(conn, secrets)
    await conn.close()
    return tally


def _fmt_int(n) -> str:
    return f"{n:,}" if isinstance(n, int) else "?"


def _reach_of(r) -> dict:
    raw = r.get("reach") if hasattr(r, "get") else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    return raw if isinstance(raw, dict) else {}


def reach_line(rec: dict) -> str:
    """One-line reach summary for the card / CLI (numbers, tier label, no hold detail)."""
    if not rec:
        return "reach not recorded"
    label = rec.get("label") or "?"
    if rec.get("tier") == reach.TIER_NA:
        return "n/a (not a GitHub repo)"
    nums = (f"{_fmt_int(rec.get('stars'))} stars · "
            f"{_fmt_int(rec.get('contributors')) if rec.get('contributors') is not None else 'many'}"
            f" contributors")
    if rec.get("tier") == reach.TIER_UNKNOWN:
        return f"{label} — GitHub lookup failed ({rec.get('error')}); check the repo size by hand"
    extra = " · standards org" if rec.get("bypass") else ""
    if label == "deferred-high":
        extra += " · high reach, deferred — review only, not for immediate posting"
    return f"{nums} · {label}{extra}"


def _lead_message(r) -> str:
    """One Telegram LEAD card: (a) thread + one-line verifiable point, (b) primary-source
    pointer, ALWAYS marked ⚠️ UNVERIFIED (verify in review — the worker never confirms),
    (c) reach numbers. No composed comment, no approve/post — Lars writes the comment in review."""
    rec = _reach_of(r)
    reason = (r["class_reason"] or "").strip()
    head = "🔎 LEAD (deferred-high)" if rec.get("label") == "deferred-high" else (
        "🔎 LEAD (reach-unknown)" if rec.get("label") == "reach-unknown" else "🔎 LEAD")
    return (
        f"{head} #{r['id']} — {r['target'] or r['source_ref']}\n"
        f"POINT: {r['lead_point'] or '(none)'}\n"
        f"VERIFY: ⚠️ UNVERIFIED — check the primary source in review\n"
        f"  primary source: {r['source_ref']}\n"
        f"REACH: {reach_line(rec)}\n"
        f"  ↳ {reason[:220]}\n"
        f"— you write the comment in review; `discard {r['id']}` to dismiss")


_NOTIFY_SQL = """
        SELECT id, target, source_ref, class_reason, lead_point, created_at{extra}
        FROM content_review_queue
        WHERE state='pending_review' AND draft_type='gh_lead'
          AND lead_point IS NOT NULL AND notified_at IS NULL
        ORDER BY created_at, id"""


async def notify_new_leads(conn, secrets) -> int:
    """One-way Telegram push of each new LEAD not yet notified. De-duped by notified_at.
    Held rows are never selected (state != pending_review) and are skipped defensively."""
    try:
        rows = await conn.fetch(_NOTIFY_SQL.format(extra=", reach"))
    except asyncpg.exceptions.UndefinedColumnError:
        _warn_schema_once()
        rows = await conn.fetch(_NOTIFY_SQL.format(extra=""))
    rows = [r for r in rows if not _reach_of(r).get("hold")]
    if not rows:
        return 0
    telegram.send_message(secrets,
        f"🔎 Content-Scout — {len(rows)} new lead(s). Each is a thread + one-line point to "
        "VERIFY against the primary source and write up in review. Nothing is composed or posted.")
    for r in rows:
        ids = telegram.send_message(secrets, _lead_message(r), label=f"#{r['id']}")
        await conn.execute(
            "UPDATE content_review_queue SET notified_at=now(), telegram_message_ids=$2::jsonb WHERE id=$1",
            r["id"], json.dumps(ids))
    return len(rows)


async def _persist_or_alert(conn, row, secrets) -> bool:
    """Persist one row; on failure alert (Telegram) with the offending source_ref
    and keep going. A single bad candidate — e.g. a draft_type the CHECK constraint
    rejects — must never abort the whole run. That exact gap (draft_type='gh_lead'
    vs a stale constraint) silently killed the scout for days, because the raw
    _persist exception propagated out of the per-candidate loop."""
    try:
        await _persist(conn, row)
        return True
    except Exception as e:
        ref = row.get("source_ref", "?")
        try:
            telegram.send_message(
                secrets,
                f"⚠️ Content-Scout persist failed for {ref}: {type(e).__name__}: {e}",
                label="persist-error")
        except Exception:
            pass  # alerting must never itself break the run
        return False


_schema_warned = False


def _warn_schema_once():
    global _schema_warned
    if not _schema_warned:
        _schema_warned = True
        log.warning("content_review_queue lacks the reach column / 'held' state — migration "
                    "2026-09-15_content_scout_reach_hold.sql not applied; using the legacy "
                    "row shape (held -> discarded, reach folded into class_reason)")


def _is_schema_gap(e: Exception) -> bool:
    if isinstance(e, asyncpg.exceptions.UndefinedColumnError):
        return True
    return (isinstance(e, asyncpg.exceptions.CheckViolationError)
            and getattr(e, "constraint_name", "") == "content_review_queue_state_check")


def _legacy_reason(row: dict, rec) -> str:
    reason = row.get("class_reason") or ""
    if not rec:
        return reason
    tag = []
    if rec.get("hold"):
        tag.append(f"held:{rec['hold']}")
    if rec.get("tier"):
        tag.append(f"reach:{rec.get('label')} stars={rec.get('stars')} "
                   f"contributors={rec.get('contributors')}")
    return (f"[{' · '.join(tag)}] " if tag else "") + reason


_INSERT_SQL = """
        INSERT INTO content_review_queue
          (source, source_ref, classification, class_reason, draft_type, target,
           draft_md, lead_point, verify_status, model_used, tokens_in, tokens_out,
           cost_est, state, code_flag{extra_cols})
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,$12,$13,$14,$15{extra_vals})
        ON CONFLICT (source_ref) DO NOTHING
    """


async def _persist(conn, row):
    state = row.pop("state_override", "pending_review")
    rec = row.get("reach")
    args = [row["source"], row["source_ref"], row["classification"], row.get("class_reason"),
            row.get("draft_type", "none"), row.get("target"), row.get("draft_md"),
            row.get("lead_point"), json.dumps([]), row.get("model_used"),
            row.get("tokens_in", 0), row.get("tokens_out", 0), row.get("cost_est", 0), state, "none"]
    try:
        await conn.execute(_INSERT_SQL.format(extra_cols=", reach", extra_vals=",$16::jsonb"),
                           *args, json.dumps(rec) if rec is not None else None)
        return
    except Exception as e:
        if not _is_schema_gap(e):
            raise
        _warn_schema_once()
    # Legacy shape (migration pending): a held row must still never become a post
    # candidate, so it is stored as 'discarded' with the hold ID in class_reason.
    args[3] = _legacy_reason(row, rec)
    args[13] = "discarded" if state == STATE_HELD else state
    await conn.execute(_INSERT_SQL.format(extra_cols="", extra_vals=""), *args)


def _short(c, verdict):
    return {"source": c["source"], "ref": c["ref"], "target": c["target"],
            "reason": verdict["reason"]}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="run once manually; still populates the queue with leads")
    ap.add_argument("--json", action="store_true", help="emit the run tally as JSON")
    args = ap.parse_args()
    tally = asyncio.run(run(dry_run=args.dry_run))
    if args.json:
        print(json.dumps(tally, default=str, indent=2))
    else:
        print(tally["summary"])
        print(f"classified={tally['classified']} pass={tally['pass']} lead={tally['lead']} "
              f"held={tally['held']} below_reach={tally['below_reach']} "
              f"watch={tally['watch']} drop={tally['drop']} candidates={tally['candidates']} "
              f"cost=${tally['spend']['cost']:.4f}")


if __name__ == "__main__":
    main()
