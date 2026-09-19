#!/usr/bin/env python3
"""Funnel test — five ways in, measured from outside.

Spec: docs/funnel-test.md. Run this from a GitHub Actions runner or a fresh box,
never from api.moltrust.ch: a funnel measured from inside measures a warm cache
and a key that is already in the environment.

    python3 scripts/funnel_test.py --classes K5,K2,K3,K1 --out report.json

K4 needs a funded wallet and is opt-in (--classes K4), because it spends real
USDC from the one address CLAUDE.md carves out of the wallet gate.

Nothing here prints a secret. Keys arrive through the environment and are used,
never echoed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict

API = os.environ.get("FUNNEL_API_BASE", "https://api.moltrust.ch")
TEST_PLATFORM = "test"


@dataclass
class Step:
    name: str
    ok: bool
    seconds: float
    detail: str = ""


@dataclass
class ClassResult:
    key: str
    label: str
    steps: list[Step] = field(default_factory=list)
    discovery_ok: bool | None = None
    discovery_seconds: float | None = None
    signup_without_human: bool | None = None
    seconds_to_first_200: float | None = None
    credits_after: int | None = None
    vc_issued: bool | None = None
    anchor_tx: str | None = None
    did: str | None = None
    payment: dict | None = None
    error: str | None = None

    def add(self, name: str, ok: bool, seconds: float, detail: str = "") -> Step:
        s = Step(name, ok, round(seconds, 3), detail)
        self.steps.append(s)
        return s

    @property
    def passed(self) -> bool:
        return self.error is None and all(s.ok for s in self.steps)


def _req(method: str, path_or_url: str, **kw):
    """Thin requests wrapper that returns (status, json_or_text, seconds)."""
    import requests

    url = path_or_url if path_or_url.startswith("http") else f"{API}{path_or_url}"
    kw.setdefault("timeout", 45)
    t0 = time.monotonic()
    r = requests.request(method, url, **kw)
    dt = time.monotonic() - t0
    try:
        body = r.json()
    except Exception:
        body = r.text[:400]
    return r.status_code, body, dt


# ── K5 — Dev / SDK ───────────────────────────────────────────────────────────

def run_k5() -> ClassResult:
    res = ClassResult("K5", "Dev / SDK")
    try:
        t0 = time.monotonic()
        code, body, dt = _req("GET", "https://pypi.org/pypi/moltrust/json")
        res.discovery_ok = code == 200
        res.discovery_seconds = round(dt, 3)
        res.add("pypi metadata", code == 200, dt,
                f"version {body.get('info', {}).get('version')}" if code == 200 else str(code))

        t = time.monotonic()
        pip = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "moltrust"],
            capture_output=True, text=True,
        )
        res.add("pip install moltrust", pip.returncode == 0, time.monotonic() - t,
                (pip.stderr or pip.stdout)[-200:] if pip.returncode else "")

        email = f"funnel-{uuid.uuid4().hex[:12]}@moltrust.test"
        code, body, dt = _req("POST", "/auth/signup", json={"email": email})
        ok = code == 200 and body.get("status") == "created"
        res.signup_without_human = ok
        res.add("signup by email", ok, dt, f"{code} {body.get('status')}")
        if not ok:
            res.error = f"signup returned {code}: {body}"
            return res
        api_key = body["api_key"]
        res.seconds_to_first_200 = round(time.monotonic() - t0, 3)

        code, body, dt = _req(
            "POST", "/identity/register",
            headers={"X-API-Key": api_key},
            json={"display_name": f"funnel-k5-{uuid.uuid4().hex[:6]}", "platform": TEST_PLATFORM},
        )
        ok = code == 200
        res.add("identity/register", ok, dt, str(code))
        if not ok:
            res.error = f"register returned {code}: {body}"
            return res
        res.did = body.get("did")

        code, body, dt = _req("GET", f"/credits/balance/{res.did}", headers={"X-API-Key": api_key})
        res.credits_after = body.get("balance") if code == 200 else None
        res.add("credits balance", code == 200, dt, f"balance {res.credits_after}")

        before = res.credits_after
        code, body, dt = _req(
            "POST", "/credentials/issue",
            headers={"X-API-Key": api_key},
            json={"subject_did": res.did, "credential_type": "AgentTrustCredential"},
        )
        res.vc_issued = code == 200
        res.add("credentials/issue (first free)", code == 200, dt, str(code))

        code, body, dt = _req("GET", f"/credits/balance/{res.did}", headers={"X-API-Key": api_key})
        after = body.get("balance") if code == 200 else None
        res.credits_after = after
        res.add("first issuance was free", before == after, dt, f"{before} -> {after}")
    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
    return res


# ── K2 — MCP client ──────────────────────────────────────────────────────────

def run_k2() -> ClassResult:
    res = ClassResult("K2", "MCP client")
    try:
        t0 = time.monotonic()
        code, body, dt = _req("GET", "https://registry.smithery.ai/servers?q=moltrust")
        found = code == 200 and any(
            s.get("qualifiedName") == "moltrust/moltrust-mcp-server" for s in body.get("servers", [])
        )
        res.discovery_ok = found
        res.discovery_seconds = round(dt, 3)
        res.add("smithery listing", found, dt, "moltrust/moltrust-mcp-server" if found else str(code))

        email = f"funnel-{uuid.uuid4().hex[:12]}@moltrust.test"
        code, body, dt = _req("POST", "/auth/signup", json={"email": email})
        ok = code == 200 and body.get("status") == "created"
        res.signup_without_human = ok
        res.add("signup for MCP key", ok, dt, str(code))
        if not ok:
            res.error = f"signup returned {code}: {body}"
            return res
        api_key = body["api_key"]

        code, body, dt = _req(
            "POST", "/mcp",
            headers={"X-API-Key": api_key, "Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        tools = []
        if isinstance(body, dict):
            tools = body.get("result", {}).get("tools", [])
        ok = code == 200 and bool(tools)
        res.seconds_to_first_200 = round(time.monotonic() - t0, 3) if ok else None
        res.add("tools/list", ok, dt, f"{len(tools)} tools" if ok else str(code)[:120])

        code, body, dt = _req(
            "POST", "/identity/register",
            headers={"X-API-Key": api_key},
            json={"display_name": f"funnel-k2-{uuid.uuid4().hex[:6]}", "platform": TEST_PLATFORM},
        )
        res.add("identity.register", code == 200, dt, str(code))
        if code != 200:
            res.error = f"register returned {code}: {body}"
            return res
        res.did = body.get("did")

        code, body, dt = _req("GET", f"/skill/trust-score/{res.did}")
        res.add("trust score", code == 200, dt, f"grade {body.get('grade')}" if code == 200 else str(code))

        code, body, dt = _req(
            "GET", "/guard/skill/audit",
            params={"url": "https://github.com/MoltyCel/moltrust-vet", "profile": "claude_skill"},
        )
        ok = code == 200
        res.add("mt_skill_audit", ok, dt, f"score {body.get('audit', {}).get('score')}" if ok else str(code))

        code, body, dt = _req("GET", f"/credits/balance/{res.did}", headers={"X-API-Key": api_key})
        res.credits_after = body.get("balance") if code == 200 else None
    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
    return res


# ── K3 — A2A agent ───────────────────────────────────────────────────────────

def run_k3() -> ClassResult:
    res = ClassResult("K3", "A2A agent")
    try:
        t0 = time.monotonic()
        code, body, dt = _req("GET", "/.well-known/agent-card.json")
        ok = code == 200 and bool(body.get("skills"))
        res.discovery_ok = ok
        res.discovery_seconds = round(dt, 3)
        res.add("agent card", ok, dt, f"{len(body.get('skills', []))} skills" if ok else str(code))

        code, body, dt = _req(
            "POST", "/a2a",
            headers={"Content-Type": "application/json"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        res.add("a2a JSON-RPC reachable", code == 200, dt, str(code))
        res.seconds_to_first_200 = round(time.monotonic() - t0, 3) if code == 200 else None

        email = f"funnel-{uuid.uuid4().hex[:12]}@moltrust.test"
        code, body, dt = _req("POST", "/auth/signup", json={"email": email})
        ok = code == 200 and body.get("status") == "created"
        res.signup_without_human = ok
        res.add("signup", ok, dt, str(code))
        if not ok:
            res.error = f"signup returned {code}: {body}"
            return res
        api_key = body["api_key"]

        code, body, dt = _req(
            "POST", "/identity/register",
            headers={"X-API-Key": api_key},
            json={"display_name": f"funnel-k3-{uuid.uuid4().hex[:6]}", "platform": TEST_PLATFORM},
        )
        res.add("register", code == 200, dt, str(code))
        if code != 200:
            res.error = f"register returned {code}: {body}"
            return res
        res.did = body.get("did")

        code, body, dt = _req(
            "POST", "/credentials/issue",
            headers={"X-API-Key": api_key},
            json={"subject_did": res.did, "credential_type": "AgentTrustCredential"},
        )
        res.vc_issued = code == 200
        res.add("credential", code == 200, dt, str(code))

        code, body, dt = _req("GET", f"/a2a/agent-card/{res.did}", headers={"X-API-Key": api_key})
        res.add("own agent card", code == 200, dt, str(code))

        code, body, dt = _req("GET", f"/credits/balance/{res.did}", headers={"X-API-Key": api_key})
        res.credits_after = body.get("balance") if code == 200 else None
    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
    return res


# ── K1 — OpenClaw ────────────────────────────────────────────────────────────

def run_k1() -> ClassResult:
    res = ClassResult("K1", "OpenClaw")
    try:
        t0 = time.monotonic()
        code, body, dt = _req("GET", "https://clawhub.ai/api/v1/skills/moltrust-vet?owner=moltycel")
        ok = code == 200
        res.discovery_ok = ok
        res.discovery_seconds = round(dt, 3)
        res.add("clawhub listing", ok, dt, "moltycel/moltrust-vet" if ok else str(code))

        t = time.monotonic()
        inst = subprocess.run(
            ["npx", "--yes", "clawhub@latest", "install", "moltycel/moltrust-vet", "--dir", "skills"],
            capture_output=True, text=True, timeout=600,
        )
        installed = inst.returncode == 0
        res.add("clawhub install", installed, time.monotonic() - t,
                (inst.stderr or inst.stdout)[-200:] if not installed else "")

        # The first vet call is the skill's own behaviour: a free audit, no key.
        code, body, dt = _req(
            "GET", "/guard/skill/audit",
            params={"url": "https://github.com/MoltyCel/moltrust-vet", "profile": "claude_skill"},
        )
        ok = code == 200
        res.signup_without_human = True  # no signup at all on this path
        res.seconds_to_first_200 = round(time.monotonic() - t0, 3) if ok else None
        res.add("first vet call (no key)", ok, dt,
                f"score {body.get('audit', {}).get('score')}" if ok else str(code))

        if ok:
            code2, body2, dt2 = _req("GET", f"/guard/skill/vet-free/{body['skillHash']}")
            res.add("vet-free by hash", code2 == 200, dt2, str(code2))
    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
    return res


RUNNERS = {"K5": run_k5, "K2": run_k2, "K3": run_k3, "K1": run_k1}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--classes", default="K5,K2,K3,K1")
    ap.add_argument("--out", default="funnel-result.json")
    args = ap.parse_args()

    wanted = [c.strip().upper() for c in args.classes.split(",") if c.strip()]
    unknown = [c for c in wanted if c not in RUNNERS]
    if unknown:
        print(f"unknown classes: {unknown}; K4 is a separate script (it spends money)")
        return 2

    results = []
    for key in wanted:
        print(f"── {key} …", flush=True)
        r = RUNNERS[key]()
        results.append(r)
        for s in r.steps:
            print(f"   {'ok ' if s.ok else 'FAIL'} {s.name:<34} {s.seconds:>7.3f}s  {s.detail}")
        if r.error:
            print(f"   ERROR {r.error}")

    out = {
        "api_base": API,
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "classes": [asdict(r) for r in results],
        "passed": all(r.passed for r in results),
        "dids": [r.did for r in results if r.did],
    }
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nwrote {args.out} — passed={out['passed']}")
    print("DIDs to revoke: " + (", ".join(out["dids"]) or "none"))
    return 0 if out["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
