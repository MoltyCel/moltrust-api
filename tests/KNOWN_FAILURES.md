# Known Test Failures — TODO Triage

Tests that fail on a clean checkout of `feature/auto-probe-token` at commit `88956b7` (or later) but are **out of scope for the current auto-probe security sprint**. Each entry pairs a working hypothesis with a deferred fix proposal. To be addressed after the sprint lands.

## How to read this file

- **Status** = current verdict. `PRE-EXISTING` means failing before sprint touched anything. `INTRODUCED` means a sprint change caused it.
- **Hypothesis** = best guess at root cause based on code inspection, not a verified diagnosis.
- **Reproduce** = the minimum command to see the failure.
- **Proposed fix** = the change that would most likely make the test pass without weakening what it asserts.

If you fix one, drop the entry and add a one-liner to the commit message: `closes KNOWN_FAILURES entry #N`.

---

## #1 — `tests/test_identity.py::test_no_key_mints_probe`

- **Status:** PRE-EXISTING (failed on `88956b7^` and on `88956b7`)
- **Exception:** `app.identity.AuthError: Probe spawn rate limit (per IP) exceeded — try again later`
- **Source of error:** `app/identity.py:160` inside `_enforce_spawn_rate`

### Hypothesis

The fixture cleans probes by UA marker only:

```python
# tests/test_identity.py:75
await conn.execute("DELETE FROM probe_agents WHERE first_seen_ua = $1", CLEAN_MARKER)
```

The rate-limit guard counts by IP, not UA:

```python
# app/identity.py:153
"SELECT COUNT(*) FROM probe_agents "
"WHERE first_seen_ip = $1::inet AND created_at > now() - interval '1 hour'"
```

When `127.0.0.1` (or whichever IP the test machine binds to) accumulates ≥ `PROBE_SPAWN_PER_IP_PER_HOUR` probes (default 5) from non-test sources — most easily through manual `curl` integration testing of the running server — the rate-limit guard fires before the test can mint its own probe. The fixture's UA-scoped cleanup never touches those manual-test probes because their UA isn't `CLEAN_MARKER`.

### Reproduce

```bash
cd ~/moltstack
set -a && source ~/.moltrust_secrets && set +a
source venv/bin/activate
pytest tests/test_identity.py::test_no_key_mints_probe -v
```

### Proposed fix (post-sprint)

Broaden `_cleanup` in `tests/test_identity.py` to drop any recent probes from the test source IP, regardless of UA:

```python
async def _cleanup(conn):
    # ... existing UA-scoped cleanup ...
    await conn.execute("DELETE FROM probe_agents WHERE first_seen_ua = $1", CLEAN_MARKER)
    # Belt-and-braces: clear any probe-spawn-rate counter rows for the test
    # IP that accumulated outside this test suite (e.g. manual curl probing).
    await conn.execute(
        "DELETE FROM probe_agents "
        "WHERE first_seen_ip = '127.0.0.1'::inet "
        "AND created_at > now() - interval '1 hour'"
    )
```

Estimated effort: ~10 minutes including a verifying pytest run.

---

## #2 — `tests/test_identity.py::test_session_id_reuses_probe`

- **Status:** PRE-EXISTING
- **Exception:** `app.identity.AuthError` (same family as #1)

### Hypothesis

Cascade from #1. The test mints a fresh probe in its first call, which hits the same per-IP cap blown by accumulated manual-test probes. Failing because the *first* mint inside the test fails, never reaching the session-reuse logic the test is actually asserting on.

### Reproduce

Same as #1.

### Proposed fix

Resolving #1 should resolve this. If not, the test's `mock_request` IP source needs to be moved to a dedicated test range (e.g., `198.18.0.0/15` — RFC 2544 test-only).

---

## #3 — `tests/test_identity.py::test_session_id_no_reuse_after_expiry`

- **Status:** PRE-EXISTING
- **Exception:** `app.identity.AuthError`

### Hypothesis

Same as #2 — cascade from #1, can't get past the first mint.

### Reproduce

Same as #1.

### Proposed fix

Same as #1 + #2.

---

## #4 — `tests/test_identity.py::test_claim_with_valid_probe_email`

- **Status:** PRE-EXISTING
- **Exception:** `app.identity.AuthError`

### Hypothesis

Cascade from #1. The test needs to mint a probe before claiming it; if mint fails the claim path is never exercised.

### Reproduce

Same as #1.

### Proposed fix

Same as #1.

---

## Notes for the post-sprint fix-loop

- All four failures share the same root cause and one fix likely closes all four.
- The hypothesis is unverified — to confirm, look at `probe_agents` rows for `first_seen_ip = '127.0.0.1'` from the last hour:
  ```sql
  SELECT first_seen_ip, first_seen_ua, COUNT(*)
  FROM probe_agents
  WHERE first_seen_ip = '127.0.0.1'::inet
    AND created_at > now() - interval '1 hour'
  GROUP BY first_seen_ip, first_seen_ua
  ORDER BY 3 DESC;
  ```
  If the rows are non-CLEAN_MARKER UAs (e.g. `audit`, `smoke`, `smithery-test`, `public-test`) accumulated from this session's manual testing, the hypothesis is confirmed.
- If the proposed fix doesn't close all four after one pytest cycle, consider that the IPv4 /24 cap (`PROBE_SPAWN_PER_SUBNET_PER_HOUR`, raised at `app/identity.py:169`) may also be saturated — broaden the cleanup query to `first_seen_ip << '127.0.0.0/24'::inet`.
- After fixing, drop this whole file or this entry block. Don't leave stale "known failure" docs lying around once they're resolved — that's how known failures become tribal knowledge.

---

**Created:** 2026-05-11 during the auto-probe-token security sprint pause
**Originating audit:** `audits/2026-05-11_working-tree-inventory.md` § Pre-existing failing tests
