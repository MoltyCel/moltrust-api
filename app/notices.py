"""Append one finding to the queue the collected report reads.

`agents/supervision.py` routes its own findings through `queue_notice`, which
writes this file. A producer outside supervision needs the same door: on
2026-10-05 `voice_gate.refresh_docs` and the content_scout pipeline swallowed a
failed mirror fetch by design ("best-effort"), so a revoked token looked like
health in every signal they produced.

Deliberately not a sender. The collected report at 08:00 and 18:00 CEST is the
consumer; a producer that called Telegram itself would be the second sender the
volume rule exists to prevent.
"""
from __future__ import annotations

import datetime
import json
import os

from app import paths


def note(check: str, detail: str, light: str = "red") -> bool:
    """Queue one finding. Returns False when it could not be written.

    Never raises: a producer reporting a failure must not fail on the report.
    The caller logs either way, so a lost row is visible in the log.
    """
    try:
        target = paths.ensure(paths.data("notices.jsonl"))
        row = {
            "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "check": check,
            "light": light,
            "detail": detail,
            "exception": None,          # collected, never an immediate message
            "sent_immediately": False,
        }
        with open(target, "a") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        os.chmod(target, 0o640)
        return True
    except Exception:
        return False
