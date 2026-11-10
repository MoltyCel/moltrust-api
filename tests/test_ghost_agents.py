"""Card and table "Ghost Agents" read one rule (ops GHOST-1110).

Origin: Lars 10.11.2026, the admin overview showed the card "Ghost Agents" 578
and under it "No ghost agents". The card counted every row, the 18 revoked
ghosts included; the table filtered ghosts out of the 100 most recently active
agents, where none can be. Read-only against production on 10.11.: the new
count is 561, the new list returns 100 ghosts, the old list held 0.

The SQL needs PostgreSQL, so the rule is held on the text: both queries carry
the revocation filter and the same window, the overview uses the count, and the
list only answers ghosts when asked for them.
"""
import ast
import pathlib
import re

SRC = (pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")


def consts():
    env = {}
    for n in ast.parse(SRC).body:
        if (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                and n.targets[0].id in ("GHOST_DAYS", "GHOST_COUNT_SQL", "_DASHBOARD_AGENT_COLUMNS",
                                         "DASHBOARD_AGENTS_SQL", "DASHBOARD_GHOSTS_SQL")):
            env[n.targets[0].id] = eval(compile(ast.Expression(n.value), "main", "eval"), {}, env)
    return env


def norm(sql):
    return " ".join(sql.split())


def test_count_and_list_carry_the_same_rule():
    c = consts()
    rule = "revoked_at IS NULL AND COALESCE(last_active_at, created_at) < NOW() - INTERVAL '30 days'"
    assert rule in norm(c["GHOST_COUNT_SQL"])
    assert rule in norm(c["DASHBOARD_GHOSTS_SQL"])
    assert f"INTERVAL '{c['GHOST_DAYS']} days'" in c["GHOST_COUNT_SQL"]


def test_the_plain_list_is_unchanged_and_has_no_ghost_filter():
    c = consts()
    assert "WHERE" not in c["DASHBOARD_AGENTS_SQL"].split("FROM agents", 1)[1]


def test_the_overview_counts_with_the_shared_rule():
    body = SRC[SRC.index('@app.get("/admin/dashboard/overview")'):SRC.index('@app.get("/admin/dashboard/agents")')]
    assert "ghost_count = await conn.fetchval(GHOST_COUNT_SQL)" in body
    assert not re.search(r"ghost_count = await conn\.fetchval\(\s*\"SELECT", body)


def test_the_list_answers_ghosts_only_when_asked():
    body = SRC[SRC.index('async def dashboard_agents'):SRC.index('@app.get("/admin/dashboard/agent-clusters")')]
    assert 'ghosts = status == "ghost"' in body
    assert "DASHBOARD_GHOSTS_SQL if ghosts else DASHBOARD_AGENTS_SQL" in body
    assert '"total": total' in body
