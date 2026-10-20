"""Full credentials in our own archive, only the hash on IPFS (ops IP-1).

Until IP-1 POST /credentials/issue pinned the full signed credential to IPFS.
From IP-1 on every issued credential is written to vc_archive, and IPFS gets a
document that carries only the credential's hash. CIDs published before stay;
nothing unpins or deletes them.

The unit tests need no database. The issuing tests run against the sandbox
database with migrations/2026-10-20_vc_archive.sql applied by the fixture.
"""
import hashlib
import json
import os
import pathlib

import jcs
import pytest
import pytest_asyncio

from app import vc_archive

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "2026-10-20_vc_archive.sql"
ANSWER_FIELDS = ("renew_at", "ipfs_cid", "ipfs_url")

VC = {
    "@context": ["https://www.w3.org/ns/credentials/v2"],
    "type": ["VerifiableCredential", "AgentTrustCredential"],
    "issuer": "did:web:api.moltrust.ch",
    "validFrom": "2026-10-20T00:00:00Z",
    "credentialSubject": {"id": "did:moltrust:0123456789abcdef", "verified": True},
    "proof": {"type": "DataIntegrityProof", "proofValue": "z3abc"},
}


# ------------------------------------------------------------- no database

def test_the_hash_is_sha256_over_the_jcs_form():
    assert vc_archive.vc_hash(VC) == hashlib.sha256(jcs.canonicalize(VC)).hexdigest()
    reordered = json.loads(json.dumps(VC, sort_keys=True))
    assert vc_archive.vc_hash(reordered) == vc_archive.vc_hash(VC)


def test_the_pinned_document_carries_the_hash_and_nothing_of_the_credential():
    doc = vc_archive.hash_document(VC)
    assert doc == {"vc_hash": vc_archive.vc_hash(VC), "hash_alg": "sha256-jcs"}
    text = json.dumps(doc)
    for part in ("credentialSubject", "did:moltrust:0123456789abcdef", "z3abc", "proof"):
        assert part not in text


class _Conn:
    def __init__(self, fail=False):
        self.fail = fail
        self.statements = []
        self.transactions = 0

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self):
                conn.transactions += 1

            async def __aexit__(self, *exc):
                return False
        return _Tx()

    async def execute(self, sql, *args):
        if self.fail:
            raise RuntimeError("relation \"vc_archive\" does not exist")
        self.statements.append((sql, args))


@pytest.mark.asyncio
async def test_archive_writes_one_row_in_its_own_transaction():
    conn = _Conn()
    assert await vc_archive.archive(conn, VC, "credentials/issue", ipfs_cid="bafyx") is True
    assert conn.transactions == 1
    (sql, args), = conn.statements
    assert sql.startswith("INSERT INTO vc_archive")
    assert args[0] == vc_archive.vc_hash(VC)
    assert args[1:5] == ("AgentTrustCredential", "did:moltrust:0123456789abcdef",
                         "did:web:api.moltrust.ch", "z3abc")
    assert json.loads(args[5]) == VC
    assert args[6:] == ("credentials/issue", "issued", "bafyx")


@pytest.mark.asyncio
async def test_a_failed_archive_write_never_blocks_issuance(caplog):
    assert await vc_archive.archive(_Conn(fail=True), VC, "music") is False
    assert "VC archive write failed (music)" in caplog.text


def test_nothing_unpins_or_deletes_on_ipfs():
    for path in ("app/ipfs_publisher.py", "app/vc_archive.py", "scripts/vc_archive_backfill.py"):
        src = (ROOT / path).read_text(encoding="utf-8").lower()
        for word in ("pinning/unpin", "unpin(", "method=\"delete\"", "requests.delete"):
            assert word not in src, (path, word)


def test_the_backfill_counts_and_skips_what_it_cannot_take():
    from scripts.vc_archive_backfill import plan
    old = dict(VC, credentialSubject={"id": "did:moltrust:aaaaaaaaaaaaaaaa"})
    known = dict(VC, credentialSubject={"id": "did:moltrust:bbbbbbbbbbbbbbbb"})
    docs = {"cid-old": old, "cid-hash": vc_archive.hash_document(VC),
            "cid-known": known, "cid-gone": None}
    rows = [(1, old, "cid-old"), (2, None, "cid-hash"), (3, known, "cid-known"),
            (4, VC, "cid-gone")]
    counts, writes = plan(rows, {vc_archive.vc_hash(known)}, fetcher=docs.get)
    assert counts == {"candidates": 4, "readable": 3, "unreadable": 1,
                      "not_a_credential": 1, "equal_to_raw_vc": 2,
                      "already_archived": 1, "to_write": 1}
    (row,) = writes
    assert row[0] == vc_archive.vc_hash(old)
    assert row[6:] == ("credentials.ipfs_cid", "ipfs-backfill", "cid-old")


# ---------------------------------------------------------- sandbox database

@pytest.fixture(autouse=True)
def _key(monkeypatch):
    # The credential key falls back to ~/.moltrust_did_private_key when this is
    # unset; on the server that file is the real issuer key.
    monkeypatch.setenv("DID_PRIVATE_KEY_HEX", os.urandom(32).hex())
    monkeypatch.delenv("DID_PRIVATE_KEY_ENCRYPTED", raising=False)


@pytest.fixture(autouse=True)
def _no_ip_limit():
    from app.main import limiter
    previous = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = previous


@pytest_asyncio.fixture
async def archive_db(app_with_lifespan):
    from app.main import db_pool
    async with db_pool.acquire() as conn:
        await conn.execute(MIGRATION.read_text())
    yield db_pool


def _doc(value):
    return value if isinstance(value, dict) else json.loads(value)


def _signed(answer):
    return {k: v for k, v in answer.items() if k not in ANSWER_FIELDS}


@pytest.mark.asyncio
async def test_an_issued_credential_is_archived_and_only_its_hash_is_pinned(
        async_client, archive_db, credit_test_agent, monkeypatch):
    import app.main as m
    pinned = []
    monkeypatch.setattr(m, "publish_to_ipfs", lambda doc: pinned.append(doc) or "bafyhashonly")
    did, key = await credit_test_agent()
    r = await async_client.post("/credentials/issue", json={"subject_did": did},
                                headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    answer = r.json()
    vc = _signed(answer)

    assert pinned == [{"vc_hash": vc_archive.vc_hash(vc), "hash_alg": "sha256-jcs"}]
    assert answer["ipfs_cid"] == "bafyhashonly"

    async with archive_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT raw_vc, source, origin, ipfs_cid, subject_did FROM vc_archive "
            "WHERE vc_hash = $1", vc_archive.vc_hash(vc))
        stored_cid = await conn.fetchval(
            "SELECT ipfs_cid FROM credentials WHERE subject_did = $1 ORDER BY id DESC LIMIT 1", did)
    assert row is not None
    assert _doc(row["raw_vc"]) == vc
    assert (row["source"], row["origin"], row["ipfs_cid"], row["subject_did"]) == (
        "credentials/issue", "issued", "bafyhashonly", did)
    assert stored_cid == "bafyhashonly"


@pytest.mark.asyncio
async def test_without_ipfs_the_credential_is_still_archived(
        async_client, archive_db, credit_test_agent, monkeypatch):
    import app.main as m
    monkeypatch.setattr(m, "publish_to_ipfs", lambda doc: None)
    did, key = await credit_test_agent()
    r = await async_client.post("/credentials/issue", json={"subject_did": did},
                                headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    answer = r.json()
    assert "ipfs_cid" not in answer
    async with archive_db.acquire() as conn:
        cid = await conn.fetchval("SELECT ipfs_cid FROM vc_archive WHERE vc_hash = $1",
                                  vc_archive.vc_hash(_signed(answer)))
        found = await conn.fetchval("SELECT count(*) FROM vc_archive WHERE vc_hash = $1",
                                    vc_archive.vc_hash(_signed(answer)))
    assert found == 1 and cid is None


@pytest.mark.asyncio
async def test_the_archived_credential_verifies(async_client, archive_db, credit_test_agent,
                                                monkeypatch):
    import app.main as m
    monkeypatch.setattr(m, "publish_to_ipfs", lambda doc: "bafyhashonly")
    did, key = await credit_test_agent()
    r = await async_client.post("/credentials/issue", json={"subject_did": did},
                                headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    async with archive_db.acquire() as conn:
        raw = await conn.fetchval("SELECT raw_vc FROM vc_archive WHERE vc_hash = $1",
                                  vc_archive.vc_hash(_signed(r.json())))
    v = await async_client.post("/credentials/verify", json={"credential": _doc(raw)})
    assert v.status_code == 200, v.text
    assert v.json()["valid"] is True, v.json()
