"""Console CLI (v0). Review only — approve marks ready, it does NOT publish.

  python -m workers.content_scout.cli list
  python -m workers.content_scout.cli show <id> [--write]
  python -m workers.content_scout.cli approve <id>
  python -m workers.content_scout.cli discard <id>
"""
import argparse
import asyncio
import json
from pathlib import Path

from . import config, db


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
    print(f"# {r['id']}  {r['classification']}  {r['draft_type']}  state={r['state']}")
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


def main():
    ap = argparse.ArgumentParser(prog="content-scout")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("list")
    lp.add_argument("--dropped", action="store_true", help="show retained auto-dropped rows")
    s = sub.add_parser("show"); s.add_argument("id", type=int); s.add_argument("--write", action="store_true")
    a = sub.add_parser("approve"); a.add_argument("id", type=int)
    d = sub.add_parser("discard"); d.add_argument("id", type=int)
    args = ap.parse_args()
    if args.cmd == "list":
        asyncio.run(_list(args.dropped))
    elif args.cmd == "show":
        asyncio.run(_show(args.id, args.write))
    elif args.cmd == "approve":
        asyncio.run(_set(args.id, "approved"))
    elif args.cmd == "discard":
        asyncio.run(_set(args.id, "discarded"))


if __name__ == "__main__":
    main()
