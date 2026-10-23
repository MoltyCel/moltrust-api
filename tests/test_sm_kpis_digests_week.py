"""The weekly sum of digest impressions in the Sunday KPI report.

The report named the best digest of the week and nothing else, so the channel
had a peak and no series: six posts at 20 impressions and one at 120 read the
same. The sum turns the week into a figure the next week can be held against.

Two rules carry it, and both are testable without the network: one digest
measured twice is one digest, and a digest without a measurement is not a
digest with 0 impressions.

sm_kpis imports psycopg2, requests_oauthlib and app.notify at module scope, so
the function under test is lifted out of the file rather than imported, the way
tests/test_sm_kpis_moltbook_spam.py does it.
"""
import datetime
import json
import logging
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts" / "sm_kpis.py").read_text()

_ns: dict = {"json": json, "datetime": datetime,
             "log": logging.getLogger("test_digests_week")}
_SRC_FN = re.search(r"^def digests_in_window.*?(?=\n\n# The two share:copy)",
                    SRC, re.M | re.S).group(0)
# The signature carries `dict | None`, which is evaluated at definition time
# below Python 3.10. The future import keeps the lifted copy readable on the
# interpreter that runs the tests.
exec(compile("from __future__ import annotations\n" + _SRC_FN, "<f>", "exec"),
     _ns)
digests_in_window = _ns["digests_in_window"]

NOW = datetime.datetime(2026, 10, 22, 18, 0, tzinfo=datetime.timezone.utc)
SINCE = NOW - datetime.timedelta(days=7)


def _row(tweet_id, measured_at, impressions=37, **kw):
    row = {"kind": "digest", "tweet_id": tweet_id,
           "measured_at": measured_at, "digest_date": measured_at[:10]}
    if impressions is not None:
        row["impressions"] = impressions
    row.update(kw)
    return row


def _file(tmp_path, rows):
    p = tmp_path / "digest_metrics.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    _ns["METRICS_FILE"] = str(p)
    return p


class TestEachDigestCountsOnce:
    def test_two_measurements_of_one_digest_are_one_post(self, tmp_path):
        """The later value replaces the earlier one. 12 + 37 is not the week."""
        _file(tmp_path, [
            _row("111", "2026-10-20T12:00:00+00:00", 12),
            _row("111", "2026-10-20T18:00:00+00:00", 37),
        ])
        got = digests_in_window(SINCE)
        assert got == {"posts": 1, "impressions": 37, "unmeasured": 0}

    def test_the_later_row_wins_regardless_of_file_order(self, tmp_path):
        _file(tmp_path, [
            _row("111", "2026-10-20T18:00:00+00:00", 37),
            _row("111", "2026-10-20T12:00:00+00:00", 12),
        ])
        assert digests_in_window(SINCE)["impressions"] == 37

    def test_six_digests_add_up(self, tmp_path):
        _file(tmp_path, [
            _row(str(i), f"2026-10-{16 + i}T18:00:00+00:00", 10 * i)
            for i in range(1, 7)
        ])
        got = digests_in_window(SINCE)
        assert got["posts"] == 6
        assert got["impressions"] == 10 + 20 + 30 + 40 + 50 + 60


class TestADigestWithoutAMeasurement:
    def test_counts_as_a_post_and_is_reported_apart(self, tmp_path):
        _file(tmp_path, [
            _row("111", "2026-10-20T18:00:00+00:00", 37),
            _row("222", "2026-10-21T18:00:00+00:00", None),
        ])
        got = digests_in_window(SINCE)
        assert got == {"posts": 2, "impressions": 37, "unmeasured": 1}

    def test_an_explicit_null_is_the_same_case(self, tmp_path):
        _file(tmp_path, [_row("222", "2026-10-21T18:00:00+00:00", None)])
        got = digests_in_window(SINCE)
        assert got == {"posts": 1, "impressions": 0, "unmeasured": 1}

    def test_zero_impressions_is_a_measurement_not_a_gap(self, tmp_path):
        """A post nobody saw is a finding. It is not the same as no reading."""
        _file(tmp_path, [_row("222", "2026-10-21T18:00:00+00:00", 0)])
        got = digests_in_window(SINCE)
        assert got == {"posts": 1, "impressions": 0, "unmeasured": 0}


class TestWhatDoesNotCount:
    def test_reply_and_video_rows_stay_out(self, tmp_path):
        _file(tmp_path, [
            _row("111", "2026-10-20T18:00:00+00:00", 37),
            {"kind": "reply", "tweet_id": "999",
             "measured_at": "2026-10-20T18:00:00+00:00", "impressions": 500},
            {"kind": "video", "clip": "a",
             "measured_at": "2026-10-20T18:00:00+00:00", "impressions": 700},
        ])
        got = digests_in_window(SINCE)
        assert got == {"posts": 1, "impressions": 37, "unmeasured": 0}

    def test_a_row_without_kind_is_a_digest(self, tmp_path):
        """Rows from before 2026-09-22 carry no kind, as top_from_metrics_file
        already assumes."""
        _file(tmp_path, [{"tweet_id": "111", "impressions": 37,
                          "measured_at": "2026-10-20T18:00:00+00:00"}])
        assert digests_in_window(SINCE)["posts"] == 1

    def test_rows_before_the_window_stay_out(self, tmp_path):
        _file(tmp_path, [
            _row("old", "2026-10-01T18:00:00+00:00", 999),
            _row("111", "2026-10-20T18:00:00+00:00", 37),
        ])
        got = digests_in_window(SINCE)
        assert got == {"posts": 1, "impressions": 37, "unmeasured": 0}

    def test_broken_lines_do_not_stop_the_count(self, tmp_path):
        p = tmp_path / "digest_metrics.jsonl"
        p.write_text("{not json\n"
                     + json.dumps(_row("111", "2026-10-20T18:00:00+00:00", 37))
                     + "\n")
        _ns["METRICS_FILE"] = str(p)
        assert digests_in_window(SINCE)["impressions"] == 37

    def test_a_row_without_a_timestamp_stays_out(self, tmp_path):
        _file(tmp_path, [{"kind": "digest", "tweet_id": "111",
                          "impressions": 37}])
        assert digests_in_window(SINCE)["posts"] == 0


class TestTheQuietCases:
    def test_a_missing_file_is_zero_not_a_failure(self, tmp_path):
        _ns["METRICS_FILE"] = str(tmp_path / "nothing.jsonl")
        assert digests_in_window(SINCE) == {"posts": 0, "impressions": 0,
                                           "unmeasured": 0}

    def test_an_unreadable_file_reports_nothing_rather_than_zero(self,
                                                                 tmp_path):
        """A read that failed is not a week with no digests."""
        d = tmp_path / "a-directory.jsonl"
        d.mkdir()
        _ns["METRICS_FILE"] = str(d)
        assert digests_in_window(SINCE) is None

    def test_an_empty_window_is_zero_posts(self, tmp_path):
        _file(tmp_path, [_row("old", "2026-10-01T18:00:00+00:00", 999)])
        assert digests_in_window(SINCE) == {"posts": 0, "impressions": 0,
                                           "unmeasured": 0}
