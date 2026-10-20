import datetime as dt

from scripts import checkout_writes as cw

NOW = dt.datetime(2026, 10, 11, 8, 0, tzinfo=dt.timezone.utc)


def _reflog(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(
        f"{'a'*40} {'b'*40} Lars Kroehl <lars@moltrust.ch> {int((NOW - dt.timedelta(hours=h)).timestamp())} +0000\t{m}\n"
        for h, m in rows))


def test_a_fetch_without_the_deploy_tag_is_named(tmp_path):
    g = tmp_path / "co" / ".git"
    _reflog(g / "logs" / "refs" / "remotes" / "origin" / "main", [
        (5, "deploy.sh moltrust-api abc: fast-forward"),
        (3, "fetch -q origin main: fast-forward"),
        (1, "deploy.sh moltrust-api def: fast-forward")])
    out = cw.lines(NOW, {"moltrust-api": str(tmp_path / "co")})
    assert "moltrust-api 1" in out[0]
    assert "fetch -q origin main" in out[1]


def test_entries_before_tagging_went_live_are_not_judged(tmp_path):
    g = tmp_path / "co" / ".git"
    _reflog(g / "logs" / "HEAD", [(10, "merge dad2317: Fast-forward"),
                                  (2, "deploy.sh moltrust-api x: Fast-forward")])
    assert "moltrust-api 0" in cw.lines(NOW, {"moltrust-api": str(tmp_path / "co")})[0]


def test_a_reflog_without_any_tag_yet_is_not_judged(tmp_path):
    g = tmp_path / "co" / ".git"
    _reflog(g / "logs" / "HEAD", [(2, "checkout: moving from main to x")])
    assert "moltrust-api 0" in cw.lines(NOW, {"moltrust-api": str(tmp_path / "co")})[0]


def test_missing_checkout_is_a_line(tmp_path):
    out = cw.lines(NOW, {"moltrust-web": str(tmp_path / "nope")})
    assert "moltrust-web ?" in out[0]
