"""Every read against the X API goes through x_meter.get.

On 11.10.2026 the ledger was found to miss reads of our own system: the hourly
user lookup in supervision.py, follower reads in four scripts, the x_budget
probe. A meter that misses reads measures part of the bill and calls it the
bill. This test fails on any direct requests/httpx GET to an X host outside
agents/x_meter.py.

Found by the syntax tree, not by grep: the URL is often a module constant
(TWEETS_URL, PROBE_URL), and a comment that names the host is not a read.
Limit: a URL handed in as a function parameter is invisible here; reply_radar._get
is such a case and books through x_meter.get itself.
"""
import ast
import pathlib
import subprocess

WURZEL = pathlib.Path(__file__).resolve().parent.parent
X_HOSTS = ("api.twitter.com", "api.x.com")
TRANSPORT = {"get", "request"}

# A GET that X does not bill as a resource read.
ERLAUBT = {
    # Upload status of our own media (STATUS command), no post or profile.
    ("agents/x_post.py", "MEDIA_UPLOAD_URL"),
}


def _x_literal(node) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return any(h in node.value for h in X_HOSTS)
    if isinstance(node, ast.JoinedStr):
        return any(_x_literal(v) for v in node.values)
    if isinstance(node, ast.FormattedValue):
        return False
    return False


def _x_constants(tree) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _x_literal(node.value):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def _names_in(node) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def direct_x_gets(rel: str, source: str) -> list[str]:
    tree = ast.parse(source)
    consts = _x_constants(tree)
    hits = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in TRANSPORT
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"requests", "httpx"}):
            continue
        if not node.args:
            continue
        url = node.args[0]
        used = _names_in(url) & consts
        if any((rel, n) in ERLAUBT for n in used):
            continue
        if _x_literal(url) or used:
            hits.append(f"{rel}:{node.lineno}")
    return hits


def _tracked_py() -> list[str]:
    out = subprocess.run(["git", "-C", str(WURZEL), "ls-files", "*.py"],
                         capture_output=True, text=True, check=True).stdout
    return [p for p in out.split() if not p.startswith("tests/")
            and p != "agents/x_meter.py"]


def test_no_direct_x_get_outside_the_meter():
    hits = []
    for rel in _tracked_py():
        try:
            src = (WURZEL / rel).read_text()
        except (OSError, UnicodeDecodeError):
            continue
        try:
            hits += direct_x_gets(rel, src)
        except SyntaxError:
            continue
    assert not hits, "X read without x_meter.get: " + ", ".join(hits)


def test_detector_finds_literal_and_constant():
    src = ('import requests\nU = "https://api.twitter.com/2/tweets"\n'
           'requests.get(U)\nrequests.get(f"{U}/1")\n'
           'requests.get("https://api.x.com/2/users/me")\n')
    assert direct_x_gets("x.py", src) == ["x.py:3", "x.py:4", "x.py:5"]


def test_detector_leaves_other_hosts_and_comments():
    src = ('import requests, httpx\n# requests.get("https://api.twitter.com/2/x")\n'
           'httpx.get("https://api.telegram.org/bot/getMe")\n'
           'requests.post("https://api.twitter.com/2/tweets", json={})\n')
    assert direct_x_gets("y.py", src) == []


def test_get_books_profiles_on_a_user_lookup_and_posts_elsewhere(monkeypatch, tmp_path):
    import json as _json
    import requests
    from agents import x_meter

    class R:
        status_code = 200
        def __init__(self, body):
            self._b = body
        def json(self):
            return self._b

    bodies = {"me": {"data": {"id": "u1", "username": "moltrust"}},
              "tweets": {"data": [{"id": "p1"}, {"id": "p2"}],
                         "includes": {"users": [{"id": "u2"}]}}}
    monkeypatch.setattr(requests, "get",
                        lambda url, **kw: R(bodies["me" if url.endswith("/me") else "tweets"]))
    monkeypatch.setattr(x_meter, "LEDGER", str(tmp_path / "x_meter.jsonl"))
    x_meter.get("https://api.twitter.com/2/users/me", source="t")
    x_meter.get("https://api.twitter.com/2/users/1/tweets", source="t")
    rows = [_json.loads(x) for x in (tmp_path / "x_meter.jsonl").read_text().splitlines()]
    assert rows[0]["posts"] == [] and rows[0]["users"] == ["u1"]
    assert rows[1]["posts"] == ["p1", "p2"] and rows[1]["users"] == ["u2"]


def test_get_never_costs_a_call_over_an_unreadable_body(monkeypatch, tmp_path):
    import requests
    from agents import x_meter

    class R:
        status_code = 200
        text = "not json"

    monkeypatch.setattr(requests, "get", lambda url, **kw: R())
    monkeypatch.setattr(x_meter, "LEDGER", str(tmp_path / "x_meter.jsonl"))
    assert x_meter.get("https://api.twitter.com/2/users/me").status_code == 200
