"""Console CLI (lead model). The worker surfaces LEADS; you verify against the primary
source and write + post the comment yourself. There is no compose / approve / post path.

  python -m workers.content_scout.cli list [--status pending_review|held|discarded|approved|published] [--dropped]
  python -m workers.content_scout.cli show <id>
  python -m workers.content_scout.cli discard <id>

`list --status held` shows items stopped by the hold list (queue only, never carded).
The reach column shows the tier label (deferred-high / mid / reach-unknown / below-reach).
"""
import argparse
import asyncio

import asyncpg

from . import config, db
from .pipeline import _reach_of, reach_line

STATES = ("pending_review", "held", "discarded", "approved", "published")

_LIST_SQL = """
        SELECT id, classification, draft_type, target, lead_point, created_at{extra}
        FROM content_review_queue
        WHERE state=$1
        ORDER BY created_at DESC"""


async def _list(dropped: bool = False, status: str = "pending_review"):
    conn = await db.connect(config.load_secrets())
    if dropped:
        rows = await db.list_dropped(conn)
        if not rows:
            print("(no auto-dropped rows)")
        for r in rows:
            print(f"#{r['id']:<4} DROP  {(r['target'] or '')[:38]:<38} {(r['class_reason'] or '')[:70]}")
        await conn.close()
        return
    try:
        rows = await conn.fetch(_LIST_SQL.format(extra=", reach"), status)
    except asyncpg.exceptions.UndefinedColumnError:
        rows = await conn.fetch(_LIST_SQL.format(extra=""), status)
        if status == "held":
            print("(reach/held migration not applied: held rows are stored as 'discarded' "
                  "with a [held:<id>] prefix in class_reason — see `list --status discarded`)")
    if not rows:
        print(f"(no rows with state={status})")
    for r in rows:
        rec = _reach_of(r)
        tag = (f"hold:{rec['hold']}" if rec.get("hold") else rec.get("label") or "-")[:22]
        pt = (r["lead_point"] or "")[:50]
        print(f"#{r['id']:<4} {r['classification']:<5} {r['draft_type']:<9} "
              f"{(r['target'] or '')[:34]:<34} {tag:<22} {r['created_at']:%m-%d %H:%M}  {pt}")
    await conn.close()


async def _show(rid: int):
    conn = await db.connect(config.load_secrets())
    r = await db.get_row(conn, rid)
    if not r:
        print(f"no row #{rid}"); await conn.close(); return
    rec = _reach_of(r)
    print(f"# {r['id']}  {r['classification']}  {r['draft_type']}  state={r['state']}")
    print(f"source={r['source']}  target={r['target']}")
    print(f"primary source: {r['source_ref']}")
    print(f"reach: {reach_line(rec)}")
    if rec.get("reason"):
        print(f"reach reason: {rec['reason']}")
    if rec.get("hold"):
        print(f"held: hold-list entry {rec['hold']} (queue only — not a post candidate)")
    print(f"classifier reason: {r['class_reason']}")
    print(f"\nPOINT (one-line verifiable): {r['lead_point'] or '(none)'}")
    print("VERIFY: ⚠️ UNVERIFIED — check the primary source in review (the worker never confirms)")
    print("\n(no composed comment — you write it in review)")
    await conn.close()


async def _set(rid: int, state: str):
    conn = await db.connect(config.load_secrets())
    ok = await db.set_state(conn, rid, state)
    print(f"#{rid} -> {state}" if ok else f"no row #{rid}")
    await conn.close()


def main():
    ap = argparse.ArgumentParser(prog="content-scout")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("list")
    lp.add_argument("--dropped", action="store_true")
    lp.add_argument("--status", choices=STATES, default="pending_review")
    s = sub.add_parser("show"); s.add_argument("id", type=int)
    d = sub.add_parser("discard"); d.add_argument("id", type=int)
    args = ap.parse_args()
    if args.cmd == "list":
        asyncio.run(_list(args.dropped, args.status))
    elif args.cmd == "show":
        asyncio.run(_show(args.id))
    elif args.cmd == "discard":
        asyncio.run(_set(args.id, "discarded"))


if __name__ == "__main__":
    main()
