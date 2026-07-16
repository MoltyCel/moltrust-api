"""content_scout persist resilience (fix/content-scout-lead-constraint).

Two guarantees pinned here:
  1. after the constraint fix, a draft_type='gh_lead' row persists;
  2. a row with an invalid draft_type triggers a Telegram alert and is skipped —
     it does NOT raise out of _persist_or_alert (which is what silently killed the
     whole run 2x/day: the raw exception propagated out of the per-candidate loop).

Integration against the sandbox DB via conftest's test_db fixture.
"""
import os
import sys
import uuid

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from workers.content_scout import pipeline, telegram  # noqa: E402

_FIX_CONSTRAINT = (
    "ALTER TABLE content_review_queue DROP CONSTRAINT IF EXISTS "
    "content_review_queue_draft_type_check",
    "ALTER TABLE content_review_queue ADD CONSTRAINT "
    "content_review_queue_draft_type_check "
    "CHECK (draft_type IN ('gh_comment','blog_post','none','gh_lead'))",
)


def _row(draft_type, ref):
    return {"source": "test", "source_ref": ref, "classification": "pass",
            "class_reason": "test", "draft_type": draft_type, "target": "test",
            "draft_md": None, "lead_point": "one-line verifiable point",
            "model_used": "claude-haiku-4-5", "tokens_in": 1, "tokens_out": 1,
            "cost_est": 0.0}


@pytest_asyncio.fixture
async def queue_conn(test_db):
    """Sandbox connection with the constraint fix applied (idempotent)."""
    for stmt in _FIX_CONSTRAINT:
        await test_db.execute(stmt)
    yield test_db


async def test_gh_lead_persists(queue_conn):
    ref = f"test://gh_lead/{uuid.uuid4().hex}"
    try:
        ok = await pipeline._persist_or_alert(queue_conn, _row("gh_lead", ref), {})
        assert ok is True
        got = await queue_conn.fetchval(
            "SELECT draft_type FROM content_review_queue WHERE source_ref=$1", ref)
        assert got == "gh_lead"
    finally:
        await queue_conn.execute(
            "DELETE FROM content_review_queue WHERE source_ref=$1", ref)


async def test_invalid_draft_type_alerts_and_does_not_raise(queue_conn, monkeypatch):
    ref = f"test://bogus/{uuid.uuid4().hex}"
    sent = []
    monkeypatch.setattr(telegram, "send_message",
                        lambda secrets, text, label="": sent.append((text, label)) or [])
    # An invalid value would raise CheckViolationError from _persist; the wrapper
    # must catch it, alert, and return False — never propagate.
    ok = await pipeline._persist_or_alert(queue_conn, _row("bogus_type", ref), {})
    assert ok is False
    assert len(sent) == 1, "exactly one alert must fire"
    assert ref in sent[0][0] and "persist failed" in sent[0][0].lower()
    # nothing was written
    n = await queue_conn.fetchval(
        "SELECT count(*) FROM content_review_queue WHERE source_ref=$1", ref)
    assert n == 0
