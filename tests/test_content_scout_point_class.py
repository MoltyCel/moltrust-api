"""A NONCONFORMANCE label has to carry its three locators, or it is downgraded.

The three cases below are the three ways October's leads went wrong. 711 asserted a
contradiction against a rule that appears nowhere in the repository it accused; 754
compared a negotiation step against an acceptance path that does not exist. Both
were labelled unverified and still read as accusations, which is what the label
distinction and this check are for.
"""
import pytest

from workers.content_scout.pipeline import enforce_point_class

GOOD = (
    "NONCONFORMANCE: the task router mutates lifecycle state the roadmap reserves "
    "to Kanban "
    "[hermes_cluster/routers/tasks.py:48 in kvnloo/hermes-agent-cluster@a5655cc] "
    "[claims: README.md:165 lists the lifecycle endpoints as stable features]"
)


def test_a_complete_nonconformance_survives():
    out, reason = enforce_point_class(GOOD)
    assert out == GOOD
    assert reason is None


def test_without_path_and_line_it_is_downgraded():
    point = ("NONCONFORMANCE: the task router mutates lifecycle state the roadmap "
             "reserves to Kanban [claims: README.md:165 lists them as stable]")
    out, reason = enforce_point_class(point)
    assert out.startswith("OBSERVATION: ")
    assert "the task router mutates" in out
    assert "no [path:line in owner/repo@ref] locator" in reason


def test_without_a_ref_on_the_locator_it_is_downgraded():
    """A path and line without the ref they were read at is not checkable later:
    the line moves and the claim cannot be reproduced."""
    point = ("NONCONFORMANCE: the task router mutates lifecycle state "
             "[hermes_cluster/routers/tasks.py:48 in kvnloo/hermes-agent-cluster] "
             "[claims: README.md:165 lists them as stable]")
    out, reason = enforce_point_class(point)
    assert out.startswith("OBSERVATION: ")
    assert "no @<ref>" in reason, reason


def test_without_a_claim_locator_it_is_downgraded():
    """A public specification binds nobody. Without a place where the counterparty
    adopts the document, there is no contract to be non-conformant with."""
    point = ("NONCONFORMANCE: no interface negotiation before accepting a peer "
             "delegation "
             "[packages/peer/src/accept.ts:12 in Entif-AI/Rosetta@e7c8bbf]")
    out, reason = enforce_point_class(point)
    assert out.startswith("OBSERVATION: ")
    assert "claims" in reason


def test_an_observation_passes_through_untouched():
    point = "OBSERVATION: the thread quotes a rule that is not in the repository."
    out, reason = enforce_point_class(point)
    assert out == point
    assert reason is None


@pytest.mark.parametrize("point", ["", None, "   "])
def test_empty_input_is_not_a_nonconformance(point):
    out, reason = enforce_point_class(point)
    assert out == point
    assert reason is None


def test_the_label_check_is_case_insensitive_but_the_body_is_kept():
    point = ("nonconformance: the router mutates lifecycle state "
             "[claims: README.md:165]")
    out, reason = enforce_point_class(point)
    assert out == "OBSERVATION: the router mutates lifecycle state [claims: README.md:165]"
    assert reason
