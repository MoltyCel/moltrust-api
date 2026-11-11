"""scripts/check_unpublished_aae_revision.py (ops PUB-04).

The revision number is joined in at runtime, as in the script, so this file does
not name it either.
"""
import pathlib

from scripts import check_unpublished_aae_revision as c

N = "0" + "4"
REPO = pathlib.Path(__file__).resolve().parent.parent


def test_names_of_the_revision_are_hits():
    for line in (
        f"Kritische Regel (Taxonomie / AAE draft-{N} §2.3):",
        f"# Normative types (taxonomy / AAE draft-{N} §2.3).",
        f"**Source:** AAE draft-{N} §2.2–2.4 (local working revision)",
        f"see draft-kroehl-agentic-trust-aae-{N}",
        f"material for aae-{N} lives elsewhere",
        f"AAE -{N} adds a section",
        f"Die lokale Arbeitsrevision „-{N}\" war inhaltsgleich",
        f"the „-{N}“ text",
    ):
        assert c.hits_in(line), line


def test_published_revisions_and_third_party_drafts_are_not():
    for line in (
        "draft-kroehl-agentic-trust-aae-02, posted 2026-09-06",
        "AAE -02 §2.3",
        f"| UCR | draft-agentic-ai-usecases-requirements-{N} | revision {N} only |",
        f"AHU cites UCR-{N}.",
        f"posted 2026-{N}-17",
        f"--hash=sha256:0014eaff8ad5b3b43feda2279f9d34bf2eaae{N}0720b9fbbb5",
        f"version 1.{N}.2",
    ):
        assert not c.hits_in(line), line


def test_hits_carry_the_line_number():
    text = "first\nAAE -02 §5\n" + f"AAE draft-{N} §5\n"
    assert c.hits_in(text) == [(3, f"AAE draft-{N} §5")]


def test_this_repository_names_none():
    found = c.scan(str(REPO))
    assert found == [], "\n".join(found)
