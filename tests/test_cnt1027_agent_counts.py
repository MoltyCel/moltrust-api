"""Dashboard, /swarm/stats and the digest show the same registered count (ops CNT-1027).

On 27.10.2026 the admin dashboard said 691 and the daily stats mail said 628.
Both were right about what they counted: the dashboard and /swarm/stats ran a
bare COUNT(*) over agents, the digest read app/sql/agent_counts.sql, which
leaves out revoked DIDs. The difference was exactly the 63 revoked ones.

Fixture: one live DID and one revoked DID in an `agents` table of their own.
A schema that shadows public.agents through search_path keeps the numbers
absolute -- the shared sandbox database carries whatever other suites left in
it, and "revoked = 1" is only a statement if nothing else can be in the table.
Every other table the routes read falls through to public.

/stats is not tested here: its agents_total stays a bare count by decision
(Lars, 01.11.2026, Variante A; see the comment under stats["agents_total"]).
"""
import os
import re
import subprocess
import sys
import uuid

import asyncpg
import pytest
import pytest_asyncio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQL = os.path.join(ROOT, "app", "sql", "agent_counts.sql")


def _did() -> str:
    return f"did:moltrust:{uuid.uuid4().hex[:16]}"


@pytest_asyncio.fixture
async def counts_schema(app_with_lifespan, monkeypatch):
    """A pool whose `agents` holds one live and one revoked DID, nothing else."""
    import app.main as m

    schema = f"cnt1027_{uuid.uuid4().hex[:8]}"
    kw = dict(host=os.getenv("DB_HOST", "localhost"),
              database=os.getenv("DB_NAME", "moltstack_sandbox"),
              user="moltstack", password=os.getenv("MOLTSTACK_DB_PW") or None)
    admin = await asyncpg.connect(**kw)
    await admin.execute(f"CREATE SCHEMA {schema}")
    await admin.execute(
        f"CREATE TABLE {schema}.agents (LIKE public.agents INCLUDING DEFAULTS)")
    # Production has last_active_at; the CI schema does not, and the dashboard
    # reads it. Added to the shadow table only.
    await admin.execute(
        f"ALTER TABLE {schema}.agents ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMP")
    # /swarm/stats reads MAX(propagation_depth) from swarm_graph, a live table
    # no migration in this repository creates (2026-10-08_sandbox_schema_parity
    # names it as left out). The one column read, in the shadow schema only.
    await admin.execute(
        f"CREATE TABLE {schema}.swarm_graph (propagation_depth INTEGER)")
    live, revoked = _did(), _did()
    await admin.execute(
        f"INSERT INTO {schema}.agents (did, display_name, platform, agent_type) "
        "VALUES ($1, 'cnt1027-live', 'moltbook', 'external')", live)
    await admin.execute(
        f"INSERT INTO {schema}.agents (did, display_name, platform, agent_type, "
        "revoked_at, revocation_reason) "
        "VALUES ($1, 'cnt1027-revoked', 'moltbook', 'external', now(), 'test')",
        revoked)

    pool = await asyncpg.create_pool(
        **kw, min_size=1, max_size=2,
        server_settings={"search_path": f"{schema},public"})
    monkeypatch.setattr(m, "db_pool", pool)
    try:
        yield schema
    finally:
        await pool.close()
        await admin.execute(f"DROP SCHEMA {schema} CASCADE")
        await admin.close()


async def test_swarm_stats_counts_registered_and_revoked(counts_schema, async_client):
    r = await async_client.get("/swarm/stats")
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body["total_agents"] == 1, body
    assert body["revoked"] == 1, body


async def test_dashboard_counts_registered_and_revoked(counts_schema, async_client,
                                                       monkeypatch):
    import subprocess as _sp
    import app.main as m

    # The route checks the TLS certificate of moltrust.ch with openssl; not
    # under test, and a runner should not depend on reaching it.
    def _no_openssl(*a, **kw):
        raise OSError("not in tests")
    monkeypatch.setattr(_sp, "run", _no_openssl)

    monkeypatch.setitem(m.ADMIN_USERS, "t-cnt1027", {"hash": "$2b$12$x", "role": "admin"})
    token, _ = m.create_session("t-cnt1027")
    try:
        r = await async_client.get("/admin/dashboard/overview",
                                   headers={"Authorization": f"Bearer {token}"})
    finally:
        m.invalidate_session(token)
    assert r.status_code == 200, r.text[:300]
    agents = r.json()["agents"]
    assert agents["total"] == 1, agents
    assert agents["revoked"] == 1, agents


def _digest_columns() -> list[str]:
    """The variable names daily_stats.sh reads the SQL row into, in order."""
    with open(os.path.join(ROOT, "scripts", "daily_stats.sh"), encoding="utf-8") as f:
        sh = f.read()
    m = re.search(r"IFS='\|' read -r ([A-Z_ ]+) < <\(\s*\n\s*psql [^\n]*\$AGENT_COUNTS_SQL", sh)
    assert m, "daily_stats.sh no longer reads agent_counts.sql with IFS='|' read"
    return m.group(1).split()


async def test_digest_block_shows_registered_and_revoked(counts_schema):
    """The digest path as daily_stats.sh runs it: psql -A over the SQL file,
    split on '|', rendered by scripts/zahl_block.py."""
    from app import zahl_deklaration as zd

    env = dict(os.environ, PGOPTIONS=f"-c search_path={counts_schema},public",
               PGPASSWORD=os.getenv("MOLTSTACK_DB_PW", ""))
    try:
        out = subprocess.run(
            ["psql", "-h", os.getenv("DB_HOST", "localhost"), "-U", "moltstack",
             "-d", os.getenv("DB_NAME", "moltstack_sandbox"), "-t", "-A", "-f", SQL],
            check=True, capture_output=True, text=True, env=env).stdout.strip()
    except FileNotFoundError:
        pytest.skip("psql not installed")

    werte = dict(zip(_digest_columns(), out.split("|")))
    assert werte["REGISTERED"] == "1", werte
    assert werte["REVOKED"] == "1", werte

    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "zahl_block.py"),
         "--stichtag", "2026-11-02 07:00 UTC", "--fenster", werte["ACTIVE_WINDOW"],
         "--registered", werte["REGISTERED"], "--revoked", werte["REVOKED"],
         "--active", werte["ACTIVE"], "--test", werte["TEST_AGENTS"],
         "--partner-test", werte["PARTNER_TEST"]],
        check=False, capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    zeilen = r.stdout.splitlines()
    assert any(z.startswith("  registered 1 — ") for z in zeilen), r.stdout
    assert any(z.startswith("  revoked 1 — ") for z in zeilen), r.stdout
    assert zd.undeklarierte(r.stdout + "\nCredentials: 1\n") == []


def test_digest_passes_revoked_to_the_block():
    """A column that is read and never handed on sends nothing."""
    with open(os.path.join(ROOT, "scripts", "daily_stats.sh"), encoding="utf-8") as f:
        sh = f.read()
    assert "REVOKED" in _digest_columns()
    assert '--revoked "$REVOKED"' in sh
