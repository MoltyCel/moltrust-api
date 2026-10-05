"""One paid place per worker address across a round, decided by time.

Round 3 earned this: ten worker addresses held all forty paid places across the
four stage-1 tasks, four each, because the cap was per task. The round 4 text
promises "your first qualifying submission takes your place; later submissions
on the other tasks are qualified and unpaid".
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "task_watch", ROOT / "scripts" / "task_watch.py")
tw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tw)


def e(addr, at, did=None):
    return {"addr": addr, "addr_lc": addr.lower(), "at": at,
            "did": did or f"did:moltrust:{addr[-16:]}"}


def test_an_address_is_paid_once_across_the_round():
    per_task = {
        "A": [e("0xaa", "2026-10-05T10:00:00Z"), e("0xbb", "2026-10-05T10:05:00Z")],
        "B": [e("0xaa", "2026-10-05T10:01:00Z"), e("0xcc", "2026-10-05T10:06:00Z")],
        "C": [e("0xaa", "2026-10-05T10:02:00Z")],
    }
    out = tw.round_cap(per_task)
    # 0xaa submitted earliest on A, so it holds its place there and nowhere else.
    assert [x["addr_lc"] for x in out["A"][0]] == ["0xaa", "0xbb"]
    assert [x["addr_lc"] for x in out["B"][0]] == ["0xcc"]
    assert [x["addr_lc"] for x in out["C"][0]] == []
    assert out["B"][1] == 1 and out["C"][1] == 1 and out["A"][1] == 0


def test_the_earliest_submission_wins_not_the_earliest_task():
    # 0xaa was earliest on C, so C keeps it even though A is listed first.
    per_task = {
        "A": [e("0xaa", "2026-10-05T12:00:00Z")],
        "C": [e("0xaa", "2026-10-05T09:00:00Z")],
    }
    out = tw.round_cap(per_task)
    assert [x["addr_lc"] for x in out["C"][0]] == ["0xaa"]
    assert out["A"][0] == [] and out["A"][1] == 1


def test_every_address_appears_exactly_once_in_total():
    per_task = {
        "A": [e("0xaa", "t1"), e("0xbb", "t2"), e("0xcc", "t3")],
        "B": [e("0xaa", "t4"), e("0xbb", "t5")],
        "C": [e("0xaa", "t6"), e("0xdd", "t7")],
    }
    out = tw.round_cap(per_task)
    placed = [x["addr_lc"] for kept, _ in out.values() for x in kept]
    assert sorted(placed) == ["0xaa", "0xbb", "0xcc", "0xdd"]
    assert len(placed) == len(set(placed))


def test_the_three_round_four_tasks_share_one_round_key():
    r4 = [t for t in tw.TASKS if t.get("round") == "r4"]
    assert len(r4) == 3
    assert {t["ref"] for t in r4} == {"STUFE1-R4-1", "STUFE1-R4-2", "TIEFE-R4"}
    assert all(t["cap"] == 1 for t in r4)
    assert {t["profile"] for t in r4} == {"stage1", "offscript"}


def test_round_three_tasks_carry_no_round_key_and_keep_their_per_task_cap():
    # Retroactive capping would change a payout that is already on chain.
    for t in tw.TASKS:
        if t["ref"].startswith("STUFE1-") and not t["ref"].endswith(("R4-1", "R4-2")):
            assert "round" not in t, t["ref"]


def test_the_offscript_profile_has_an_eligibility_function():
    assert "offscript" in tw.ELIGIBLE
    src = (ROOT / "scripts" / "task_watch.py").read_text()
    # It must read the shared list, not a copy of it.
    assert "from agents.proof_post import SCRIPTED_ENDPOINTS" in src
    assert "SCRIPTED_ENDPOINTS ist leer" in src
