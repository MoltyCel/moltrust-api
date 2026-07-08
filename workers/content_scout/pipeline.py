"""Content-Scout pipeline (v0 = draft-and-queue only; nothing publishes).

ingest -> classify (Haiku, ALL) -> [PASS] content-pull -> draft (Opus)
       -> verify-gate -> insert queue row (pending_review) -> Telegram summary

Run:  python -m workers.content_scout.pipeline --dry-run
"""
import argparse
import asyncio
import json
import re

from . import config, db, guardrails, llm, prompts, pull, telegram, verify


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
    # NewsScout — the artifact today is hashed url_key()s, not URLs. Accept only
    # http(s) entries: yields 0 now (no scraping), auto-works if news_scout starts
    # persisting real URLs. The newsworthy content is otherwise Telegram-only.
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
    api_key = config.anthropic_key(secrets)
    client = llm.make_client(api_key)
    llm.reset_spend()

    # Balance gate: reuse the existing monitor's probe. On failure -> classify-only.
    classify_only = not llm.balance_ok(client)
    if classify_only:
        telegram.send_summary(secrets,
            "⚠️ MolTrust Content-Scout: Anthropic API unhealthy (quota/credit?) — "
            "running classify-only this cycle, no drafting. Check credits.")

    docs = guardrails.load_all(gh_token) if not classify_only else {}
    conn = await db.connect(secrets)
    seen = await db.seen_refs(conn)
    cands = ingest(seen)

    tally = {"pass": 0, "watch": 0, "drop": 0, "gh_comment": 0, "blog_post": 0,
             "classified": 0, "classify_only": classify_only, "rows": []}

    for c in cands:
        cin = prompts.classifier_input(c["source"], c["ref"], c["title"], "")
        verdict = llm.classify(client, prompts.CLASSIFIER_SYSTEM, cin)
        tally["classified"] += 1
        v = verdict["verdict"].lower()
        tally[v] += 1
        row = {"source": c["source"], "source_ref": c["ref"], "classification": v,
               "class_reason": verdict["reason"], "draft_type": "none",
               "target": c["target"], "draft_md": None, "verify_status": [],
               "model_used": config.MODEL_CLASSIFY}

        if v == "pass" and not classify_only:
            if c["source"] == "discovery":
                content = pull.pull_discovery(c["ref"], gh_token)
                dtype = "gh_comment"
            else:
                final_url, content = pull.pull_article(c["ref"])
                c["ref"] = final_url  # dedupe on the resolved primary URL
                row["source_ref"] = final_url
                dtype = "blog_post"
            sys = prompts.drafter_system(docs, dtype)
            user = prompts.drafter_user(c["source"], c["ref"], c["title"], content, c["target"])
            draft_md, model = llm.draft(client, sys, user)
            vstatus = verify.run(draft_md)
            row.update(draft_type=dtype, draft_md=draft_md, verify_status=vstatus,
                       model_used=f"{config.MODEL_CLASSIFY}+{model}")
            tally[dtype] += 1
            state_row = {**row, "verify_summary": verify.summary(vstatus)}
            tally["rows"].append({"cls": "PASS", **_short(c, verdict), "draft": draft_md,
                                  "verify": vstatus, "verify_summary": verify.summary(vstatus)})
        elif v == "watch":
            tally["rows"].append({"cls": "WATCH", **_short(c, verdict)})
        else:  # drop -> auto-discarded, stored only for idempotency
            row["state_override"] = "discarded"
            tally["rows"].append({"cls": "DROP", **_short(c, verdict)})

        sp = llm.spend()
        row["tokens_in"], row["tokens_out"], row["cost_est"] = (
            sp["tokens_in"], sp["tokens_out"], round(sp["cost"], 5))
        # write (DROP rows stored as discarded so `list` stays clean but dedup holds)
        await _persist(conn, row)

    spend = llm.spend()
    summary = (f"🧾 Content-Scout: {tally['gh_comment']} gh-comment, {tally['blog_post']} blog "
               f"drafts pending · {tally['watch']} watch · run cost ~${spend['cost']:.2f}"
               + (" · CLASSIFY-ONLY" if classify_only else ""))
    telegram.send_summary(secrets, summary)
    tally["spend"] = spend
    tally["summary"] = summary
    tally["candidates"] = len(cands)
    await conn.close()
    return tally


async def _persist(conn, row):
    state = row.pop("state_override", "pending_review")
    row.pop("verify_summary", None)
    new = await conn.execute("""
        INSERT INTO content_review_queue
          (source, source_ref, classification, class_reason, draft_type, target,
           draft_md, verify_status, model_used, tokens_in, tokens_out, cost_est, state)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11,$12,$13)
        ON CONFLICT (source_ref) DO NOTHING
    """, row["source"], row["source_ref"], row["classification"], row.get("class_reason"),
        row.get("draft_type", "none"), row.get("target"), row.get("draft_md"),
        json.dumps(row.get("verify_status", [])), row.get("model_used"),
        row.get("tokens_in", 0), row.get("tokens_out", 0), row.get("cost_est", 0), state)


def _short(c, verdict):
    return {"source": c["source"], "ref": c["ref"], "target": c["target"],
            "reason": verdict["reason"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="run once manually; still populates the queue for review")
    ap.add_argument("--json", action="store_true", help="emit the run tally as JSON")
    args = ap.parse_args()
    tally = asyncio.run(run(dry_run=args.dry_run))
    if args.json:
        print(json.dumps(tally, default=str, indent=2))
    else:
        print(tally["summary"])
        print(f"classified={tally['classified']} pass={tally['pass']} "
              f"watch={tally['watch']} drop={tally['drop']} "
              f"candidates={tally['candidates']} cost=${tally['spend']['cost']:.4f}")


if __name__ == "__main__":
    main()
