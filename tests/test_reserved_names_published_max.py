"""The reserved-names guard follows the highest published AAE revision.

Origin (ops PIN-AAE03, 16.11.2026): -03 is on the datatracker since
2026-11-15, and the guard still treated a bare "-03" next to a revision word
as unpublished, which blocked the commit that pins the citation source to it.
The next number stays reserved. It is assembled here so this file does not
carry it.
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "reserved_names_guard", ROOT / ".github" / "scripts" / "reserved_names_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

NEXT = "-0" + str(guard.PUBLISHED_MAX + 1)


def test_published_max_is_three():
    assert guard.PUBLISHED_MAX == 3


def test_the_published_revision_passes_next_to_a_revision_word():
    assert not guard.reserved("current revision -03, see §2.2")
    assert not guard.reserved("-03 (current, published)", "docs/spec-fakten/aae.md")


def test_the_next_revision_stays_reserved():
    assert guard.reserved(f"revision {NEXT} candidates")
    assert guard.reserved(f"{NEXT} notes", "docs/spec-fakten/aae.md")


def test_dates_and_versions_are_not_revisions():
    assert not guard.reserved("revision of 2026-11-05", "docs/spec-fakten/aae.md")
    assert not guard.reserved("draft version 1.0-07")
