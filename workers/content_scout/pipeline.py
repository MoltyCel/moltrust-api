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


# A leaked lead-in line: opens with "here", mentions "draft" ("Here's my draft…",
# "Here is a publishable draft.", "Here's a draft comment…"). Length-capped in the
# loop so a genuine body sentence that merely contains "draft" is never stripped.
_PREAMBLE_RE = re.compile(r"^\s*here\b.*\bdraft\b", re.IGNORECASE)


def strip_preamble(md: str) -> str:
    """Remove leaked drafter meta so a queued draft is post-ready:
    - an outermost ```/```markdown fence wrapping the WHOLE comment,
    - a leading "here's my draft"/"here is a publishable draft" line,
    - a lone "---" separator at the very top.
    Inner code fences and in-body "---" rules are preserved — only the
    outermost wrapper and top-of-document lead-ins are stripped."""
    if not md:
        return md
    s = md.strip()

    def _unwrap_fence(t: str) -> str:
        m = re.match(r"^```[^\n]*\n(.*)\n```$", t, re.DOTALL)
        return m.group(1).strip() if m else t

    s = _unwrap_fence(s)
    # peel leading preamble lines / lone "---" rules (blank lines between allowed)
    for _ in range(6):
        lines = s.split("\n")
        while lines and not lines[0].strip():
            lines.pop(0)
        if not lines:
            break
        head = lines[0].strip()
        if (_PREAMBLE_RE.match(head) and len(head) <= 90) or head == "---":
            s = "\n".join(lines[1:]).strip()
            continue
        break
    return _unwrap_fence(s)  # a preamble line may have preceded the fence


_FENCE_RE = re.compile(r"(?m)^\s*```")


def code_flag(draft_type: str, md: str) -> str:
    """FIX 5 — a gh_comment draft that embeds a fenced code block is held for code
    verification: it must be run in the sandbox or reduced to a labelled
    illustrative fragment before it can post. Returns 'needs-code-verification'
    when a fenced block is present, else 'none'."""
    if draft_type != "gh_comment" or not md:
        return "none"
    return "needs-code-verification" if len(_FENCE_RE.findall(md)) >= 2 else "none"


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
            draft_md = strip_preamble(draft_md)  # belt-and-suspenders: guard the prompt rule
            vstatus = verify.run(draft_md)
            row.update(draft_type=dtype, draft_md=draft_md, verify_status=vstatus,
                       model_used=f"{config.MODEL_CLASSIFY}+{model}",
                       code_flag=code_flag(dtype, draft_md))
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
    tally["notified"] = await notify_new_drafts(conn, secrets)
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


def context_header(r) -> str:
    """WHO/WHAT · WHY IT'S WORTH IT · VERIFY — derived from target + class_reason +
    the verify-confirm gate. Shown above the draft so Lars can decide from his phone."""
    terr = _territory(r["target"] or "")
    obscure = terr.startswith("obscure")
    ver = r["redraft_version"] or 1
    verified = r["verify_confirmed_version"] == ver
    vline = (f"✅ claim confirmed vs primary source (v{ver})" if verified
             else "⚠️ UNVERIFIED — do not approve")
    why = ("technically fine, low strategic value" if obscure
           else "visibility + AAE positioning in a live standards/framework thread")
    reason = (r["class_reason"] or "").strip()
    codeline = ("\n⚠️ holds a code block — code-verify before approve"
                if r["code_flag"] == "needs-code-verification" else "")
    return (f"🔎 WHO/WHAT: {r['target'] or r['source_ref']} · {terr}\n"
            f"💡 WHY: {why}\n   ↳ {reason[:220]}\n"
            f"🔐 VERIFY: {vline}{codeline}")


def _draft_message(r) -> str:
    """One Telegram message per draft: context header, the FULL draft_md, then the
    approve/discard prompt. Splitting >4096 is handled by send_message."""
    ver = r["redraft_version"] or 1
    vmark = f"  ·  (re-draft v{ver} — ersetzt vorherige Version)" if ver > 1 else ""
    head = f"🧾 #{r['id']} · {r['draft_type']}{vmark}"
    body = r["draft_md"] or "(no draft)"
    foot = f"— reply  approve {r['id']}  /  discard {r['id']}"
    return f"{head}\n{context_header(r)}\n\n———\n{body}\n\n{foot}"


async def notify_new_drafts(conn, secrets) -> int:
    """FIX 3 — one-way Telegram push of each pending draft not yet notified.
    De-duped by the notified_at flag so re-runs don't resend. Returns count."""
    rows = await conn.fetch("""
        SELECT id, target, source_ref, draft_type, class_reason, draft_md, code_flag,
               redraft_version, verify_confirmed_version, created_at
        FROM content_review_queue
        WHERE state='pending_review' AND draft_md IS NOT NULL AND notified_at IS NULL
        ORDER BY created_at, id""")
    if not rows:
        return 0
    telegram.send_message(secrets,
        f"🧾 Content-Scout — {len(rows)} new draft(s) for review.\n"
        f"reply to approve/discard by id — posting stays manual.")
    for r in rows:
        # capture the Telegram message_id(s) so a later pass can editMessageText
        # in place instead of re-sending (stage 1: record only, no edit/delete).
        ids = telegram.send_message(secrets, _draft_message(r), label=f"#{r['id']}")
        await conn.execute(
            "UPDATE content_review_queue SET notified_at=now(), telegram_message_ids=$2::jsonb WHERE id=$1",
            r["id"], json.dumps(ids))
    return len(rows)


async def _persist(conn, row):
    state = row.pop("state_override", "pending_review")
    row.pop("verify_summary", None)
    new = await conn.execute("""
        INSERT INTO content_review_queue
          (source, source_ref, classification, class_reason, draft_type, target,
           draft_md, verify_status, model_used, tokens_in, tokens_out, cost_est, state,
           code_flag)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11,$12,$13,$14)
        ON CONFLICT (source_ref) DO NOTHING
    """, row["source"], row["source_ref"], row["classification"], row.get("class_reason"),
        row.get("draft_type", "none"), row.get("target"), row.get("draft_md"),
        json.dumps(row.get("verify_status", [])), row.get("model_used"),
        row.get("tokens_in", 0), row.get("tokens_out", 0), row.get("cost_est", 0), state,
        row.get("code_flag", "none"))


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
