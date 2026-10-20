"""Every verdict URI the ERC-8004 adapter would write has to be a route we serve.

Found 19.10.2026 (ops MC-API): the mandate template pointed at
/moltproof/verdict/{subject}, a path no service answers (404 live). The adapter
hashes the document under that URI before writing (`fetch_and_hash`), so every
mandate entry would have failed, and an entry written by hand would have carried
a dead link on-chain. No entry was ever written (erc8004_write_budget empty).
The template now names /molcheck/verdict/{subject}.

The prediction template has the same defect and belongs to MoltGuard, not to
this change; it stays marked here until its own fix lands.
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


@pytest.mark.parametrize("kind", ["score", "mandate"])
def test_verdict_uri_is_served(kind):
    assert _served(_path(kind)), _path(kind)


def test_mandate_uri_names_molcheck():
    assert _path("mandate").startswith("/molcheck/verdict/")


@pytest.mark.xfail(strict=True, reason="prediction template points at /prediction/integrity/, "
                                       "served only as /guard/prediction/integrity/ (MoltGuard)")
def test_prediction_uri_is_served():
    assert _served(_path("prediction"))


def test_the_check_rejects_a_path_nobody_serves():
    assert not _served("/moltproof/verdict/did:moltrust:0123456789abcdef")
