"""Security report: which findings alert (ops SEC-1025-API, 25.10.2026)."""
import importlib.util
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


he = _load("security_http_errors")
pa = _load("pip_audit_split")

OWN = "46.225.175.0"   # request_log keeps the /24 only


# -- HTTP errors --------------------------------------------------------------

def test_402_is_never_reported():
    assert he.report([("/api/agent/score/:address", 402, "1.2.3.4", 3414)]) == []


def test_429_from_our_own_probe_is_info_with_the_split():
    # 25.10.2026: 33 answers 429 per hour on /x402/verify, all to the hourly
    # invariant c-no-endpoint-affirms-a-revoked-did running on this host.
    out = he.report([("/x402/verify", 429, OWN, 825)])  # noqa: E501
    assert out == ["INFO /x402/verify 825x HTTP 429 in 24h (own 825, foreign 0; top foreign: none)"]


def test_401_from_one_foreign_net_is_info_with_its_24():
    rows = [("/admin/me", 401, "13.212.123.10", 300),
            ("/admin/me", 401, "13.212.123.11", 47),
            ("/admin/me", 401, "169.155.235.5", 3)]
    out = he.report(rows)
    assert out == ["INFO /admin/me 350x HTTP 401 in 24h (own 0, foreign 350; "
                   "top foreign: 13.212.123.0/24 347, 169.155.235.0/24 3)"]


def test_a_500_still_alerts_with_the_split():
    rows = [("/identity/resolve", 500, "8.8.8.8", 15), ("/identity/resolve", 500, OWN, 10)]
    assert he.report(rows) == ["ALERT /identity/resolve 25x HTTP 500 in 24h "
                               "(own 10, foreign 15; top foreign: 8.8.8.0/24 15)"]


def test_a_4xx_from_our_own_check_is_info():
    # 25.10.2026: moltrust-artefact-check, 50 GET/HEAD a day on a POST route.
    rows = [("/delegation/create", 405, OWN, 50), ("/delegation/create", 405, "51.102.230.0", 2),
            ("/delegation/create", 405, "43.166.253.0", 1)]
    assert he.report(rows) == ["INFO /delegation/create 53x HTTP 405 in 24h (own 50, foreign 3; "
                               "top foreign: 51.102.230.0/24 2, 43.166.253.0/24 1)"]


def test_a_4xx_from_foreign_callers_alerts():
    assert he.report([("/x", 422, "8.8.8.0", 21)])[0].startswith("ALERT /x 21x HTTP 422")


def test_loopback_counts_as_own():
    assert he.is_own("127.0.0.1") and he.is_own("::1") and he.is_own("46.225.175.218")
    assert not he.is_own("46.225.176.1") and not he.is_own("garbage")


def test_at_or_below_the_threshold_stays_silent():
    # Schweigeprobe: normal traffic of 20 errors on one endpoint says nothing.
    assert he.report([("/credentials/verify", 400, "8.8.8.8", 20)]) == []


def test_top_foreign_lists_three_nets_only():
    rows = [("/x", 404, f"10.0.{i}.1", 10 + i) for i in range(5)]
    line = he.report(rows)[0]
    assert line.count("/24") == 3 and "10.0.4.0/24 14" in line and "10.0.1.0/24" not in line


def test_busy_info_lines_do_not_push_an_alert_out():
    rows = [(f"/own{i}", 429, OWN, 1000 + i) for i in range(6)] + [("/broken", 500, "8.8.8.0", 21)]
    out = he.report(rows)
    assert out[0].startswith("ALERT /broken 21x HTTP 500")
    assert sum(line.startswith("INFO") for line in out) == 5


def test_parse_skips_malformed_rows():
    rows = list(he.parse(["/a|429|1.2.3.4|7", "", "garbage", "/b|x|1.2.3.4|3"]))
    assert rows == [("/a", 429, "1.2.3.4", 7)]


# -- pip-audit split ----------------------------------------------------------

def _dep(name, version, *vulns):
    return {"name": name, "version": version,
            "vulns": [{"id": vid, "fix_versions": fixes} for vid, fixes in vulns]}


def test_closure_follows_requirements_and_skips_extras():
    graph = {"fastapi": ["starlette>=1", "pydantic"], "starlette": ["anyio"],
             "pydantic": ["email-validator; extra == 'email'"]}
    got = pa.closure(["fastapi"], requires=lambda n: graph.get(n, []))
    assert got == {"fastapi", "starlette", "pydantic", "anyio"}


def test_a_fixable_production_advisory_alerts_with_the_needed_version():
    deps = [_dep("aiohttp", "3.14.1", ("A", ["3.14.3"]), ("B", ["3.14.4", "4.0.0"]))]
    out = pa.classify(deps, {"aiohttp"})
    assert out == ["ALERT pip-audit: production dependencies with a fixed release: "
                   "aiohttp 3.14.1 -> 3.14.4 (2)"]


def test_tooling_and_unfixed_advisories_do_not_alert():
    # 25.10.2026 after the upgrade: only pip and filelock (pulled in by
    # pip-audit itself) were left, neither reached from requirements.txt.
    deps = [_dep("pip", "26.1.2", ("P1", ["26.2"])),
            _dep("filelock", "3.24.3", ("F1", ["4.2.1"])),
            _dep("pyjwt", "2.15.1", ("PYSEC-X", [])),
            _dep("fastapi", "0.136.3")]
    out = pa.classify(deps, {"pyjwt", "fastapi"})
    assert not any(line.startswith("ALERT") for line in out)
    assert "INFO pip-audit: production advisories without a fixed release: pyjwt 2.15.1 (PYSEC-X)" in out
    assert "INFO pip-audit: tooling in the venv, not reached from requirements.txt: pip 26.1.2 (1); filelock 3.24.3 (1)" in out
    assert out[-1] == "OK pip-audit: no fixable advisory in production dependencies"


def test_a_clean_audit_is_ok():
    assert pa.classify([_dep("fastapi", "0.136.3")], {"fastapi"}) == [
        "OK pip-audit: no fixable advisory in production dependencies"]


def test_top_level_reads_names_and_ignores_options():
    lines = ["# comment", "-r other.txt", "mcp>=1.30.0,<2  # note", "a2a-sdk[http-server]==1.1.2", "PyJWT>=2.15.0"]
    assert pa.top_level(lines) == ["mcp", "a2a-sdk", "pyjwt"]
