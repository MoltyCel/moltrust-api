"""FIX 4 / FIX 10 / FIX 3 — fail-closed at startup instead of fail-open at runtime.

An empty webhook secret does not switch signature checking off, it makes the
signature computable by anyone who can read the source. These tests import the
modules in a subprocess so a deliberately broken environment cannot leak into
the rest of the suite.
"""
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(code: str, **overrides) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


# ---------------------------------------------------------------------------
# FIX 4 — Stripe webhook secret
# ---------------------------------------------------------------------------
def test_billing_refuses_to_import_without_stripe_webhook_secret():
    result = _run("import app.billing", STRIPE_WEBHOOK_SECRET="")
    assert result.returncode != 0, "import succeeded with an empty webhook secret"
    assert "STRIPE_WEBHOOK_SECRET" in result.stderr
    assert "RuntimeError" in result.stderr


def test_billing_refuses_to_import_with_the_variable_unset():
    result = _run("import app.billing", STRIPE_WEBHOOK_SECRET=None)
    assert result.returncode != 0
    assert "STRIPE_WEBHOOK_SECRET" in result.stderr


def test_billing_imports_when_the_secret_is_present():
    result = _run("import app.billing", STRIPE_WEBHOOK_SECRET="whsec_testvalue")
    assert result.returncode == 0, result.stderr[-2000:]


# ---------------------------------------------------------------------------
# FIX 10 — Basescan webhook secret
# ---------------------------------------------------------------------------
def test_main_refuses_to_import_without_basescan_secret():
    result = _run("import app.main", BASESCAN_WEBHOOK_SECRET="")
    assert result.returncode != 0, "import succeeded with an empty basescan secret"
    assert "BASESCAN_WEBHOOK_SECRET" in result.stderr
    assert "RuntimeError" in result.stderr


def test_payment_webhook_has_no_unsigned_path():
    """The handler must not contain a conditional around the signature check."""
    source = open(os.path.join(REPO_ROOT, "app", "main.py")).read()
    start = source.index('@app.post("/webhooks/payment")')
    body = source[start : start + 1200]
    assert "if BASESCAN_WEBHOOK_SECRET:" not in body, "signature check is still conditional"
    assert "compare_digest" in body
    assert "@limiter.limit" in body, "webhook is still unlimited"


# ---------------------------------------------------------------------------
# FIX 3 — test harness out of production
# ---------------------------------------------------------------------------
ROUTE_DUMP = (
    "import app.main, json;"
    "print(json.dumps([getattr(r, 'path', '') for r in app.main.app.routes]))"
)


def test_harness_router_is_absent_in_production():
    result = _run(ROUTE_DUMP, MOLTRUST_ENV="production")
    assert result.returncode == 0, result.stderr[-2000:]
    routes = result.stdout.strip().splitlines()[-1]
    assert "/test-harness/invoke" not in routes
    assert "/test-harness/info" not in routes


def test_harness_router_is_present_outside_production():
    result = _run(ROUTE_DUMP, MOLTRUST_ENV="development")
    assert result.returncode == 0, result.stderr[-2000:]
    routes = result.stdout.strip().splitlines()[-1]
    assert "/test-harness/invoke" in routes


def test_endorse_endpoint_survives_in_production():
    """/test-harness/endorse is declared on the app, not the router, and is
    already auth'd and rate limited — it must keep working."""
    result = _run(ROUTE_DUMP, MOLTRUST_ENV="production")
    assert result.returncode == 0, result.stderr[-2000:]
    routes = result.stdout.strip().splitlines()[-1]
    assert "/test-harness/endorse" in routes
