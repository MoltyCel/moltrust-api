"""Contact submissions are personal data and must not accumulate forever."""
import re

import agents.retention_cleanup as retention


def test_retention_is_twelve_months():
    assert retention.CONTACT_RETENTION_MONTHS == 12


def test_deletes_from_contact_inbox_by_age():
    src = open(retention.__file__).read()
    assert "DELETE FROM contact_inbox" in src
    assert "received_at <" in src


def test_interval_is_a_bound_parameter_not_a_format_string():
    """A retention window spliced into SQL is how an injection gets in, and it
    is also what makes bandit shout. prune_rollups binds it; so does this."""
    src = open(retention.__file__).read()
    block = src[src.index("DELETE FROM contact_inbox"):][:300]
    assert "$1::int" in block
    assert "%s months" not in block


def test_count_is_logged():
    src = open(retention.__file__).read()
    assert "Contact retention: %d row(s)" in src


def test_failure_does_not_abort_the_run():
    """request_log is pruned before this; a missing table must not make a
    successful prune look like a failed job."""
    src = open(retention.__file__).read()
    block = src[src.index("contact_deleted = 0"):src.index("_record(ok=True")]
    assert "except Exception" in block
    assert "Contact retention skipped" in block


def test_digest_record_carries_both_counts():
    """Since ops KO1 the counts go to the selftest digest, not to Telegram."""
    src = open(retention.__file__).read()
    assert "request_log=deleted, contact_inbox=contact_deleted" in src
