"""The spam rate has to be read over one regime, not two.

Measured on 2026-10-04: 12 736 comments, 12 729 written before the reply rules
took effect on 01.10. and 9 763 of those marked (76.7 %), 7 written since and
none marked. The account writes about seven comments a fortnight now, so the
"last 100" window reaches back to 21.09. and 93 of its rows are pre-rule
traffic. The 63 % it reported was their number.
"""
import datetime
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "moltbook_stats", ROOT / "scripts" / "moltbook_stats.py")
mb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mb)


def test_the_cut_is_the_day_the_rules_took_effect():
    assert mb.RULES_FROM == datetime.date(2026, 10, 1)


def test_the_measurement_is_in_the_source_so_the_date_reads_as_measured():
    src = (ROOT / "scripts" / "moltbook_stats.py").read_text()
    assert "2026-10-04" in src and "12 736" in src and "76.7" in src


def test_the_old_verdict_is_gone():
    src = (ROOT / "scripts" / "moltbook_stats.py").read_text()
    # It told us to delete 500 comments. On the data that would have raised the
    # lifetime ratio from 76.7 % to 77.4 %, because the oldest 500 are 58 %
    # marked against 76.7 % overall.
    assert "500er-Löschung" not in src
    assert "klebt am Konto" not in src


def test_no_comment_since_the_cut_yields_no_rate():
    src = (ROOT / "scripts" / "moltbook_stats.py").read_text()
    assert "keine Quote" in src
    # And the branch for it comes before any division.
    body = src.split("def verdict()", 1)[1]
    assert body.index("keine Quote") < body.index("share = pct(")
