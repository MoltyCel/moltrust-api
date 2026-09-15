"""content_scout hold list + reach filter (feat/content-scout-reach-hold).

No network, no database: GitHub is mocked with httpx.MockTransport / stub fetchers,
the queue with an in-memory fake connection. Hold-list entries are synthetic
placeholders only (zzq-alpha, zzq-beta).
"""
import json
import logging
import os
import sys

import asyncpg
import httpx
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from workers.content_scout import config, hold, llm, pipeline, pull, reach, telegram  # noqa: E402

HOLD_TEXT = """# synthetic test list
T1 | zzq alpha
T2 | re:zzq[-_ ]?beta\\s+gamma
zzq delta
T9 | re:([unclosed
"""


def _stats(stars, contributors, error=None):
    return lambda owner, repo, token: {"stars": stars, "contributors": contributors, "error": error}


def _cache(stars=5000, contributors=50, error=None):
    return reach.ReachCache("tok", fetch=_stats(stars, contributors, error))


# --- hold list -----------------------------------------------------------------------

def test_hold_list_parse_ids_regex_and_invalid_entry(tmp_path, caplog):
    p = tmp_path / "hold.txt"
    p.write_text(HOLD_TEXT, encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        hl = hold.load(p)
    assert len(hl) == 3  # invalid regex T9 skipped
    assert "T9" in caplog.text and "zzq" not in caplog.text
    assert hl.match("Proposal: ZZQ-Alphas for everyone") == "T1"
    assert hl.match("the zzq_beta   gamma layer") == "T2"
    assert hl.match("a zzq delta here") == "L4"
    assert hl.match("nothing restricted here, xzzq alpha") is None


def test_held_routing_records_entry_id_not_text(tmp_path):
    p = tmp_path / "hold.txt"
    p.write_text(HOLD_TEXT, encoding="utf-8")
    hl = hold.load(p)
    d = pipeline.route_pass_item("bigorg/bigrepo#1", "Title\nbody mentions zzq alpha",
                                 hl, _cache(stars=50000, contributors=900))
    assert d["state"] == pipeline.STATE_HELD
    assert d["reach"]["hold"] == "T1"
    assert d["reach"]["stars"] == 50000  # reach still recorded for held items
    assert "zzq" not in json.dumps(d["reach"]).lower()


def test_missing_hold_list_fails_safe(tmp_path, caplog):
    with caplog.at_level(logging.ERROR):
        hl = hold.load(tmp_path / "does-not-exist")
    assert hl is None
    assert "HOLD LIST MISSING" in caplog.text
    d = pipeline.route_pass_item("bigorg/bigrepo#1", "harmless text", hl, _cache(stars=50000))
    assert d["state"] == pipeline.STATE_HELD
    assert d["reach"]["hold"] == hold.LIST_MISSING


def test_empty_hold_list_fails_safe(tmp_path):
    p = tmp_path / "hold.txt"
    p.write_text("# only comments\n\n", encoding="utf-8")
    assert hold.load(p) is None


def test_hold_list_path_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv(config.HOLD_LIST_ENV, str(tmp_path / "x"))
    assert config.hold_list_path() == tmp_path / "x"
    monkeypatch.delenv(config.HOLD_LIST_ENV)
    assert config.hold_list_path() == config.HOLD_LIST_DEFAULT


def test_unavailable_text_is_held():
    hl = hold.parse("T1 | zzq alpha")
    d = pipeline.route_pass_item("o/r#1", "", hl, _cache(), text_ok=False)
    assert d["state"] == pipeline.STATE_HELD and d["reach"]["hold"] == hold.TEXT_UNAVAILABLE


# --- reach ---------------------------------------------------------------------------

EMPTY = hold.parse("T1 | zzq alpha")


@pytest.mark.parametrize("stars,contribs", [(999, 500), (50000, 19), (10, 1)])
def test_reach_discard_below_threshold(stars, contribs):
    d = pipeline.route_pass_item("tiny/repo#3", "fine text", EMPTY, _cache(stars, contribs))
    assert d["state"] == "discarded"
    assert d["reach"]["tier"] == reach.TIER_LOW
    assert "below reach threshold" in d["reach"]["reason"]


def test_reach_mid_is_normal_pending():
    d = pipeline.route_pass_item("org/repo#3", "fine text", EMPTY, _cache(1000, 20))
    assert d["state"] == "pending_review" and d["reach"]["label"] == "mid"


def test_standards_org_bypass():
    d = pipeline.route_pass_item("W3C/some-spec#9", "fine text", EMPTY, _cache(12, 2))
    assert d["state"] == "pending_review"
    assert d["reach"]["bypass"] is True and d["reach"]["label"] == "deferred-high"


def test_deferred_high_at_10k():
    d = pipeline.route_pass_item("org/repo#3", "fine text", EMPTY, _cache(10000, 20))
    assert d["state"] == "pending_review" and d["reach"]["label"] == "deferred-high"
    d = pipeline.route_pass_item("org/repo2#3", "fine text", EMPTY, _cache(9999, 20))
    assert d["reach"]["label"] == "mid"


def test_reach_unknown_on_api_error(caplog):
    with caplog.at_level(logging.WARNING):
        d = pipeline.route_pass_item("org/repo#3", "fine text", EMPTY,
                                     _cache(None, None, "repo lookup HTTP 401"))
    assert d["state"] == "pending_review"
    assert d["reach"]["label"] == "reach-unknown"
    assert "REACH UNKNOWN" in caplog.text


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_counts_contributors_from_link_header():
    def handler(req):
        if req.url.path.endswith("/contributors"):
            assert req.url.params["per_page"] == "1" and req.url.params["anon"] == "true"
            link = ('<https://api.github.com/repositories/1/contributors?per_page=1&anon=true&page=2>; rel="next", '
                    '<https://api.github.com/repositories/1/contributors?per_page=1&anon=true&page=347>; rel="last"')
            return httpx.Response(200, json=[{"login": "a"}], headers={"link": link})
        return httpx.Response(200, json={"stargazers_count": 61234})
    out = reach.fetch_repo_reach("o", "r", "tok", client=_mock_client(handler))
    assert out == {"stars": 61234, "contributors": 347, "error": None}


def test_fetch_single_contributor_without_link():
    def handler(req):
        if req.url.path.endswith("/contributors"):
            return httpx.Response(200, json=[{"login": "solo"}])
        return httpx.Response(200, json={"stargazers_count": 3})
    out = reach.fetch_repo_reach("o", "r", "tok", client=_mock_client(handler))
    assert out["contributors"] == 1 and out["error"] is None


@pytest.mark.parametrize("status", [401, 403, 429])
def test_fetch_api_error_is_unknown_not_discarded(status):
    out = reach.fetch_repo_reach("o", "r", "tok",
                                 client=_mock_client(lambda req: httpx.Response(status, json={})))
    assert out["error"] and out["stars"] is None
    assert reach.assess("o", out)["tier"] == reach.TIER_UNKNOWN


def test_fetch_too_large_contributor_list_counts_as_enough():
    def handler(req):
        if req.url.path.endswith("/contributors"):
            return httpx.Response(403, json={"message": "The history or contributor list is too large to list contributors for this repository via the API."})
        return httpx.Response(200, json={"stargazers_count": 150000})
    out = reach.fetch_repo_reach("o", "r", "tok", client=_mock_client(handler))
    assert out["error"] is None and out["contributors"] is None
    assert reach.assess("o", out)["tier"] == reach.TIER_HIGH


def test_reach_cache_one_lookup_per_repo():
    calls = []

    def fetch(owner, repo, token):
        calls.append((owner, repo))
        return {"stars": 2000, "contributors": 30, "error": None}
    cache = reach.ReachCache("tok", fetch=fetch)
    cache.lookup("Org/Repo#1"); cache.lookup("org/repo#2"); cache.lookup("org/other#1")
    assert calls == [("Org", "Repo"), ("org", "other")]


def test_non_github_target_is_na():
    d = pipeline.route_pass_item("some article title", "fine text", EMPTY, _cache())
    assert d["state"] == "pending_review" and d["reach"]["tier"] == reach.TIER_NA


def test_card_shows_reach_numbers_and_label():
    r = {"id": 7, "target": "org/repo#3", "source_ref": "https://github.com/org/repo/issues/3",
         "class_reason": "why", "lead_point": "pt",
         "reach": json.dumps(reach.assess("org", {"stars": 61234, "contributors": 347, "error": None}))}
    msg = pipeline._lead_message(r)
    assert "deferred-high" in msg and "61,234 stars" in msg and "347 contributors" in msg
    assert "obscure" not in msg


# --- end-to-end run: held items never reach Telegram --------------------------------

class FakeConn:
    """In-memory stand-in for the asyncpg connection used by pipeline.run()."""

    def __init__(self, schema_has_reach=True):
        self.rows = []
        self.schema_has_reach = schema_has_reach

    async def execute(self, sql, *args):
        if sql.strip().startswith("INSERT"):
            if "reach" in sql and not self.schema_has_reach:
                raise asyncpg.exceptions.UndefinedColumnError('column "reach" does not exist')
            row = dict(zip(["source", "source_ref", "classification", "class_reason", "draft_type",
                            "target", "draft_md", "lead_point", "verify_status", "model_used",
                            "tokens_in", "tokens_out", "cost_est", "state", "code_flag", "reach"], args))
            if row["state"] not in ("pending_review", "approved", "discarded", "published") \
                    and not self.schema_has_reach:
                e = asyncpg.exceptions.CheckViolationError("state check")
                e.constraint_name = "content_review_queue_state_check"
                raise e
            row["id"] = len(self.rows) + 1
            row["created_at"] = None
            row["notified_at"] = None
            self.rows.append(row)
            return "INSERT 0 1"
        if sql.strip().startswith("UPDATE"):
            for r in self.rows:
                if r["id"] == args[0]:
                    r["notified_at"] = "now"
            return "UPDATE 1"
        return "OK"

    async def fetch(self, sql, *args):
        if "reach" in sql and not self.schema_has_reach:
            raise asyncpg.exceptions.UndefinedColumnError('column "reach" does not exist')
        return [r for r in self.rows
                if r["state"] == "pending_review" and r["draft_type"] == "gh_lead"
                and r["lead_point"] is not None and r["notified_at"] is None]

    async def close(self):
        pass


CANDS = [
    {"source": "discovery", "ref": "https://github.com/big/one/issues/1", "title": "About zzq alpha",
     "target": "big/one#1"},
    {"source": "discovery", "ref": "https://github.com/big/two/issues/2", "title": "Plain topic",
     "target": "big/two#2"},
    {"source": "discovery", "ref": "https://github.com/tiny/three/issues/3", "title": "Plain topic",
     "target": "tiny/three#3"},
]


def _wire(monkeypatch, conn, hold_path, sent, summaries):
    monkeypatch.setattr(config, "load_secrets", lambda: {"GH_TOKEN": "tok"})
    monkeypatch.setattr(config, "anthropic_key", lambda s: "k")
    monkeypatch.setattr(config, "hold_list_path", lambda: hold_path)
    monkeypatch.setattr(llm, "make_client", lambda key: object())
    monkeypatch.setattr(llm, "balance_ok", lambda client: True)
    monkeypatch.setattr(llm, "classify", lambda client, sys_, text: {"verdict": "PASS", "reason": "r"})
    monkeypatch.setattr(llm, "point", lambda client, sys_, user: ("a point", "claude-haiku-4-5"))

    async def connect(secrets):
        return conn

    async def seen_refs(c):
        return set()
    monkeypatch.setattr(pipeline.db, "connect", connect)
    monkeypatch.setattr(pipeline.db, "seen_refs", seen_refs)
    monkeypatch.setattr(pipeline, "ingest", lambda seen: [dict(c) for c in CANDS])
    monkeypatch.setattr(pull, "pull_discovery", lambda url, tok: "# body\n\nno restricted words")
    sizes = {"big": (40000, 200), "tiny": (4, 1)}
    monkeypatch.setattr(reach, "fetch_repo_reach",
                        lambda o, r, t: {"stars": sizes[o][0], "contributors": sizes[o][1], "error": None})
    monkeypatch.setattr(telegram, "send_message",
                        lambda secrets, text, label="": sent.append(text) or [1])
    monkeypatch.setattr(telegram, "send_summary", lambda secrets, text: summaries.append(text))


async def test_run_held_gets_no_telegram_card(monkeypatch, tmp_path):
    p = tmp_path / "hold.txt"
    p.write_text("T1 | zzq alpha\n", encoding="utf-8")
    conn, sent, summaries = FakeConn(), [], []
    _wire(monkeypatch, conn, p, sent, summaries)
    tally = await pipeline.run()
    states = {r["target"]: r["state"] for r in conn.rows}
    assert states == {"big/one#1": "held", "big/two#2": "pending_review", "tiny/three#3": "discarded"}
    held = next(r for r in conn.rows if r["state"] == "held")
    assert held["lead_point"] is None and json.loads(held["reach"])["hold"] == "T1"
    assert json.loads(held["reach"])["stars"] == 40000
    assert tally["held"] == 1 and tally["below_reach"] == 1 and tally["notified"] == 1
    assert all("big/one#1" not in t for t in sent), "a held item must never be carded"
    assert any("big/two#2" in t and "deferred-high" in t for t in sent)
    assert all("zzq" not in t for t in sent + summaries)


async def test_run_missing_hold_list_holds_every_pass(monkeypatch, tmp_path):
    conn, sent, summaries = FakeConn(), [], []
    _wire(monkeypatch, conn, tmp_path / "missing", sent, summaries)
    tally = await pipeline.run()
    assert {r["state"] for r in conn.rows} == {"held"}
    assert tally["notified"] == 0 and not sent
    assert "HOLD LIST MISSING" in summaries[-1]


async def test_run_without_migration_falls_back_to_discarded(monkeypatch, tmp_path):
    p = tmp_path / "hold.txt"
    p.write_text("T1 | zzq alpha\n", encoding="utf-8")
    conn, sent, summaries = FakeConn(schema_has_reach=False), [], []
    _wire(monkeypatch, conn, p, sent, summaries)
    tally = await pipeline.run()
    states = {r["target"]: r["state"] for r in conn.rows}
    assert states == {"big/one#1": "discarded", "big/two#2": "pending_review", "tiny/three#3": "discarded"}
    held = next(r for r in conn.rows if r["target"] == "big/one#1")
    assert held["class_reason"].startswith("[held:T1")
    assert all("big/one#1" not in t for t in sent)
    assert not any("persist failed" in t for t in sent)
    assert tally["notified"] == 1
