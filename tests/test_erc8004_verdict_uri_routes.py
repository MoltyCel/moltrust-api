"""Every verdict URI the ERC-8004 adapter would write has to be a route we serve.

Found 19.10.2026 (ops MC-API): the mandate template pointed at
/moltproof/verdict/{subject}, a path no service answers (404 live). The adapter
hashes the document under that URI before writing (`fetch_and_hash`), so every
mandate entry would have failed, and an entry written by hand would have carried
a dead link on-chain. No entry was ever written (erc8004_write_budget empty).
The template now names /molcheck/verdict/{subject}.

The prediction template had the same defect (ops MG-URI, 27.10.2026): MoltGuard
serves the figures only as /guard/prediction/integrity/ behind x402. This API
now serves a free, hash-stable copy at /prediction/integrity/{market_id}
(app/prediction_integrity.py), and the template points there.
"""
from urllib.parse import urlsplit

import pytest
from starlette.routing import Match

import app.main as main
from app import erc8004_reputation as rep


def _served(path: str, method: str = "GET") -> bool:
    scope = {"type": "http", "path": path, "method": method, "root_path": "",
             "query_string": b"", "headers": []}
    return any(r.matches(scope)[0] == Match.FULL for r in main.app.router.routes)


def _path(kind: str) -> str:
    return urlsplit(rep.verdict_uri(kind, "did:moltrust:0123456789abcdef")).path


@pytest.mark.parametrize("kind", sorted(rep.VERDICT_KINDS))
def test_verdict_uri_is_served(kind):
    assert _served(_path(kind)), _path(kind)


def test_mandate_uri_names_molcheck():
    assert _path("mandate").startswith("/molcheck/verdict/")


def test_prediction_uri_is_served():
    assert _served(_path("prediction"))
    assert _path("prediction").startswith("/prediction/integrity/")


def test_the_check_rejects_a_path_nobody_serves():
    assert not _served("/moltproof/verdict/did:moltrust:0123456789abcdef")
