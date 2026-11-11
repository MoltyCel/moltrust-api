"""Error output hygiene for scripts/peer_review.py (ops PR-KEYERR, 20.10.2026).

Zwei Regeln, beide in dieser Datei gewacht: Zugangsdaten gehen zu Gemini im
Header x-goog-api-key, nicht in der Query, und ein Fehlertext traegt keine
URL-Query -- rec["error"] laeuft in Konsole, run.log, PANEL.md und
SUMMARY_JSON.
"""
import json

import httpx
import pytest

from scripts import peer_review as pr

DUMMY = "AIzaSy-DUMMY-KEY-DO-NOT-USE-0123456789"


def _mock(handler, monkeypatch):
    """Put a MockTransport under the module-level httpx.get/post calls."""
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(pr.httpx, "get", client.get)
    monkeypatch.setattr(pr.httpx, "post", client.post)
    return client


def test_pick_gemini_sends_key_in_header(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["header"] = request.headers.get("x-goog-api-key")
        return httpx.Response(200, json={"models": [
            {"name": "models/gemini-9.9-pro",
             "supportedGenerationMethods": ["generateContent"]}]})

    _mock(handler, monkeypatch)
    best, _ = pr.pick_gemini(DUMMY)

    assert best == "gemini-9.9-pro"
    assert seen["header"] == DUMMY
    assert DUMMY not in seen["url"]
    assert "key=" not in seen["url"]


def test_call_gemini_sends_key_in_header(monkeypatch):
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers.get("x-goog-api-key")))
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 2,
                              "totalTokenCount": 3}})

    _mock(handler, monkeypatch)
    text, usage, web = pr.call_gemini(DUMMY, "gemini-9.9-pro", "prompt")

    assert text == "ok"
    assert usage["total"] == 3
    assert web is True
    url, header = seen[0]
    assert header == DUMMY
    assert DUMMY not in url


def test_call_gemini_retry_without_tools_keeps_header(monkeypatch):
    """Der Zweitversuch ohne google_search darf den Schluessel nicht verlieren."""
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((request.headers.get("x-goog-api-key"), "tools" in body))
        if "tools" in body:
            return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}})
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
            "usageMetadata": {}})

    _mock(handler, monkeypatch)
    text, _, web = pr.call_gemini(DUMMY, "gemini-9.9-pro", "prompt")

    assert text == "ok"
    assert web is False
    assert [h for h, _ in seen] == [DUMMY, DUMMY]
    assert [t for _, t in seen] == [True, False]


def test_run_provider_error_carries_no_key(monkeypatch, tmp_path):
    """Der volle Fehlerpfad: HTTP 429 bei Gemini, Dummy-Key in keiner Ausgabe."""
    def handler(request):
        return httpx.Response(429, json={
            "error": {"status": "RESOURCE_EXHAUSTED", "code": 429,
                      "message": "quota exceeded"}})

    _mock(handler, monkeypatch)
    rec = pr.run_provider("gemini", pr.PROVIDERS["gemini"], DUMMY, "prompt", tmp_path)

    assert rec["status"] == "error"
    assert rec["http_status"] == 429
    assert rec["error_type"] == "RESOURCE_EXHAUSTED"
    ausgabe = json.dumps(rec)
    assert DUMMY not in ausgabe
    assert "key=" not in ausgabe
    assert "?" not in rec["error"] or "?<redacted>" in rec["error"]


@pytest.mark.parametrize("roh,erwartet", [
    ("HTTPStatusError: 429 for url 'https://x.test/v1/m?key=SECRET'",
     "HTTPStatusError: 429 for url 'https://x.test/v1/m?<redacted>'"),
    ("ReadTimeout: https://x.test/v1/m", "ReadTimeout: https://x.test/v1/m"),
    ("RuntimeError: empty response body", "RuntimeError: empty response body"),
])
def test_scrub_drops_query_keeps_rest(roh, erwartet):
    assert pr._scrub(roh) == erwartet


def test_scrub_negativfall_laesst_fragezeichen_im_text(monkeypatch):
    """Ein Fragezeichen ohne URL davor ist keine Query und bleibt stehen."""
    assert pr._scrub("RuntimeError: no output_text (status=None)?") == \
        "RuntimeError: no output_text (status=None)?"
