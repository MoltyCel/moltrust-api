"""Console CLI (v0). Review only — approve marks ready, it does NOT publish.

  python -m workers.content_scout.cli list
  python -m workers.content_scout.cli show <id> [--write]
  python -m workers.content_scout.cli approve <id>
  python -m workers.content_scout.cli discard <id>
  python -m workers.content_scout.cli redraft <id>   # in-place re-draft, bumps version, re-pushes
  python -m workers.content_scout.cli post <id>      # posts the gh_comment + ThreadWatch add
"""
import argparse
import asyncio
import datetime as _dt
import json
from pathlib import Path

import httpx

from . import config, db, guardrails, llm, pipeline, prompts, pull


async def _list(dropped: bool = False):
    conn = await db.connect(config.load_secrets())
    if dropped:
        rows = await db.list_dropped(conn)
        if not rows:
            print("(no auto-dropped rows)")
        for r in rows:
            print(f"#{r['id']:<4} DROP  {(r['target'] or '')[:38]:<38} {(r['class_reason'] or '')[:70]}")
        await conn.close()
        return
    rows = await db.list_pending(conn)
    if not rows:
        print("(queue empty — nothing pending review)")
    for r in rows:
        print(f"#{r['id']:<4} {r['classification']:<5} {r['draft_type']:<11} "
              f"{(r['target'] or '')[:40]:<40} ~${float(r['cost_est']):.3f}  {r['created_at']:%Y-%m-%d %H:%M}")
    await conn.close()


async def _show(rid: int, write: bool):
    conn = await db.connect(config.load_secrets())
    r = await db.get_row(conn, rid)
    if not r:
        print(f"no row #{rid}")
        await conn.close()
        return
    print(f"# {r['id']}  {r['classification']}  {r['draft_type']}  state={r['state']}  code_flag={r['code_flag']}")
    print(f"source={r['source']}  target={r['target']}\nref={r['source_ref']}")
    print(f"reason: {r['class_reason']}")
    vs = json.loads(r["verify_status"]) if isinstance(r["verify_status"], str) else r["verify_status"]
    print("\n=== verification-status ===")
    if not vs:
        print("(no spec/hash/quant claims detected)")
    for e in vs:
        mark = "OK " if e.get("status") == "verified" else "!! "
        kinds = ",".join(e.get("kinds", [])) or "-"
        print(f"  {mark}[{kinds}] {e.get('claim')}  [{e.get('source') or 'no source'}]")
    print("\n=== draft ===\n" + (r["draft_md"] or "(no draft — watch/drop item)"))
    if write and r["draft_md"]:
        out = Path.home() / "content-scout-drafts"
        out.mkdir(exist_ok=True)
        p = out / f"{r['id']}-{(r['target'] or 'draft').replace('/', '_').replace('#', '_')[:40]}.md"
        p.write_text(r["draft_md"], encoding="utf-8")
        print(f"\nwritten: {p}")
    await conn.close()


async def _set(rid: int, state: str):
    conn = await db.connect(config.load_secrets())
    ok = await db.set_state(conn, rid, state)
    print(f"#{rid} -> {state}" if ok else f"no row #{rid}")
    if state == "approved" and ok:
        print("NOTE: approved marks the draft ready. It does NOT publish. "
              "Publish manually (GH comment via MoltyCel token / blog via website-deploy.md).")
    await conn.close()


async def _redraft(rid: int):
    """The single re-draft path. Re-runs the drafter for an existing row and writes
    the result IN PLACE (never a parallel row — the unique source_ref index would
    reject one anyway), bumps redraft_version, resets notified_at/telegram_message_ids,
    then re-pushes ONLY this row with a '(re-draft vN — ersetzt vorherige Version)'
    marker."""
    secrets = config.load_secrets()
    gh = secrets.get("GH_TOKEN", "")
    conn = await db.connect(secrets)
    r = await db.get_row(conn, rid)
    if not r:
        print(f"no row #{rid}"); await conn.close(); return
    if r["draft_type"] not in ("gh_comment", "blog_post"):
        print(f"#{rid} draft_type={r['draft_type']} — nothing to re-draft"); await conn.close(); return
    client = llm.make_client(config.anthropic_key(secrets))
    llm.reset_spend()
    docs = guardrails.load_all(gh)
    if r["source"] == "discovery":
        content = pull.pull_discovery(r["source_ref"], gh)
    else:
        _, content = pull.pull_article(r["source_ref"])
    md, model = llm.draft(
        client, prompts.drafter_system(docs, r["draft_type"]),
        prompts.drafter_user(r["source"], r["source_ref"], r["target"] or "", content, r["target"] or ""))
    md = pipeline.strip_preamble(md)
    cf = pipeline.code_flag(r["draft_type"], md)
    newver = (r["redraft_version"] or 1) + 1
    await conn.execute("""
        UPDATE content_review_queue
        SET draft_md=$2, code_flag=$3, redraft_version=$4, model_used=$5,
            notified_at=NULL, telegram_message_ids=NULL
        WHERE id=$1""",
        rid, md, cf, newver, f"{config.MODEL_CLASSIFY}+{model} (redraft v{newver})")
    print(f"#{rid} re-drafted in place -> v{newver}  code_flag={cf}  (no new row)")
    pushed = await pipeline.notify_new_drafts(conn, secrets)
    print(f"pushed {pushed} draft(s) to Telegram with the re-draft v{newver} marker; "
          f"spend ${llm.spend()['cost']:.4f}")
    await conn.close()


def _pin_threadwatch(target: str, when: str) -> str:
    """FIX 2 — idempotently add repo#num to ThreadWatch dynamic pins
    (state['pinned']), mirroring scripts/threadwatch.py's /pin shape + atomic
    write. No duplicate if already tracked. Returns a one-line status."""
    if not target or "#" not in target:
        return f"threadwatch: skipped (target {target!r} not repo#num)"
    repo, _, num = target.rpartition("#")
    try:
        num_int = int(num)
    except ValueError:
        return f"threadwatch: skipped (bad ref {target!r})"
    p = config.THREADWATCH_STATE
    state = {}
    if p.exists():
        try:
            state = json.loads(p.read_text())
        except Exception:
            state = {}
    pinned = state.setdefault("pinned", {})
    if target in pinned:
        return f"threadwatch: already tracked ({target})"
    pinned[target] = {
        "repo": repo, "number": num_int,
        "note": f"MoltyCel commented {when} (via Content-Scout)",
        "kind": "issue",
        "pinned_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.replace(p)  # atomic
    return f"threadwatch: pinned {target}"


async def _post(rid: int, code_ok: bool = False):
    """Post a gh_comment draft to GitHub as MoltyCel, then (on 201) mark it posted
    and add the thread to ThreadWatch. This is the single post trigger point — the
    ThreadWatch add fires at POST time, not approve time."""
    conn = await db.connect(config.load_secrets())
    r = await db.get_row(conn, rid)
    if not r:
        print(f"no row #{rid}"); await conn.close(); return
    if r["draft_type"] != "gh_comment":
        print(f"#{rid} draft_type={r['draft_type']} — post supports gh_comment only")
        await conn.close(); return
    if r["state"] == "published":
        print(f"#{rid} already published (no-op)"); await conn.close(); return
    if not r["draft_md"]:
        print(f"#{rid} has no draft_md — nothing to post"); await conn.close(); return
    if r["code_flag"] == "needs-code-verification" and not code_ok:
        print(f"#{rid} BLOCKED: draft embeds a code block (needs-code-verification).\n"
              "  Run the code in the sandbox or reduce it to a labelled 'illustrative,\n"
              "  untested' fragment, then re-post with --code-ok to confirm it's cleared.")
        await conn.close(); return
    target = r["target"] or ""
    if "#" not in target:
        print(f"#{rid} target {target!r} is not repo#num — cannot post"); await conn.close(); return
    repo, _, num = target.rpartition("#")
    tok = config.load_secrets().get("GH_TOKEN", "")
    if not tok:
        print("GH_TOKEN missing in ~/.moltrust_secrets — cannot post"); await conn.close(); return
    resp = httpx.post(
        f"https://api.github.com/repos/{repo}/issues/{num}/comments",
        json={"body": r["draft_md"]}, timeout=30,
        headers={"Authorization": f"Bearer {tok}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": config.USER_AGENT})
    if resp.status_code != 201:
        print(f"POST failed: HTTP {resp.status_code} {resp.text[:300]}")
        await conn.close(); return
    comment_url = resp.json().get("html_url", "(no url)")
    # 'published' is the schema's canonical done-state (content_review_queue_state_check);
    # earlier 'posted' violated the constraint after the comment had already gone out.
    await db.set_state(conn, rid, "published")
    tw = _pin_threadwatch(target, _dt.date.today().isoformat())
    print(f"#{rid} PUBLISHED -> {comment_url}\n  state -> published\n  {tw}")
    await conn.close()


def main():
    ap = argparse.ArgumentParser(prog="content-scout")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("list")
    lp.add_argument("--dropped", action="store_true", help="show retained auto-dropped rows")
    s = sub.add_parser("show"); s.add_argument("id", type=int); s.add_argument("--write", action="store_true")
    a = sub.add_parser("approve"); a.add_argument("id", type=int)
    d = sub.add_parser("discard"); d.add_argument("id", type=int)
    rd = sub.add_parser("redraft"); rd.add_argument("id", type=int)
    po = sub.add_parser("post"); po.add_argument("id", type=int)
    po.add_argument("--code-ok", action="store_true",
                    help="confirm embedded code was run or labelled illustrative; clears the code gate")
    args = ap.parse_args()
    if args.cmd == "list":
        asyncio.run(_list(args.dropped))
    elif args.cmd == "show":
        asyncio.run(_show(args.id, args.write))
    elif args.cmd == "approve":
        asyncio.run(_set(args.id, "approved"))
    elif args.cmd == "discard":
        asyncio.run(_set(args.id, "discarded"))
    elif args.cmd == "redraft":
        asyncio.run(_redraft(args.id))
    elif args.cmd == "post":
        asyncio.run(_post(args.id, code_ok=args.code_ok))


if __name__ == "__main__":
    main()
