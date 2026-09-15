"""scripts/discovery/discovery.py — failure paths must land in discovery_health.json.

No network: urllib.request.urlopen is replaced per test. No Telegram: the
module's send_telegram is replaced. Paths point into tmp_path via env.
"""
import importlib.util
import io
import json
import os
import urllib.error

import pytest

SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                      "scripts", "discovery", "discovery.py"))


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _load(monkeypatch, tmp_path, token="test-token"):
    monkeypatch.setenv("DISCOVERY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOVERY_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.delenv("DISCOVERY_CANDIDATES_FILE", raising=False)
    monkeypatch.delenv("DISCOVERY_HEALTH_FILE", raising=False)
    monkeypatch.setenv("GITHUB_PAT", token)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    spec = importlib.util.spec_from_file_location("discovery_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sent = []
    monkeypatch.setattr(mod, "send_telegram", lambda msg: sent.append(msg))
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return mod, sent


def _health(tmp_path):
    return json.loads((tmp_path / "discovery_health.json").read_text())


def _http_error(url, code):
    return urllib.error.HTTPError(url, code, "err", {}, None)


def test_import_does_not_exit_without_token(monkeypatch, tmp_path):
    mod, _ = _load(monkeypatch, tmp_path, token="")
    assert mod.HEALTH_FILE == tmp_path / "discovery_health.json"
    with pytest.raises(SystemExit):
        mod.main()
    assert _health(tmp_path)["consecutive_failures"] == 1


def test_search_errors_record_failure_and_keep_feed(monkeypatch, tmp_path):
    """/user answers 200, every search 401s -> failure recorded, feed untouched."""
    mod, _ = _load(monkeypatch, tmp_path)
    feed = tmp_path / "discovery_candidates.json"
    feed.write_text(json.dumps({"candidates": []}))
    before = feed.stat().st_mtime_ns

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if url.endswith("/user"):
            return _Resp(b"{}")
        raise _http_error(url, 401)

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 1
    h = _health(tmp_path)
    assert h["consecutive_failures"] == 1
    assert "searches failed" in h["last_error"] and "401" in h["last_error"]
    assert feed.stat().st_mtime_ns == before


def test_partial_search_error_saves_results_but_is_a_failure(monkeypatch, tmp_path):
    mod, _ = _load(monkeypatch, tmp_path)
    calls = {"search": 0}
    item = {"repository_url": "https://api.github.com/repos/acme/widgets",
            "number": 7, "title": "agent trust", "updated_at": "2026-09-15T00:00:00Z",
            "html_url": "https://github.com/acme/widgets/issues/7"}

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if url.endswith("/user"):
            return _Resp(b"{}")
        if "/search/issues" in url:
            calls["search"] += 1
            if calls["search"] == 1:
                raise _http_error(url, 502)
            return _Resp(json.dumps({"items": [item]}).encode())
        return _Resp(b"[]")  # comments lookup

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 1
    feed = json.loads((tmp_path / "discovery_candidates.json").read_text())
    assert [c["url"] for c in feed["candidates"]] == [item["html_url"]]
    h = _health(tmp_path)
    assert h["consecutive_failures"] == 1 and "1/6 searches failed" in h["last_error"]


def test_third_consecutive_failure_alerts(monkeypatch, tmp_path):
    mod, sent = _load(monkeypatch, tmp_path)
    (tmp_path / "discovery_health.json").write_text(
        json.dumps({"consecutive_failures": 2, "last_ok": "2026-09-12"}))

    def fake_urlopen(req, timeout=None):
        raise _http_error(req.full_url, 401)

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SystemExit):
        mod.main()
    assert _health(tmp_path)["consecutive_failures"] == 3
    assert len(sent) == 1 and "failed 3 runs in a row" in sent[0]


def test_success_writes_last_ok_at(monkeypatch, tmp_path):
    mod, _ = _load(monkeypatch, tmp_path)

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if "/search/issues" in url:
            return _Resp(b'{"items": []}')
        return _Resp(b"{}")

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    mod.main()
    h = _health(tmp_path)
    assert h["consecutive_failures"] == 0 and h["last_error"] is None
    assert h["last_ok"] and h["last_ok_at"].endswith("+00:00")
