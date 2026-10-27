"""Die Tageszeile zum Brett (ops G5, Strategie ops/wachstum.md).

Once a day: how many entries stand on the board and what the newest of them
offer. The text and the rule that builds it live in `app/board.py`; this
program reads, decides whether there is anything to say, posts it, and leaves
a trace of both outcomes.

**An empty board says nothing.** A line that reports zero every day is the
exact shape the posting throttle of 23.09.2026 exists to stop, and after a
week nobody reads the eighth one. So an empty board is a declared quiet state
(`config/expectations.yaml`, `board_digest`): the heartbeat carries the reason,
the log prints it, and no post goes out. That is also why the silence proof in
`tests/test_board.py` is a test and not a hope.

**Taskmarket is named and not written to.** The job asks for the line on
Moltbook *and* on taskmarket. Measured on 17.10.2026 against the taskmarket
CLI 1.13.0 and the published OpenAPI document of `api.taskmarket.dev` (203
paths): the platform has no free broadcast surface we may use. `/emails/
broadcast` is admin-only, `/emails/send` is addressed to one agent, and every
task operation costs USDC — `task create` costs the reward. A daily paid task
as a bulletin board would be money moving without a decision, and money is
Lars' gate. So the target is declared, the reason is printed on every run, and
nothing is sent there until that decision exists.

**One sending place.** Telegram goes through `app/notify.py`, so the run shows
up in `~/selftest/telegram-sent.jsonl` and is throttled like everything else.

Cron: daily 11:00 UTC. Moltbook already carries one post a day for this
account at 09:00 (`agents/moltbook_poster.py`), and this is a second one on
the days the board is not empty — named here because the throttle of
23.09.2026 says one post per account per day, and this is the newer
instruction standing against it.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import sys

import psycopg2

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
# agents/ as well, because agents/moltbook_poster.py reaches for its
# neighbour `activity` by bare name. Importing it as `agents.moltbook_poster`
# fails outside pytest for exactly that reason.
sys.path.insert(0, _HERE)

from app import board as _board            # noqa: E402
from app import notify                     # noqa: E402
from app import tagesbericht_eingang  # noqa: E402 - ops NOTIFY-3c
from app import paths                      # noqa: E402

# DB_NAME is what the test suite redirects (tests/conftest.py points it at the
# sandbox), DATABASE_URL is what the other cron agents read. Honouring both
# means a run against the sandbox is one variable away and cannot be confused
# with a run against live.
DB_URL = os.environ.get("DATABASE_URL") or (
    f"dbname={os.environ.get('DB_NAME', 'moltstack')} user=moltstack")

SUBMOLT = "agents"

# Why nothing is sent to taskmarket. Printed on every run, so the state is
# read off the log rather than remembered.
TASKMARKET_BLOCKED = (
    "taskmarket: not posted — no free broadcast surface (CLI 1.13.0 and the "
    "openapi document have none; /emails/broadcast is admin-only, every task "
    "write costs USDC). Needs Lars' decision."
)

HEARTBEAT_FILE = "board_digest_heartbeat.json"

log = logging.getLogger("board-digest")


def _configure_logging() -> None:
    """Called from main(), not at import.

    `logging.basicConfig` at module level is an effect on load, and
    `scripts/module_effects.py` is right to count it: a scanner that imports
    this file would reconfigure the logging of whatever imported it.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def write_heartbeat(status: str, detail: str = "") -> None:
    try:
        path = paths.ensure(paths.data(HEARTBEAT_FILE))
        with open(path, "w") as f:
            json.dump({"timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       "status": status, "detail": detail}, f)
    except Exception as e:                                    # pragma: no cover
        log.warning("heartbeat not written: %s", type(e).__name__)


def read_board(now: datetime.datetime) -> list[dict]:
    """The open entries, freshest first — the same order `GET /board` serves.

    Read with the plain driver and the same predicate as `app/board.active`,
    not through the HTTP endpoint: a cron run that depends on the API being up
    reports an outage of the API as an empty board, and those are different
    facts.
    """
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT did, offers, seeks, note, posted_at, expires_at "
                "  FROM board_entries WHERE expires_at > %s "
                " ORDER BY expires_at DESC, did ASC",
                (now,),
            )
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def post_to_moltbook(title: str, body: str) -> str | None:
    """Post the line, after the content rule that guards every Moltbook write."""
    from moltbook_poster import content_violations, create_post

    violations = content_violations(title, body)
    if violations:
        # Not softened and not sent. The rule is Moltbook's terms, and a text
        # that breaks them is a defect in the text.
        log.error("content rule refused the digest: %s", "; ".join(violations))
        return None
    return create_post(SUBMOLT, title, body)


def run(dry_run: bool = False) -> int:
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        entries = read_board(now)
    except Exception as e:
        log.error("board not read: %s: %s", type(e).__name__, e)
        write_heartbeat("error", f"board not read: {type(e).__name__}")
        notify.send_telegram(f"board digest: board not read ({type(e).__name__})",
                             channel=notify.ALERTS)
        return 1

    log.info(TASKMARKET_BLOCKED)

    try:
        title, body = _board.digest_text(entries)
    except _board.Rejected as quiet:
        # The declared quiet state. No post, no Telegram — the heartbeat and
        # this line are the record, and `config/expectations.yaml` names both.
        log.info("board digest: nothing to post — %s", quiet)
        write_heartbeat("ok", f"quiet: {quiet}")
        return 0

    if dry_run:
        log.info("dry run, nothing sent")
        print(title)
        print(body)
        write_heartbeat("ok", f"dry run, {len(entries)} entries")
        return 0

    post_id = post_to_moltbook(title, body)
    if post_id is None:
        write_heartbeat("error", "moltbook post failed")
        notify.send_telegram("board digest: Moltbook post failed", channel=notify.ALERTS)
        return 1

    log.info("board digest posted: %s", post_id)
    write_heartbeat("ok", f"posted {len(entries)} entries as {post_id}")
    # A measurement, not a finding: it goes into the daily report (ops NOTIFY-3c).
    tagesbericht_eingang.ablegen(
        "board_digest",
        f"Brett: {len(entries)} Eintraege, Moltbook-Post {post_id}. {TASKMARKET_BLOCKED}")
    return 0


def main() -> int:
    _configure_logging()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="build the line and print it, send nothing")
    args = ap.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
