"""Content-Scout pipeline (LEAD model — surfaces review leads, never composes or posts).

ingest -> classify (Haiku, ALL) -> [PASS] content-pull -> one-line verifiable POINT (Haiku)
       -> queue a LEAD (thread + point + who/why, always UNVERIFIED) -> Telegram push

Worker output per lead: (a) thread + one-line verifiable point, (b) a primary-source pointer
marked ALWAYS ⚠️ UNVERIFIED (the ✅/❌ is done in review — the worker never confirms), (c) who/why
context. It does NOT compose the comment and does NOT post — Lars writes and posts in review.

Run:  python -m workers.content_scout.pipeline --dry-run
"""
import argparse
import asyncio
import json
import re

from . import config, db, llm, prompts, pull, telegram


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

    conn = await db.connect(secrets)
    seen = await db.seen_refs(conn)
    cands = ingest(seen)

    tally = {"pass": 0, "watch": 0, "drop": 0, "lead": 0,
             "classified": 0, "classify_only": classify_only, "rows": []}

    for c in cands:
        cin = prompts.classifier_input(c["source"], c["ref"], c["title"], "")
        verdict = llm.classify(client, prompts.CLASSIFIER_SYSTEM, cin)
        tally["classified"] += 1
        v = verdict["verdict"].lower()
        tally[v] += 1
        row = {"source": c["source"], "source_ref": c["ref"], "classification": v,
               "class_reason": verdict["reason"], "draft_type": "none",
               "target": c["target"], "draft_md": None, "lead_point": None,
               "model_used": config.MODEL_CLASSIFY}

        if v == "pass" and not classify_only:
            # Pull the thread, then a ONE-LINE verifiable point (Haiku). No composed
            # comment, no verify verdict — the worker only surfaces the lead.
            if c["source"] == "discovery":
                content = pull.pull_discovery(c["ref"], gh_token)
            else:
                final_url, content = pull.pull_article(c["ref"])
                c["ref"] = final_url
                row["source_ref"] = final_url
            point, model = llm.point(
                client, prompts.POINT_SYSTEM,
                prompts.point_user(c["source"], c["ref"], c["title"], content, c["target"]))
            row.update(draft_type="gh_lead", lead_point=point, model_used=model)
            tally["lead"] += 1
            tally["rows"].append({"cls": "LEAD", **_short(c, verdict), "point": point})
        elif v == "watch":
            tally["rows"].append({"cls": "WATCH", **_short(c, verdict)})
        else:  # drop -> auto-discarded, stored only for idempotency (dedup)
            row["state_override"] = "discarded"
            tally["rows"].append({"cls": "DROP", **_short(c, verdict)})

        sp = llm.spend()
        row["tokens_in"], row["tokens_out"], row["cost_est"] = (
            sp["tokens_in"], sp["tokens_out"], round(sp["cost"], 5))
        await _persist_or_alert(conn, row, secrets)

    spend = llm.spend()
    summary = (f"🔎 Content-Scout: {tally['lead']} lead(s), {tally['watch']} watch · "
               f"classified {tally['classified']} · run cost ~${spend['cost']:.2f}"
               + (" · CLASSIFY-ONLY" if classify_only else ""))
    telegram.send_summary(secrets, summary)
    tally["spend"] = spend
    tally["summary"] = summary
    tally["candidates"] = len(cands)
    tally["notified"] = await notify_new_leads(conn, secrets)
    await conn.close()
    return tally


# Repos whose threads are worth being seen in (standards bodies / major frameworks).
_STANDARDS_ORGS = {"w3c", "ietf", "a2aproject", "in-toto", "x402-foundation",
                   "google-agentic-commerce"}
_MAJOR_ORGS = {"google", "microsoft", "crewAIInc", "run-llama", "openai", "langchain-ai"}


def _territory(target: str) -> str:
    org = (target or "").split("/")[0]
    if org in _STANDARDS_ORGS:
        return "real standards body / core territory"
    if org in _MAJOR_ORGS:
        return "major framework / core territory"
    return "obscure / low-reach"


def _lead_message(r) -> str:
    """One Telegram LEAD card: (a) thread + one-line verifiable point, (b) primary-source
    pointer, ALWAYS marked ⚠️ UNVERIFIED (verify in review — the worker never confirms),
    (c) who/why. No composed comment, no approve/post — Lars writes the comment in review."""
    terr = _territory(r["target"] or "")
    why = ("technically fine, low strategic value" if terr.startswith("obscure")
           else "visibility + AAE positioning in a live standards/framework thread")
    reason = (r["class_reason"] or "").strip()
    return (
        f"🔎 LEAD #{r['id']} — {r['target'] or r['source_ref']}\n"
        f"POINT: {r['lead_point'] or '(none)'}\n"
        f"VERIFY: ⚠️ UNVERIFIED — check the primary source in review\n"
        f"  primary source: {r['source_ref']}\n"
        f"WHO/WHY: {terr} · {why}\n"
        f"  ↳ {reason[:220]}\n"
        f"— you write the comment in review; `discard {r['id']}` to dismiss")


async def notify_new_leads(conn, secrets) -> int:
    """One-way Telegram push of each new LEAD not yet notified. De-duped by notified_at."""
    rows = await conn.fetch("""
        SELECT id, target, source_ref, class_reason, lead_point, created_at
        FROM content_review_queue
        WHERE state='pending_review' AND draft_type='gh_lead'
          AND lead_point IS NOT NULL AND notified_at IS NULL
        ORDER BY created_at, id""")
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


async def _persist(conn, row):
    state = row.pop("state_override", "pending_review")
    await conn.execute("""
        INSERT INTO content_review_queue
          (source, source_ref, classification, class_reason, draft_type, target,
           draft_md, lead_point, verify_status, model_used, tokens_in, tokens_out,
           cost_est, state, code_flag)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,$12,$13,$14,$15)
        ON CONFLICT (source_ref) DO NOTHING
    """, row["source"], row["source_ref"], row["classification"], row.get("class_reason"),
        row.get("draft_type", "none"), row.get("target"), row.get("draft_md"),
        row.get("lead_point"), json.dumps([]), row.get("model_used"),
        row.get("tokens_in", 0), row.get("tokens_out", 0), row.get("cost_est", 0), state, "none")


def _short(c, verdict):
    return {"source": c["source"], "ref": c["ref"], "target": c["target"],
            "reason": verdict["reason"]}


def main():
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
              f"watch={tally['watch']} drop={tally['drop']} candidates={tally['candidates']} "
              f"cost=${tally['spend']['cost']:.4f}")


if __name__ == "__main__":
    main()
